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

---

## 5. Модули

Актуальная логическая структура planning core после M3:

```text
src/household_supply/
├── domain/
│   ├── money.py
│   ├── quantity.py
│   ├── items.py
│   ├── inventory.py
│   ├── demand.py
│   ├── recipes.py
│   ├── market.py
│   ├── objectives.py
│   └── plan.py
│
├── demand/
│   ├── sources.py
│   └── compile.py
│
└── planning/
    ├── compile.py       # Demand + inventory -> net requirements
    ├── candidates.py    # package-aware purchase candidates
    ├── baseline.py      # M1 cost-only selection
    ├── objective.py     # M3 score accounting
    ├── multi_objective.py
    ├── assemble.py      # common ProcurementPlan assembly
    └── validate.py
```

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

## 7. AI boundary

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

## 8. Persistence boundary

База данных появится тогда, когда появятся состояния, которые действительно нужно сохранять.

Кандидаты:

```text
Items / SKUs
Offers / observations
Inventory events
Purchase events
Consumption observations
Plans
Household preferences
```

До этого fixture-файлы достаточны.

PostgreSQL и SQLAlchemy не являются архитектурным требованием M1.

---

## 9. Learning boundary

Фраза:

> «Молоко обычно заканчивается раз в пять дней»

не должна храниться как единственная истина системы.

Предпочтительная модель:

```text
PurchaseEvent
InventoryObservation
ConsumptionObservation
        ↓
ConsumptionEstimator
        ↓
ConsumptionEstimate
```

`ConsumptionEstimate` содержит как минимум:

```text
estimated_rate
sample_count
uncertainty/confidence
basis_window
```

Алгоритм оценки можно менять без потери исходной истории.

---

## 10. Что намеренно не входит в ядро

На текущем этапе:

- HTTP;
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
