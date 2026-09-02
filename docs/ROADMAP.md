# Roadmap

Roadmap построен так, чтобы каждый milestone доказывал новую способность продукта, а не просто добавлял инфраструктуру.

---

## M0 — Architecture Foundation

**Статус: завершён.**

Цель: зафиксировать, что именно строится до начала реализации.

### Результаты

- продуктовая граница;
- базовая доменная модель;
- разделение `Item / SKU / Offer`;
- snapshots;
- `Demand -> NetRequirement -> ProcurementPlan`;
- граница planner / infrastructure;
- граница AI;
- первый набор инвариантов;
- Definition of Done для M1.

### Не входит

- API;
- БД;
- UI;
- LLM;
- реальные магазины.

---

## M1 — Baseline Procurement Kernel

**Статус: реализован.**

Цель: доказать, что ядро умеет корректно планировать закупку на фиксированных данных.

### Реализовать

Базовые типы:

```text
Money
Quantity
Item
SKU
Offer
InventoryLot
Demand
InventorySnapshot
MarketSnapshot
PlanningPolicy
PlanningProblem
ProcurementPlan
```

Пайплайн:

```text
Demand
   ↓
Inventory reconciliation
   ↓
Net requirements
   ↓
Package-aware candidate resolution
   ↓
Baseline planner
   ↓
Plan validation
   ↓
ProcurementPlan
```

### Baseline constraints

- hard budget;
- availability;
- целое количество упаковок;
- полное покрытие обязательных requirements;
- корректные преобразования совместимых units.

### Обязательные тесты

1. существующий запас полностью покрывает demand;
2. существующий запас частично покрывает demand;
3. округление до упаковки;
4. несколько упаковок одного SKU;
5. самая низкая цена за единицу не обязательно означает самую дешёвую покупку;
6. недоступный offer не выбирается;
7. hard budget соблюдается;
8. невозможный budget даёт `infeasible`;
9. несовместимые units отклоняются;
10. одинаковый input даёт одинаковый output;
11. итоговая стоимость равна сумме выбранных упаковок;
12. requirement coverage не превышает и не теряет фактические количества.

### Definition of Done

> При фиксированных `InventorySnapshot`, `MarketSnapshot`, `Demand` и `PlanningPolicy` система детерминированно строит валидный feasible procurement plan либо явно возвращает `infeasible` для отсутствующего market coverage или недостаточного hard budget.

M1 validator независимо сверяет feasible plan с исходными requirements, точным `MarketSnapshot`, package arithmetic, budget и projected leftovers. Snapshot-ы отклоняют дублирующиеся lot/offer IDs.

HTTP не является критерием готовности.

---

## M2 — Demand Compilation & Meals

**Статус: завершён.**

Цель: отделить способ появления потребности от самого planner.

### Реализовано

```text
DemandSource
DemandCompilation
ExplicitNeed / ExplicitNeedSource
MealDemandSource
Recipe / RecipeIngredient
MealRequest
```

Пайплайн:

```text
recipes / explicit needs
        ↓
normalized Demand[]
        ↓
existing M1 planner
```

Planner из M1 не должен знать, что часть demand появилась из рецептов.

M2 также фиксирует attributable `DemandContribution` и детерминированную recipe-scaling policy: repeating Decimal ratios округляются вверх до 12 знаков в базовой единице, независимо от ambient `decimal` context.

### Доказано

- точный пересчёт порций на `Decimal`;
- объединение одинаковых Items из разных recipes;
- один Item может одновременно требоваться нескольким `DemandSource`;
- conflicting Item identity и incompatible units отклоняются;
- source IDs уникальны в одной compilation;
- исходные contributions сохраняются отдельно от normalized demands;
- existing inventory применяется после aggregation;
- M1 `build_plan()` не изменён для поддержки recipes;
- старый M1 regression suite остаётся green.

### Definition of Done

> При фиксированном наборе `DemandSource` система детерминированно получает нормализованный `Demand[]` с сохраняемыми source contributions, а этот результат без специальных meal-paths проходит через существующий M1 procurement planner.

---

## M3 — Multi-objective Planning

**Статус: реализован.**

Цель: сделать результат бытово разумнее, чем просто минимальная цена, не меняя hard constraints M1.

### Реализовано

```text
Purchase Candidate Generation
          ├── M1: minimize purchase cost
          └── M3: minimize objective score
```

M3 objective:

```text
objective_score =
    purchase_cost
    + surplus_penalty
    + additional_store_penalty
```

Где:

- `purchase_cost` — реальные расходы пользователя;
- `surplus_penalty` — soft cost за избыточно купленное количество по item-specific rate;
- existing inventory не штрафуется как surplus: objective учитывает только новый overbuy текущего плана;
- `additional_store_penalty` — soft cost за каждого продавца после первого.

### Hard budget остаётся отдельным

```text
purchase_cost <= hard_budget
```

Objective score может быть выше budget: soft penalties не являются фактическими расходами и не уменьшают `budget_remaining`.

### Добавлено

- `MultiObjectivePolicy`;
- `SurplusPenaltyRate`;
- `ObjectiveBreakdown`;
- общий deterministic candidate generation layer;
- global cross-item seller optimization;
- budget-aware Pareto pruning внутри одинакового seller set;
- отдельный `validate_multi_objective_plan()`;
- объяснение trade-off относительно M1 cost-only baseline.

### Доказано

- store penalty может оправданно выбрать один магазин вместо нескольких более дешёвых;
- surplus penalty может выбрать более дорогую, но точнее подходящую упаковку;
- hard budget никогда не нарушается ради soft objective;
- один seller для нескольких Items считается одной поездкой;
- inventory-only plan не получает store penalty;
- market/demand ordering не меняет результат;
- ambient Decimal precision не меняет M3;
- нулевая M3 policy буквально воспроизводит M1 selection.

### Definition of Done

> Для фиксированных `PlanningProblem` и `MultiObjectivePolicy` M3 детерминированно выбирает budget-feasible procurement plan с минимальным objective score, сохраняет M1 hard constraints и предоставляет независимо проверяемый objective breakdown. При нулевых soft penalties результат совпадает с M1 baseline.

---

## M4 — Market Acquisition & Catalog Resolution

**Статус: реализован.**

Цель: заменить предположение «planner уже получил правильные Offer» явной, проверяемой границей между внешними рыночными данными и каноническим `MarketSnapshot`.

### Реализовано

```text
MarketProvider
    ↓
MarketAcquisitionBatch
    ↓
MarketObservation[]
    +
CatalogSnapshot
    ↓
resolve / compile
    ↓
MarketCompilation
    ↓
MarketSnapshot
```

Добавлены:

- `ProductIdentifier`;
- `ExternalListingKey`;
- `CatalogBinding`;
- `CatalogSnapshot`;
- `MarketObservation`;
- `MarketAcquisitionBatch`;
- `MarketProvider` boundary и deterministic `StaticMarketProvider`;
- `CatalogResolution`;
- `MarketCompilationPolicy`;
- `MarketObservationDisposition`;
- `OfferProvenance`;
- self-contained `MarketCompilation` basis (`CatalogSnapshot` + acquisition batches + policy);
- `compile_market_snapshot()`.

### Identity policy

Автоматическое SKU resolution разрешено только по:

1. exact external listing binding;
2. exact global/product identifier match.

```text
free-text similarity != SKU identity
package size alone != SKU identity
external listing != canonical SKU
```

Если binding и identifier указывают на разные SKU, либо observed package materially отличается от resolved SKU, observation получает `conflict`.

### Temporal policy

- timestamps market evidence обязаны быть timezone-aware;
- acquisition batch не может использоваться для snapshot, который исторически предшествует acquisition;
- по одной listing identity допускается только latest observation;
- older observations сохраняются как `superseded`;
- stale threshold задаётся явно;
- несколько latest observations на одном timestamp не разрешаются скрытым tie-break — это conflict;
- latest `unavailable` / `unresolved` / `conflict` не вызывает fallback на более старое evidence.

### Definition of Done

> Фиксированный набор attributable external observations, Catalog и compilation policy детерминированно дают один self-validating `MarketCompilation`; только resolved и temporally admissible observations становятся `Offer`, exact derivation basis и provenance сохраняются, а resulting `MarketSnapshot` проходит через существующий M1/M3 planner без специальной market-adapter логики.

---

## M5 — Real Market Provider Vertical Slice

**Статус: реализован.**

Цель: доказать первый provider-independent → real-retailer vertical slice без переноса retailer semantics в planner.

Первый источник — публичный **Globus Online demo catalog**.

```text
explicit Globus good URLs
        ↓
GlobusOnlineDemoProvider
        ↓
MarketObservation[]
        ↓
existing M4 admission
        ↓
MarketSnapshot
        ↓
existing M1 / M3 planner
```

### Зафиксированные границы

- provider не crawler и не search engine;
- SKU identity не выводится из product title;
- seller scope фиксирован как `globus-online-demo`;
- страница обязана явно подтверждать addressless/demo scope; DOM-позиция marker не считается частью контракта;
- price/availability читаются только из bounded product surface и не могут протечь из рекомендаций/cart/footer;
- скидочная current price требует явного контекста либо exact-consistent пары `current/regular + discount percent`; просто меньшая цена не считается evidence, а неоднозначные разные цены fail-closed;
- redirect не может изменить product identity или вывести запрос за `https://globus-online.kg`;
- live HTTP имеет timeout и response-size bound;
- CI использует injected transport и не зависит от retailer/network;
- только piece-priced packaged goods допускаются в M5; `сом/кг` отклоняется до появления variable-weight model.

### Реальный feedback в M4

Unavailable retailer page может не содержать цену. M5 поэтому расширяет acquisition evidence:

```text
MarketObservation(price=None, available=False)
```

Такое latest evidence не создаёт fake Offer и не разрешает fallback на старую доступную цену.

### Definition of Done

> Explicitly configured Globus demo product pages через заменяемый HTTP transport дают attributable observations; M4 допускает их в `MarketSnapshot`, а M1/M3 используют snapshot без retailer-specific ветвлений. Demo/address scope и unsupported variable-weight semantics не скрываются.

---

## M6 — Application Service + Minimal JSON/CLI/ASGI Boundary

**Статус: реализован.**

Цель: собрать M1–M5 в одну тонкую application operation без появления второго источника planning semantics.

```text
ApplicationPlanRequest
        ↓
catalog preflight (до сети)
        ↓
PlanApplicationService
        ↓
MarketProvider[] → M4 compilation
        ↓
M3 planner
        ↓
ApplicationPlanResult
```

### Реализовано

- typed `ApplicationPlanRequest`;
- typed inventory input;
- optional existing `MultiObjectivePolicy`;
- exact catalog preflight до acquisition;
- host-owned timezone-aware capture clock;
- self-validating `ApplicationPlanResult`;
- strict JSON parser/serializer;
- `PlanJsonApi` (`POST /plans`, `GET /health`);
- injected CLI adapter;
- dependency-free bounded ASGI adapter.

### Transport semantics

```text
feasible/infeasible -> 200
malformed JSON/HTTP -> 400
invalid application input -> 422
market acquisition failure -> 502
```

Planner logic, catalog resolution и retailer parsing не копируются в transport adapters.

### Definition of Done

> Один explicit application request через offline fixture и opt-in live Globus composition получает attributable M4 market snapshot и self-validating M3 procurement plan; invalid catalog input прекращается до external acquisition, а JSON/CLI/ASGI остаются transport-only adapters.

Persistence намеренно не добавляется в M6: `POST /plans` вычисляет result синхронно и не обещает `GET /plans/{id}` до появления настоящего требования хранить plan history.

---

## M7 — Plan Lifecycle & Persistence Boundary

**Статус: реализован.**

Цель: сохранить результат M6 как исторический planning record без повторного обращения к рынку и без привязки application core к PostgreSQL/SQLite.

```text
ApplicationPlanRequest
        ↓
M6 PlanApplicationService
        ↓
ApplicationPlanResult
        ↓
PlanLifecycleService
        ↓
PlanRecord
        ↓
PlanRepository
```

### Реализовано

- path-safe `PlanId`;
- immutable canonical JSON snapshots;
- `PlanRecord` с SHA-256 corruption digest;
- `PlanRepository` protocol;
- `InMemoryPlanRepository`;
- local-first `FilePlanRepository`;
- exclusive no-overwrite record identity;
- complete saved M4 market evidence basis;
- `PlanLifecycleService`;
- отдельный `PlanLifecycleJsonApi`;
- `POST /plans` → `201` persisted record;
- `GET /plans/{id}` → exact saved historical record;
- `GET /plans?limit=N` → bounded recent-history summary;
- ASGI query-string forwarding без framework dependency.

История не является командой на recomputation:

```text
GET saved plan
    != fetch current Globus
    != rerun planner
```

Если цена магазина изменилась после создания record, старый record продолжает содержать старый request, старый result и exact market evidence, на котором он был построен.

Filesystem adapter хранит dedicated JSON record per `PlanId`, никогда не перезаписывает существующую identity и публикует полностью записанный файл через same-directory hard-link. Digest предназначен для detection случайной corruption, а не как cryptographic signature доверенного автора.

### Definition of Done

> Completed M6 result можно сохранить, перечитать и перечислить как immutable historical snapshot; чтение истории не запускает market providers или planner, изменение текущего рынка не меняет старый record, а persistence остаётся заменяемым application adapter без database dependency.

---

## M8 — Household State & Learning

**Статус: реализован.**

Цель: система начинает учитывать историю конкретного дома через явные replayable facts, а не через скрытое состояние модели.

```text
PurchaseEvent
InventoryCorrection
ConsumptionObservation
        ↓
HouseholdEventRepository
        ↓
HouseholdHistory
        ├── HouseholdState
        └── ConsumptionEstimate
                  ↓
          RecurringNeedSource
                  ↓
          M2 demand compiler
```

### Реализовано

- path-safe `HouseholdEventId`;
- immutable `PurchaseEvent`;
- absolute `InventoryCorrection`;
- interval-based `ConsumptionObservation`;
- separate effective/recorded time semantics;
- `HouseholdHistory`;
- deterministic inventory replay;
- `HouseholdState.inventory_snapshot()`;
- `ConsumptionEstimate`;
- weighted daily-rate estimation from exact rational evidence;
- sample count / observed duration / min-max spread;
- exact total-consumed + observed-microseconds basis retained;
- `RecurringNeedSource` compatible with existing M2 compiler;
- `HouseholdEventRepository` protocol;
- in-memory repository;
- local-first file repository with no-overwrite publication and corruption digest;
- `HouseholdLearningService`;
- executable M8 offline vertical slice through the existing planner.

### Important semantics

```text
ProcurementPlan != PurchaseEvent
correction != history rewrite
estimate != source fact
rounded display rate != recurring arithmetic basis
```

A late-recorded fact does not appear in a historical projection before its `recorded_at`. Overlapping consumption intervals are rejected to prevent double counting. An absolute inventory correction strictly inside a consumption interval is also rejected because the split of consumption around that count is unknowable from the supplied evidence.

`ConsumptionEstimate.uncertainty` is the observed daily max-minus-min spread. It is descriptive evidence, not a statistical confidence interval. Better forecasting can later replace the estimator without deleting raw household events.

Recurring demand is derived directly from exact total consumption and exact observation duration, then rounded **upward only at the final demand boundary**. This prevents a rounded daily display rate from understating future demand.

### Definition of Done

> Household events survive restart, replay to a deterministic current inventory, produce transparent consumption estimates and generate attributable recurring demand through the existing M2/M3 planning system; every derived value remains reproducible from explicit stored facts and ambiguous history fails closed.

---

## M9 — Household Replenishment Workflow

**Статус: реализован.**

Цель: собрать M2/M6/M7/M8 в первую household-aware planning operation без новой planning semantics.

```text
HouseholdHistory
+ horizon
+ explicit needs
+ budget
        ↓
HouseholdReplenishmentPreparation
        ↓
HouseholdState + estimates
        ↓
RecurringNeedSource / ExplicitNeedSource
        ↓
DemandCompilation
        ↓
ApplicationPlanRequest
        ↓
PlanLifecycleService
        ↓
persisted PlanRecord
```

### Реализовано

- `HouseholdReplenishmentRequest`;
- deterministic canonical explicit-needs ordering;
- one-history / one-`as_of` preparation snapshot;
- exact reuse of M8 state and learning;
- recurring + explicit demand composition through M2;
- inventory projection only for demanded Item IDs;
- catalog identity validation before network acquisition;
- learned missing-catalog Item fail-closed;
- self-validating `HouseholdReplenishmentPreparation`;
- self-validating `HouseholdReplenishmentResult` linked to exact M7 stored request;
- `HouseholdReplenishmentService`;
- strict `HouseholdReplenishmentJsonApi` on durable `/plans` surface;
- existing M7 history GET routes preserved;
- existing generic ASGI adapter reused unchanged;
- executable offline M9 vertical slice.

### Important semantics

```text
Plan != PurchaseEvent
household snapshot != live mutable history during one run
missing catalog need != silently ignored need
unrelated household inventory != market catalog requirement
```

M9 не создаёт второй planner. Он materialize-ит household-derived inputs в существующий `ApplicationPlanRequest`, после чего M6/M3 остаются authoritative для market/planning semantics, а M7 — для plan persistence.

### Definition of Done

> Один household snapshot, planning horizon, budget и optional explicit needs можно детерминированно собрать в attributable replenishment demand, получить и сохранить existing-stack procurement plan, причём malformed/unsupported household needs прекращаются до external market acquisition, а recommendation не превращается автоматически в факт покупки.

---

## M10 — Natural Language Interface

Только после доказанного M9 workflow появляется AI-интерпретация пользовательских запросов.

Пример:

```text
«Спланируй пополнение на неделю, бюджет 3000 сом,
и добавь ещё литр масла»
            ↓
candidate HouseholdReplenishmentRequest
            ↓
validation / clarification
            ↓
existing deterministic M9 workflow
```

AI не получает право:

- выдумывать цены;
- создавать `PurchaseEvent` из recommendation;
- считать household consumption вместо M8 evidence model;
- игнорировать hard constraints;
- считать предложение магазина актуальным без market evidence.

---

## Дальнейшие направления

После доказанного food/replenishment core:

### Household consumables

```text
soap
detergent
toothpaste
toilet paper
cleaning supplies
```

### Better forecasting

- сезонность;
- будни / выходные;
- household events;
- uncertainty-aware replenishment.

### Better optimization

- CP-SAT/MIP;
- time cost;
- delivery fees;
- minimum order;
- promotions;
- substitution policies.

### Durable acquisition

Одежда, электроника и другие долговечные покупки рассматриваются как отдельная planning model, а не как искусственное продолжение расходуемых запасов.

---

## Что не нужно добавлять «на всякий случай»

Пока конкретный milestone этого не требует:

- PostgreSQL;
- Redis;
- Celery;
- Kafka;
- Kubernetes;
- микросервисы;
- vector database;
- agent framework;
- LLM orchestration;
- сложную frontend-архитектуру.

Правило проекта:

> Инфраструктура появляется только тогда, когда уже существующая способность ядра создаёт конкретное требование к инфраструктуре.
