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
contributions[]  # точные вклады конкретных sources
demands[]        # нормализованный aggregate по Item
```

Так provenance не приходится кодировать в арифметику planner-а. `compile_demand_sources()` проверяет уникальность source IDs, совместимость единиц и непротиворечивую Item identity, после чего детерминированно сортирует нормализованный demand по `item.id`.

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

`MealRequest` связывает рецепт с фактически нужным количеством порций. Масштабирование выполняется точно через `Decimal`:

```text
scale = requested_servings / recipe.servings
ingredient demand = ingredient.quantity * scale
```

`float` для servings намеренно не принимается: компиляция demand должна быть воспроизводимой и не вносить двоичную погрешность до planner-а.

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

Явно задаёт критерии выбора.

Например:

```text
PlanningPolicy
- hard_budget
- preferred_currency
- store_visit_penalty
- surplus_penalty
- preference_penalties
```

Первый planner может использовать только стоимость и hard budget.

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

Минимальная будущая форма:

```text
ProcurementPlan
- status
- purchases[]
- requirement_coverage[]
- projected_leftovers[]
- total_cost
- budget_remaining
- objective_breakdown
- warnings[]
- explanation[]
```

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

---

## 5. Модули

Предварительная структура:

```text
src/
└── <package_name>/
    ├── domain/
    │   ├── money.py
    │   ├── quantity.py
    │   ├── items.py
    │   ├── household.py
    │   ├── inventory.py
    │   ├── demand.py
    │   ├── market.py
    │   └── plan.py
    │
    ├── demand/
    │   ├── explicit.py
    │   ├── meals.py
    │   └── recurring.py
    │
    ├── inventory/
    │   └── reconcile.py
    │
    ├── catalog/
    │   ├── matching.py
    │   └── substitutions.py
    │
    ├── planning/
    │   ├── problem.py
    │   ├── compile.py
    │   ├── baseline.py
    │   ├── validate.py
    │   └── explain.py
    │
    ├── learning/
    │   └── consumption.py
    │
    └── adapters/
        ├── api/
        ├── persistence/
        └── market/
```

Python package name зафиксирован в M1: `household_supply`.

---

## 6. Planner

### Baseline Planner

Первый planner намеренно простой:

```text
single objective:
    minimize purchase cost

constraints:
    requirements covered
    hard budget respected
    offers available
    packages integer
    quantities valid
```

Его задача — стать корректным baseline, а не сразу найти глобально лучшую бытовую стратегию.

### Дальнейшее развитие objective

Позже:

```text
minimize(
    purchase_cost
    + α * surplus
    + β * store_visits
    + γ * preference_penalty
    + δ * preparation_burden
)
```

После появления достаточно сложных combinatorial cases можно рассматривать CP-SAT/MIP. Оптимизационный движок не должен становиться частью доменной модели.

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
