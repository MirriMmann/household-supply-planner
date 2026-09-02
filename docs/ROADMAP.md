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

## M6 — Application Boundary

Цель: добавить тонкую прикладную поверхность вокруг уже доказанных M1–M5 контрактов.

Возможный первый API:

```text
POST /plans
GET  /plans/{id}
```

API должен только:

- разобрать transport schema;
- вызвать существующие demand/market/planning boundaries;
- сериализовать результат.

Он не должен переносить planner logic, catalog resolution или market evidence policy в HTTP handlers.

На этом этапе отдельно решается, нужна ли persistence layer для plans/catalog/observations и какая именно.

---

## M7 — Household State & Learning

Цель: система начинает учитывать историю конкретного дома.

Сначала события:

```text
PurchaseEvent
InventoryCorrection
ConsumptionObservation
```

Затем производные оценки:

```text
ConsumptionEstimate
```

Пример:

```text
milk:
    estimated daily rate = ...
    sample count = ...
    uncertainty = ...
```

Recurring demand создаётся из оценок через отдельный `RecurringNeedSource`.

Никакой скрытой «памяти модели».

---

## M8 — Natural Language Interface

Только здесь появляется AI-интерпретация пользовательских запросов.

Пример:

```text
«Мне нужно прожить одному неделю на 3000 сом,
рыбу не люблю, готовить хочу максимум раз в два дня»
            ↓
candidate typed PlanRequest
            ↓
validation / clarification
            ↓
existing deterministic system
```

AI не получает право:

- выдумывать цены;
- считать итоговую стоимость вместо planner;
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
