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

Цель: сделать результат бытово разумнее, чем просто минимальная цена.

### Добавить

- projected surplus;
- waste/surplus penalty;
- store visit penalty;
- несколько продавцов;
- objective breakdown;
- explainability.

Пример:

```text
Store A + Store B дешевле на 37 KGS,
но политика оценивает дополнительную поездку в 100 KGS,
поэтому выбран план только из Store A.
```

### Важное требование

Каждый новый objective сравнивается с M1 baseline на фиксированном corpus задач.

Если более сложный solver не улучшает целевую метрику или ухудшает корректность, он не заменяет baseline.

---

## M4 — Application Boundary

Только после стабильного ядра добавить внешний интерфейс.

Возможный первый API:

```text
POST /plans
GET  /plans/{id}
```

API переводит transport schema в доменные типы и обратно.

Он не должен содержать planner logic.

На этом этапе можно решить, нужна ли persistence layer вообще и какая.

---

## M5 — Market Acquisition

Цель: заменить fixture market реальными наблюдениями.

Через интерфейс вроде:

```text
MarketProvider
    -> MarketSnapshot
```

Возможные реализации:

```text
FixtureMarketProvider
RetailerApiProvider
ScraperProvider
OpenDataProvider
ReceiptImportProvider
```

### Обязательные свойства market data

- источник;
- timestamp;
- seller;
- SKU identity;
- currency;
- availability;
- возможность пометить устаревшие данные.

---

## M6 — Household State & Learning

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

## M7 — Natural Language Interface

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
