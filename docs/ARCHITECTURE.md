# Архитектура

## 1. Назначение документа

Этот документ фиксирует базовую архитектуру Household Supply Planner до появления инфраструктурных решений.

Главная цель — сохранить доменную модель независимой от API, базы данных, конкретных магазинов и AI-провайдеров.

Архитектура строится вокруг одного вопроса:

> Что домохозяйству потребуется в заданном горизонте, что уже имеется, что доступно на рынке и какой допустимый план снабжения лучше всего удовлетворяет ограничениям?

---

## 2. Основной поток

```text
                    PlanRequest
                         │
                         ▼
                HouseholdSnapshot
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
        Demand Sources         InventorySnapshot
              │                     │
              └──────────┬──────────┘
                         ▼
                Demand Compilation
                         │
                         ▼
              Inventory Reconciliation
                         │
                         ▼
                  Net Requirements
                         │
                         │
                  MarketSnapshot
                         │
                         ▼
                Candidate Resolution
                         │
                         ▼
                  PlanningProblem
                         │
                         ▼
                     Planner
                         │
                         ▼
                ProcurementPlan
                         │
                 ┌───────┴────────┐
                 ▼                ▼
             Validation        Explain
                         │
                         ▼
                       Result
```

Позже фактические покупки и изменения запасов могут образовать отдельный feedback loop:

```text
Plan
 ↓
Purchase Events
 ↓
Inventory / Consumption Observations
 ↓
History
 ↓
Consumption Estimate
 ↓
Future Demand
```

История наблюдений должна сохраняться отдельно от производных оценок.

---

## 3. Основные доменные сущности

### 3.1 Item

`Item` описывает семантическую сущность, которая может быть нужна домохозяйству.

Примеры:

```text
rice
milk
chicken_breast
dish_soap
```

`Item` не является конкретным товаром магазина.

Минимально:

```text
Item
- id
- canonical_name
- category
- aliases
- canonical_unit
```

---

### 3.2 SKU

`SKU` — конкретно покупаемый продукт и его упаковка.

Пример:

```text
Item:
    milk

SKU:
    brand: Example Dairy
    name: Milk 2.5%
    package_quantity: 930
    package_unit: ml
```

Минимально:

```text
SKU
- id
- item_id
- brand
- name
- package_quantity
- package_unit
- external_identifiers?
```

Количество упаковок в плане всегда целое.

---

### 3.3 Offer

`Offer` — наблюдаемое предложение продавца.

Минимально:

```text
Offer
- id
- sku_id
- seller_id
- price
- currency
- availability
- observed_at
- source
- confidence?
```

Инвариант:

```text
price без SKU + seller + observed_at + source
не является полноценным рыночным фактом
```

---

### 3.4 InventoryLot

`InventoryLot` описывает имеющийся дома запас.

Минимально:

```text
InventoryLot
- id
- item_id / sku_id
- quantity
- unit
- acquired_at?
- opened_at?
- expires_at?
```

Первый milestone может не моделировать сроки годности, но структура не должна запрещать их добавление.

---

### 3.5 Demand

`Demand` — нормализованная будущая потребность.

Минимально:

```text
Demand
- item_id
- quantity
- unit
- needed_by?
- source
- priority
```

Важно:

```text
Demand != Purchase
```

Потребность сначала покрывается существующими запасами и только затем превращается в `NetRequirement`.

---

### 3.6 DemandSource

`DemandSource` производит demand, но не выполняет закупку.

Предусмотренные источники:

```text
MealDemandSource
ExplicitNeedSource
RecurringNeedSource
StockThresholdSource
FutureEventSource
```

M2 реализует `ExplicitNeedSource` и `MealDemandSource`. Оба источника производят только demand contributions и не получают доступ к inventory, market, budget или planner state.

`DemandCompilation` хранит две поверхности:

```text
contributions[]  # DemandContribution: attributable source inputs
demands[]        # нормализованный aggregate по Item
```

`DemandContribution` отдельно фиксирует `source_id`, `contribution_id`, Item и Quantity. Compiler проверяет, что contribution действительно принадлежит тому `DemandSource`, который её вернул, и запрещает повтор одного contribution ID внутри source. Поэтому provenance не сводится к произвольной строке внутри `Demand` и не приходится кодировать в арифметику planner-а.

`compile_demand_sources()` также проверяет уникальность source IDs, совместимость единиц и непротиворечивую Item identity, после чего детерминированно сортирует нормализованный demand по `item.id`.

Рецепты входят через `MealDemandSource`, не изменяя planner.

---


### 3.6.1 Recipe / MealRequest

`Recipe` описывает количество ингредиентов для базового числа порций:

```text
Recipe
- id
- name
- servings
- ingredients[]

RecipeIngredient
- item
- quantity
```

`MealRequest` связывает рецепт с фактически нужным количеством порций. Входные значения хранятся как точные конечные `Decimal`, а масштабирование выполняется через точное рациональное отношение, не используя ambient `decimal.getcontext()`:

```text
exact ratio = requested_servings / recipe.servings
scaled ingredient = ingredient.quantity * exact ratio
canonical result = round upward to 12 decimal places in the base unit
```

Некоторые отношения, например `1 / 3`, не имеют конечного Decimal-представления. Для них M2 фиксирует каноническую resolution policy: 12 знаков после запятой в базовой единице (`g`, `ml`, `piece`) с округлением вверх. Это делает результат воспроизводимым и не занижает demand из-за округления.

`float` для servings намеренно не принимается: компиляция demand не должна вносить двоичную погрешность до planner-а.

```text
Recipe != MealRequest
MealRequest != Demand
RecipeIngredient != SKU
```

Рецепт не знает ни о магазине, ни о цене, ни о домашних запасах.

### 3.7 HouseholdSnapshot

Снимок релевантного состояния домохозяйства на момент планирования.

Он может включать:

```text
- household size
- planning horizon
- preferences
- hard constraints
- relevant schedule/context
```

Первый milestone использует только необходимые поля.

---

### 3.8 InventorySnapshot

Иммутабельное представление запасов, на основании которого строился план.

План должен быть воспроизводимым относительно точного snapshot.

---

### 3.9 MarketSnapshot

Иммутабельный набор рыночных предложений на момент планирования.

```text
MarketSnapshot
- captured_at
- offers[]
```

Изменившаяся позже цена не переписывает историю старого плана.

---

### 3.9.1 External market evidence

M4 разделяет наблюдение внешнего источника и допущенный planner-ом Offer:

```text
MarketObservation != Offer
```

`MarketObservation` фиксирует материал, который сообщил конкретный provider:

```text
MarketObservation
- id
- provider_id
- seller_id
- external_product_id
- price
- observed_at
- available
- product_identifier?
- package_quantity?
- name / brand
- source_ref
```

`name` и `brand` являются inspectable evidence, но не идентичностью. Core не делает fuzzy product matching по строкам.

`MarketAcquisitionBatch` добавляет acquisition-time и provider attribution. Observation не может утверждать другого provider, чем batch, и `observed_at` не может находиться после `acquired_at`. Market timestamps обязаны быть timezone-aware.

```text
provider output != MarketSnapshot
MarketObservation != accepted market fact
```

---

### 3.9.2 CatalogSnapshot / Catalog Resolution

`CatalogSnapshot` содержит канонические SKU и явные bindings внешних listing identities:

```text
CatalogSnapshot
- skus[]
- bindings[]

ExternalListingKey
- provider_id
- seller_id
- external_product_id

CatalogBinding
- listing_key
- sku_id
- source
```

`SKU` может содержать exact `ProductIdentifier`:

```text
ProductIdentifier
- scheme   # gtin / ean13 / upc / ...
- value
```

M4 разрешает automatic resolution только по двум основаниям:

```text
exact CatalogBinding
OR
exact ProductIdentifier match
```

Если оба основания присутствуют и подтверждают один SKU, resolution имеет статус `corroborated`. Если они указывают на разные SKU, это `conflict`.

Если resolved SKU имеет известный identifier того же namespace, а observation сообщает другой identifier, binding не может замаскировать противоречие. Аналогично observed package quantity, если она передана, должна совпасть с canonical SKU package после unit normalization.

Не используется правило вида:

```text
"похожее название" -> SKU
"тот же бренд" -> SKU
"тот же размер" -> SKU
```

Без exact identity evidence результат остаётся `unresolved`.

---

### 3.9.3 MarketCompilation

`compile_market_snapshot()` превращает acquisition evidence в planner-facing snapshot:

```text
CatalogSnapshot
      +
MarketAcquisitionBatch[]
      +
MarketCompilationPolicy
      ↓
MarketCompilation
      ├── dispositions[]
      └── MarketSnapshot
```

Для одной `ExternalListingKey` используется только latest observation. Более старые события не удаляются из результата compilation, а получают disposition `superseded`.

Если две записи являются одновременно latest и имеют одинаковый `observed_at`, compiler не выбирает одну по input order: обе получают `conflict`. Это защищает snapshot от скрытого last-write-wins при противоречивом evidence.

Latest evidence является авторитетным по времени даже когда оно неудобно: `unavailable`, `unresolved` или `conflict` не приводят к fallback на более старую доступную/разрешённую запись.

`MarketCompilationPolicy.max_observation_age` позволяет явно отбрасывать stale evidence. Batch, полученный позже `captured_at` требуемого snapshot, вообще не может использоваться для ретроспективного «знания задним числом».

Допустимые dispositions:

```text
accepted
unavailable
superseded
stale
unresolved
conflict
```

Только `accepted` observation создаёт `Offer`. Такой Offer получает `OfferProvenance`:

```text
OfferProvenance
- observation_id
- ExternalListingKey
- source_ref
```

`MarketCompilation` хранит точную basis своей деривации:

```text
CatalogSnapshot
MarketAcquisitionBatch[]
MarketCompilationPolicy
```

и при создании повторно выводит ожидаемые dispositions и `MarketSnapshot`. Поэтому compilation record нельзя вручную собрать с поддельным `RESOLVED`, неверным latest selection, изменённым Offer ID/source_ref или другим planner-facing фактом, который не следует из сохранённой basis. Это делает `MarketCompilation` self-contained proof record, а не только контейнером результата.

Unavailable latest observation тоже является валидным market evidence. Если retailer сохраняет цену, она может быть представлена как `Offer(available=False)`. Если цена отсутствует, observation получает `unavailable` disposition и не создаёт Offer. В обоих случаях fallback к старой available цене запрещён.

---

### 3.9.4 Real retailer adapter: Globus Online demo

M5 добавляет первый конкретный adapter поверх M4, не меняя planner contract:

```text
Globus product page
      ↓
GlobusOnlineDemoProvider
      ↓
MarketAcquisitionBatch
      ↓
M4
      ↓
MarketSnapshot
```

`GlobusOnlineDemoProvider` принимает только заранее перечисленные canonical `/good/<id>` URLs. Он не выполняет fuzzy search, category crawling или автоматический catalog binding. Product ID берётся из exact URL, а переход к canonical SKU остаётся ответственностью `CatalogSnapshot`.

Provider scope намеренно фиксирован:

```text
provider_id = globus-online-demo
seller_id   = globus-online-demo
```

Публичная страница должна давать явное evidence addressless/demo scope: прямой marker `Это демо-каталог` либо официальный no-address state `Укажите адрес доставки` вместе с Globus cart surface. DOM-позиция marker не является частью контракта. Поэтому наблюдение нельзя случайно представить как address-specific store evidence. Address-scoped integration в будущем должна иметь отдельную attributable seller/provider boundary.

Scope evidence и product facts имеют разные поверхности. Demo/addressless evidence может находиться в header/footer всей страницы, но цена, availability и `В корзину` извлекаются только из bounded product surface после целевого `<h1>` и до структурной границы (`hr`, следующий heading, `aside` или `footer`). Поэтому market facts соседней рекомендации или cart drawer не могут быть присвоены целевому listing.

Если product surface публикует скидочную форму `CURRENT сом вместо обычной цены OLD сом`, current price извлекается из явного discount context. Реальный Globus raw DOM может дублировать current/regular price и не сохранять эту человеческую фразу как один непрерывный текст. Поэтому разрешён второй bounded discount contract: на product surface должны существовать ровно две distinct KGS-цены и ровно один discount percent; меньшая цена принимается как current только если exact rational arithmetic подтверждает соответствие displayed percentage с допуском в один процентный пункт на UI-округление. Сам факт `min(price)` никогда не является достаточным evidence. Все остальные multiple-price случаи считаются ambiguous и fail-closed.

HTTP mechanism ограничен:

- только `https://globus-online.kg`;
- только canonical product-page URLs;
- redirect повторно валидируется и не может сменить product ID;
- timeout обязателен;
- response body имеет max-size bound;
- принимается только HTML/XHTML;
- inline script/style/template text не участвует в price/availability parsing;
- product facts не читаются из content ниже structural product boundary;
- canonical URL path должен точно соответствовать `/<locale>/good/<product-id>`;
- ambiguous multiple prices fail-closed.

M5 поддерживает только piece-priced packaged listings. `сом/кг` отклоняется как unsupported semantics вместо ложного преобразования в условную упаковку 1 kg.

Реальный retailer также потребовал уточнить M4 observation semantics:

```text
available=True  -> price обязателен
available=False -> price может отсутствовать
```

Если latest resolved observation имеет `available=False` и `price=None`, она получает disposition `unavailable`, не создаёт `Offer` и всё равно supersede-ит старые observations. Поэтому отсутствие новой цены не разрешает resurrect старый покупаемый Offer.

### 3.10 PlanningPolicy

`PlanningPolicy` содержит hard constraints базовой задачи. В M1–M3 это прежде всего реальный бюджет:

```text
PlanningPolicy
- budget: Money
```

Базовая семантика:

```text
purchase_cost <= budget
```

Soft preferences намеренно не маскируются под hard budget.

### 3.10.1 MultiObjectivePolicy

M3 вводит отдельную soft-scoring policy:

```text
MultiObjectivePolicy
- additional_store_penalty: Money
- surplus_penalties[]: SurplusPenaltyRate

SurplusPenaltyRate
- item_id
- cost_per_base_unit
```

`cost_per_base_unit` относится к нормализованной базовой единице requirement: `g` для массы, `ml` для объёма и `piece` для count. Например `0.05 KGS/g` означает виртуальную стоимость 20 KGS для 400 g избыточно купленного риса.

Surplus objective учитывает только **over-purchase**, созданный текущим решением:

```text
purchased - net_required
```

Неиспользованный inventory, который уже находился дома до планирования, остаётся в `projected_leftovers`, но не штрафуется: текущий planner не способен изменить прошлую покупку.

Эти значения — **не реальные расходы**, а soft objective terms. Поэтому:

```text
hard budget != objective score
```

План с purchase cost 160 KGS и objective score 660 KGS остаётся budget-feasible при hard budget 160 KGS, если дополнительные 500 KGS являются только store penalty.

---

### 3.11 PlanningProblem

`PlanningProblem` — полностью собранная задача, которую получает planner.

Он не должен сам ходить в сеть, читать БД или спрашивать LLM.

Концептуально:

```text
PlanningProblem
- household_snapshot
- inventory_snapshot
- market_snapshot
- requirements
- planning_policy
```

---

### 3.12 ProcurementPlan

`ProcurementPlan` — результат planner.

```text
ProcurementPlan
- status
- purchases[]
- requirement_coverage[]
- projected_leftovers[]
- total_cost
- budget_remaining
- minimum_required_cost?
- objective_breakdown?
- warnings[]
- explanation[]
```

`total_cost` всегда означает реальные расходы. В M3 `minimum_required_cost` сохраняет стоимость M1 cost-only baseline, даже если multi-objective plan осознанно выбирает более дорогую корзину.

`ObjectiveBreakdown` отдельно показывает:

```text
ObjectiveBreakdown
- purchase_cost
- surplus_penalty
- additional_store_penalty
- total_score
- selected_sellers[]
- additional_store_count
```

Поэтому consumer никогда не обязан угадывать, является ли число фактической ценой или виртуальной оценкой предпочтения.

`status` как минимум различает:

```text
feasible
infeasible
```

---

## 4. Главные инварианты

### I1. Item, SKU и Offer не смешиваются

```text
Item != SKU != Offer
```

### I2. Demand не означает необходимость покупки

```text
Demand
  - Inventory Coverage
  = Net Requirement
```

### I3. Planner работает только с переданным PlanningProblem

Никакого скрытого чтения:

- базы данных;
- сети;
- файлов;
- LLM;
- глобального mutable state.

### I4. Фиксированный вход воспроизводим

Одинаковый `PlanningProblem` и версия planner должны приводить к одинаковому результату.

### I5. Бюджет — hard constraint, если политика не говорит обратного

Planner не имеет права превышать hard budget ради «лучшего» плана.

### I6. Упаковки дискретны

Если SKU продаётся упаковками, planner покупает целое число упаковок.

### I7. Infeasible не маскируется

Отсутствие допустимого решения — валидный исход, а не повод нарушить ограничения.

### I8. Money не хранится во float

Для денежных значений в Python используется `Decimal` или эквивалентное точное представление.

### I9. Единицы измерения нормализуются явно

Нельзя сравнивать `kg`, `g`, `L`, `ml`, `piece` без явной совместимости и преобразования.

### I10. Инфраструктура не владеет доменными смыслами

FastAPI schema, SQLAlchemy model или JSON магазина не являются канонической доменной моделью.

### I11. Soft objective не изменяет hard budget

```text
objective_score > budget
```

само по себе не делает план infeasible. Проверяется только реальный `purchase_cost`.

### I12. M3 является conservative extension M1

```text
MultiObjectivePolicy.zero(currency)
        => exact M1 plan
```

Нулевая policy должна сохранить offers, pack counts, real cost и projected leftovers M1, а не просто найти другой план той же цены.

### I13. Objective accounting проверяем отдельно

`ObjectiveBreakdown` не считается доверенным только потому, что его создал planner. `validate_multi_objective_plan()` независимо пересчитывает seller set, surplus penalties, store penalty и minimum M1 cost из исходного `PlanningProblem`.

### I14. Snapshot и policy действительно иммутабельны

Публичные domain records захватывают входные collection values как immutable tuples. Переданный caller-ом `list` не остаётся скрытой mutable ссылкой внутри `InventorySnapshot`, `MarketSnapshot`, `PlanningProblem`, `ProcurementPlan` или `MultiObjectivePolicy`.

```text
external list mutation != snapshot mutation
external list mutation != policy mutation
```

Это необходимо для гарантии воспроизводимости: фиксированные problem/policy должны оставаться фиксированными после создания объектов.

### I15. External observation не является Offer

```text
MarketObservation != Offer
```

Provider может сообщить цену, но admission в `MarketSnapshot` требует catalog resolution и temporal checks.

### I16. Свободный текст не устанавливает SKU identity

```text
name similarity != product identity
brand similarity != product identity
package similarity != product identity
```

Автоматическое разрешение требует exact binding или exact identifier.

### I17. Противоречивое identity evidence не угадывается

Binding/identifier/package conflict приводит к `conflict`, а не к выбору «наиболее вероятного» SKU.

### I18. Market evidence имеет явное время знания

`observed_at`, `acquired_at` и `captured_at` timezone-aware. Данные, acquired после snapshot time, не могут задним числом считаться доступными этому snapshot.

### I19. Latest не означает silent last-write-wins

Старые observations помечаются `superseded`; competing latest observations с одним timestamp являются конфликтом и не разрешаются порядком входного списка.

### I20. Planner не знает механизм acquisition

M1/M3 получают только canonical `MarketSnapshot`. API, scraper, JSON feed или retailer SDK не меняют planning semantics.

### I21. Demo market scope не маскируется под реальный магазин

`GlobusOnlineDemoProvider` имеет фиксированные provider/seller identity и принимает только страницы с явным demo marker. Address-specific availability требует отдельного attributable scope.

### I22. Unavailable без цены не создаёт выдуманный Offer

```text
latest available=False + price=None
        ↓
unavailable disposition
        ↓
no Offer
        ↓
no fallback to older price
```

### I23. Unsupported retailer semantics fail closed

M5 не преобразует `сом/кг` в фиктивную упаковку 1 kg. Пока variable-weight purchase semantics не описаны в planning core, такой listing отклоняется adapter-ом.

---

### I24. Historical plan не recompute-ится при чтении

Сохранённый `PlanRecord` является snapshot. Retrieval не запускает market provider и planner.

### I25. Persisted PlanId не перезаписывается

Repository обязан fail closed при попытке сохранить уже существующую identity. Новая цена или новый run создают новый record.

### I26. Persistence failure не является market failure

Storage exception не маскируется как `502 market_unavailable`, а programming/runtime exception не маскируется как declared storage condition.

### I27. Household fact != derived state

`PurchaseEvent`, `InventoryCorrection` и `ConsumptionObservation` являются source facts. `HouseholdState`, `ConsumptionEstimate` и recurring demand всегда выводятся из них и могут быть пересчитаны.

```text
ProcurementPlan != PurchaseEvent
estimate != observation
projection != mutable source of truth
```

### I28. Historical projection ограничена временем знания

Событие участвует в `HouseholdState(as_of=T)` только если и его effective time, и `recorded_at` не позже `T`. Late-recorded fact не переписывает то, что система могла знать до его записи.

### I29. Неоднозначный household replay fail closed

Перекрывающиеся `ConsumptionObservation` одного Item запрещены. `InventoryCorrection`, находящаяся строго внутри consumption interval, также запрещает projection: без более точных данных невозможно определить, какая часть расхода произошла до absolute count и какая после.

### I30. Rounded estimate не владеет recurring arithmetic

Display daily-rate округляется детерминированно, но `ConsumptionEstimate` сохраняет exact total consumed и exact observed duration. `RecurringNeedSource` вычисляет horizon из этой evidence basis и округляет вверх только финальный demand.

### I31. Household event identity append-only

`HouseholdEventId` не перезаписывается. File repository публикует complete JSON record через no-overwrite operation и проверяет corruption digest при чтении. Digest не является подписью или authentication mechanism.

---

## 5. Модули

Актуальная логическая структура после M8:

```text
src/household_supply/
├── domain/
│   ├── money.py
│   ├── quantity.py
│   ├── items.py          # Item / SKU / ProductIdentifier
│   ├── catalog.py        # CatalogSnapshot / explicit listing bindings
│   ├── acquisition.py    # external MarketObservation / batches
│   ├── inventory.py
│   ├── demand.py
│   ├── recipes.py
│   ├── market.py         # canonical Offer / MarketSnapshot / provenance
│   ├── objectives.py
│   └── plan.py
│
├── demand/
│   ├── sources.py
│   └── compile.py
│
├── market/
│   ├── provider.py       # MarketProvider acquisition boundary
│   ├── resolve.py        # exact catalog resolution
│   ├── compile.py        # observations -> MarketCompilation/Snapshot
│   └── providers/
│       └── globus_online.py  # bounded M5 real-retailer adapter
│
├── planning/
│   ├── compile.py       # Demand + inventory -> net requirements
│   ├── candidates.py    # package-aware purchase candidates
│   ├── baseline.py      # M1 cost-only selection
│   ├── objective.py     # M3 score accounting
│   ├── multi_objective.py
│   ├── assemble.py      # common ProcurementPlan assembly
│   └── validate.py
│
├── household/
│   ├── events.py        # M8 source facts + effective/recorded time
│   ├── history.py       # immutable fact collection
│   ├── projection.py    # facts -> HouseholdState / InventorySnapshot
│   ├── learning.py      # exact evidence -> transparent estimate
│   ├── recurring.py     # estimate -> M2 DemandSource
│   ├── persistence.py   # append-only event repository adapters
│   └── service.py       # thin household orchestration boundary
│
└── application/
    ├── models.py        # typed request/result + catalog preflight
    ├── service.py       # M6 orchestration boundary
    ├── json_api.py      # strict compute-only JSON contract
    ├── lifecycle.py     # M7 durable plan lifecycle
    ├── lifecycle_api.py # M7 history HTTP semantics
    ├── persistence.py   # M7 PlanRepository adapters
    ├── cli.py           # injected CLI adapter
    └── asgi.py          # dependency-free bounded HTTP adapter
```

M5 сохраняет одностороннюю зависимость:

```text
external provider mechanism
        ↓
MarketObservation
        ↓
market resolution / compilation
        ↓
MarketSnapshot
        ↓
planning core
```

Planning core не импортирует acquisition provider implementations.

Ключевая граница M3:

```text
requirements
    ↓
candidate generation
    ├── baseline selection
    └── multi-objective selection
```

Candidate generation не знает, какой objective потом выберет planner. Благодаря этому будущий CP-SAT/MIP engine может заменить механизм перебора, не меняя `Item`, `Offer`, `Demand`, `ProcurementPlan` или семантику objective policy.

---

## 6. Planner

### M1 Baseline Planner

M1 выбирает для каждого Item package-aware candidate по ключу:

```text
minimum purchase cost
then minimum surplus
then minimum pack count
then deterministic offer/count tie-break
```

Hard constraints:

```text
requirements covered
hard budget respected
offers available
packages integer
quantities compatible
```

### Общий Candidate Layer

M3 выносит перебор допустимых package combinations в `planning/candidates.py`. И M1, и M3 работают с одной семантикой `ItemCandidate`:

```text
ItemCandidate
- purchases[]
- purchased quantity
- purchase cost
- over-purchase surplus
- seller set
- pack count
- deterministic count signature
```

Это предотвращает появление двух независимых трактовок упаковок.

### M3 Multi-objective Planner

M3 решает уже глобальную задачу по всем Items, потому что store penalty связывает независимые товарные решения:

```text
objective_score =
    purchase_cost
    + Σ(item surplus × configured rate)
    + max(unique_sellers - 1, 0) × additional_store_penalty
```

При этом:

```text
purchase_cost <= hard budget
```

остаётся отдельным hard constraint.

Planner сохраняет budget-relevant Pareto candidates внутри одного seller set и затем выполняет bounded global search. Текущие exhaustive limits являются осознанным baseline-механизмом, а не обещанием масштабируемости. При росте реального рынка именно этот механизм должен быть заменён CP-SAT/MIP solver-ом.

### Explainability

M3 не ограничивается финальным score. План хранит objective breakdown и сравнивает решение с M1 baseline. Например:

```text
cost-only baseline: 180 KGS
M3 purchase cost:   210 KGS
baseline M3 score:  300 KGS
selected M3 score:  210 KGS

reason:
30 KGS дополнительной реальной стоимости
устраняют surplus и вторую поездку
```

Это позволяет UI позднее объяснять trade-off без повторного запуска optimizer-а.

---


## 7. Application boundary (M6)

M6 добавляет отдельный application layer поверх уже замороженных domain/market/planning контрактов. Он не является новым planner-ом.

```text
transport payload
      ↓
ApplicationPlanRequest
      ↓
catalog preflight
      ↓
PlanApplicationService
      ↓
MarketProvider[]
      ↓
MarketCompilation
      ↓
PlanningProblem
      ↓
M3
      ↓
ApplicationPlanResult
```

### Request contract

Первый M6 request surface намеренно узкий:

```text
ApplicationPlanRequest
- demands[]: item_id + Quantity
- inventory[]: lot_id + item_id + Quantity
- budget: Money
- objective_policy?: MultiObjectivePolicy
```

Explicit request не переносит `Item`, `SKU`, `Offer` или retailer identity через transport. `item_id` разрешается только против точного configured `CatalogSnapshot`.

До любого market acquisition выполняется preflight:

```text
known item identity
compatible quantity dimension
unique demand item IDs
unique inventory lot IDs
objective currency == budget currency
surplus objective references active demand items
```

Поэтому malformed/unknown input не создаёт внешний network effect.

### Application clock

Provider не определяет application present. После завершения acquisition service берёт timezone-aware host clock и передаёт его как `captured_at` в M4.

```text
provider acquired_at != application captured_at
```

Это необходимо для честной freshness policy: старый batch не может сделать собственное evidence «свежим», назначив snapshot time равным самому себе. Clock inject-able, поэтому tests остаются deterministic.

### Result contract

`ApplicationPlanResult` хранит exact request, `MarketCompilation`, derived `PlanningProblem`, effective objective policy и `ProcurementPlan`. Record повторно выводит ожидаемый problem из request + compilation и запускает M1/M3 validators.

```text
ApplicationPlanResult != mutable session
ApplicationPlanResult != persistence record
```

### JSON / CLI / ASGI

M6 transport adapters тонкие и используют один JSON schema.

`PlanJsonApi`:

```text
GET  /health
POST /plans
```

`run_plan_cli()` читает тот же payload из stdin/file и получает already-composed `PlanApplicationService` от host.

`PlanAsgiApp` — dependency-free ASGI adapter. Он владеет только HTTP mechanism: method/path, JSON content type, UTF-8 decoding, body-size limit и response serialization. ASGI adapter не импортирует Globus provider и не вычисляет planner decisions.

Transport status semantics:

```text
200 feasible/infeasible planning result
400 malformed HTTP/JSON
422 invalid application request
502 market acquisition/compilation unavailable
```

`infeasible` не является HTTP error: это валидный ответ optimizer-а.

---

## 8. AI boundary

AI не является источником канонической арифметики или фактом рынка.

Допустимый будущий путь:

```text
Natural language
      ↓
AI / deterministic parser
      ↓
typed candidate PlanRequest
      ↓
validation
      ↓
PlanningProblem
      ↓
deterministic planner
```

LLM может помогать интерпретировать предпочтения и намерения, но:

```text
LLM output != validated demand
LLM arithmetic != procurement calculation
LLM confidence != market evidence
```

---

## 9. Plan lifecycle & persistence boundary (M7)

M7 добавляет persistence только для уже возникшего реального требования: после M6 пользовательский plan имеет смысл открыть позднее и увидеть **тот же самый** market basis и результат.

```text
ApplicationPlanResult
        ↓
PlanLifecycleService
        ↓
PlanRecord
        ↓
PlanRepository
        ├── InMemoryPlanRepository
        └── FilePlanRepository
```

`PlanRecord` не является mutable session и не является инструкцией «вычислить снова». Он содержит immutable canonical JSON snapshots:

```text
plan_id
created_at
request
result
market_evidence
digest
```

Market evidence сохраняет exact M4 derivation surface, необходимую для исторической инспекции:

```text
CatalogSnapshot projection
MarketAcquisitionBatch[]
MarketCompilationPolicy
observation dispositions / resolutions
planner-facing offers + provenance
```

Поэтому:

```text
GET /plans/{id}
    != current market acquisition
    != planner recomputation
    != silent migration to today's price
```

`PlanRepository` — application protocol. Domain/planning layers не импортируют filesystem или database adapters. M7 намеренно начинает с local-first JSON filesystem repository, а не с PostgreSQL.

Filesystem adapter:

- использует path-safe `PlanId`;
- публикует полностью записанный same-directory temporary file через hard-link;
- никогда не заменяет существующий `PlanId`;
- имеет bounded record size;
- запрещает record symlink;
- проверяет strict record schema;
- сверяет SHA-256 corruption digest при чтении.

Digest обнаруживает случайную/несогласованную модификацию файла, но **не является цифровой подписью** и не доказывает автора record.

Отдельный `PlanLifecycleJsonApi` добавляет durable HTTP semantics:

```text
POST /plans          -> 201 record created
GET  /plans/{id}     -> 200 exact saved record / 404
GET  /plans?limit=N  -> 200 bounded recent summaries
```

M6 `PlanJsonApi` остаётся compute-only surface с прежним `200` planning result. Host явно выбирает, нужна ему persistence или нет.

---

## 10. Household state & learning boundary (M8)

M8 хранит не «то, что модель запомнила», а explicit household facts. Repository instance является namespace одного household profile; multi-household account identity пока намеренно не вводится.

```text
PurchaseEvent
InventoryCorrection
ConsumptionObservation
        ↓
HouseholdEventRepository
        ↓
HouseholdHistory
        ├── project_household_state()
        │          ↓
        │    HouseholdState
        │          ↓
        │    InventorySnapshot
        │
        └── estimate_consumption()
                   ↓
           ConsumptionEstimate
                   ↓
           RecurringNeedSource
                   ↓
           M2 demand compiler
```

### Source facts

`PurchaseEvent` означает подтверждённую фактическую покупку. Planner selection не превращается в purchase автоматически:

```text
planned purchase != completed purchase
```

`InventoryCorrection` — абсолютный observed on-hand count. Она не меняет предыдущие event files и не стирает provenance.

`ConsumptionObservation` описывает положительное consumed quantity на `[period_start, period_end]`. Для одного Item intervals не могут перекрываться: иначе одна consumption evidence могла бы попасть в state/learning дважды.

У events есть `recorded_at`. Поэтому projection прошлого учитывает knowledge boundary:

```text
effective_at <= as_of
AND
recorded_at <= as_of
```

### Inventory projection

Replay выполняется детерминированно в effective-time order:

```text
purchase    -> add
consumption -> subtract
correction  -> set absolute quantity
```

На одинаковом timestamp correction применяется последней как absolute count. Две corrections одного Item на одном timestamp отклоняются как ambiguous. Consumption, превышающий tracked balance, также отклоняется вместо implicit negative inventory.

Если correction лежит строго внутри aggregate consumption interval, projection fail closed. Absolute count внутри интервала уничтожает информацию о split consumption; угадывать его M8 не пытается.

`HouseholdState.inventory_snapshot()` создаёт обычный domain `InventorySnapshot`, поэтому household state не требует второй inventory model внутри planner.

### Transparent consumption estimate

Estimator использует только non-overlapping `ConsumptionObservation`. Central rate — duration-weighted rate:

```text
weighted_daily_rate =
    total_consumed × one_day / total_observed_duration
```

Вся ratio arithmetic выполняется exact rational/integer arithmetic независимо от ambient Decimal context. Display values округляются half-even до bounded precision.

`ConsumptionEstimate` хранит:

```text
item
daily_quantity              # descriptive rounded rate
sample_count
observed_days
total_consumed              # exact evidence
observed_microseconds        # exact evidence
daily_min
daily_max
uncertainty = daily_max - daily_min
```

`uncertainty` — descriptive observed spread, **не** confidence interval. Будущий estimator может использовать seasonality/weekday/model-based inference, но raw events остаются source of truth.

### Recurring demand

`RecurringNeedSource` реализует существующий M2 `DemandSource` contract. Он не умножает rounded `daily_quantity`. Вместо этого horizon считается напрямую из exact evidence:

```text
expected horizon quantity =
    total_consumed
    × horizon_duration
    / observed_duration
```

Только финальный demand округляется вверх до canonical precision, поэтому промежуточное display rounding не может занизить потребность.

### Durable household events

`HouseholdEventRepository` имеет in-memory и local-first filesystem adapters. File adapter:

- хранит один JSON record на path-safe `HouseholdEventId`;
- никогда не заменяет существующий ID;
- использует same-directory temporary write + `fsync`;
- публикует event через no-overwrite hard link;
- ограничивает размер record;
- запрещает record symlink;
- проверяет strict JSON schema и SHA-256 corruption digest.

Digest обнаруживает accidental/inconsistent modification, но не является trust/signature boundary.

### Planner composition

M8 не добавляет `HouseholdPlanner`. Это намеренно: стандартные outputs уже существуют.

```text
HouseholdState.inventory_snapshot() -> existing inventory input
RecurringNeedSource                 -> existing M2 demand compiler
                                      ↓
                                existing M3 planner
```

Таким образом learning не получает право принимать procurement decisions.

---

## 11. Что намеренно не входит в domain/planning core

Даже после M8 domain/planning core не включает:

- HTTP/ASGI transport semantics;
- UI;
- авторизация;
- аккаунты;
- PostgreSQL;
- Redis;
- очереди;
- Docker orchestration;
- scraper конкретного магазина;
- LLM provider;
- nutrition recommender;
- медицинские рекомендации.

Все эти вещи могут появиться позже вокруг проверенного planner core.
