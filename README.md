# Household Supply Planner

> Рабочее техническое название. Продуктовое имя будет выбрано позже.

**Household Supply Planner** — система планирования снабжения домохозяйства.

Цель проекта — не сделать ещё один список покупок и не обернуть рецепты в FastAPI. Система должна понимать будущие потребности дома, учитывать уже имеющиеся запасы, сопоставлять их с реальными вариантами покупки и строить объяснимый план закупки под ограничения пользователя.

Пример исходного запроса:

> «Мне нужно прожить одному неделю. Бюджет — 3000 KGS».

В будущем система должна уметь превратить такой запрос примерно в следующий процесс:

```text
потребности на период
        +
текущие запасы дома
        +
доступные товары и цены
        +
бюджет / предпочтения / ограничения
        ↓
задача планирования
        ↓
план закупки
        ↓
что купить / где / сколько / почему
```

## Чем проект отличается от обычного meal planner

Рецепт — только один из возможных источников потребности.

Система проектируется вокруг пяти независимых частей:

1. **Demand** — что понадобится в выбранном горизонте.
2. **Inventory** — что уже есть дома.
3. **Market** — что реально доступно для покупки.
4. **Planning** — как удовлетворить потребности с минимальными потерями и в рамках ограничений.
5. **Learning** — что фактически произошло после плана и что можно вывести из истории использования.

Поэтому базовая цепочка проекта выглядит не так:

```text
Recipe -> Ingredient -> Shopping List
```

а так:

```text
Demand Sources
      +
Inventory Snapshot
      +
Market Snapshot
      +
Planning Policy
      ↓
Planning Problem
      ↓
Planner
      ↓
Procurement Plan
```

## Ключевое разделение

В доменной модели намеренно разделяются разные уровни реальности:

```text
Item != SKU != Offer
```

Например:

```text
рис
!=
конкретная пачка риса 800 г
!=
эта пачка в конкретном магазине за 129 KGS в конкретный момент времени
```

- **Item** — семантическая потребность: рис, молоко, куриное филе.
- **SKU** — конкретно покупаемый товар и упаковка.
- **Offer** — предложение конкретного продавца: SKU + цена + доступность + время наблюдения + источник.

Это разделение является частью архитектуры, а не деталями базы данных.

## Что должен уметь первый настоящий vertical slice

Первый программный milestone не требует AI, PostgreSQL или реальных магазинов.

На фиксированных тестовых данных система должна:

- принять потребности;
- учесть существующие домашние запасы;
- рассчитать недостающее количество;
- сопоставить его с доступными упаковками;
- корректно округлить покупку до целых упаковок;
- выбрать допустимый вариант закупки;
- соблюдать бюджет;
- посчитать стоимость;
- показать ожидаемые остатки;
- объяснить, почему выбран именно этот вариант;
- явно сообщить, если допустимого плана не существует.

Пример:

```text
Household:
    1 человек

Budget:
    3000 KGS

Inventory:
    rice: 350 g

Demand:
    rice: 900 g
    chicken: 1000 g
    milk: 1500 ml

Market:
    Store A:
        rice 800 g     120 KGS
        chicken 1 kg   370 KGS
        milk 1 L        90 KGS

    Store B:
        rice 1 kg      135 KGS
        chicken 500 g  180 KGS
        milk 930 ml     82 KGS
```

Результатом должен быть не просто `shopping_list`, а полноценный `ProcurementPlan`, содержащий покупки, покрытие потребностей, стоимость, остаток бюджета, ожидаемый surplus и объяснение решения.

## Архитектурные принципы

### 1. Ядро не зависит от инфраструктуры

Доменная модель и planner не должны импортировать:

- FastAPI;
- SQLAlchemy;
- PostgreSQL-клиент;
- конкретные API магазинов;
- LLM SDK.

HTTP, БД, UI и внешние источники — адаптеры вокруг ядра.

### 2. Одинаковый вход должен давать одинаковый результат

Для фиксированных `HouseholdSnapshot`, `InventorySnapshot`, `MarketSnapshot`, `DemandBundle` и `PlanningPolicy` planner должен быть воспроизводимым.

### 3. Цена — это наблюдение, а не вечное свойство товара

Цена всегда относится к:

- конкретному SKU;
- конкретному продавцу;
- конкретному моменту времени;
- конкретному источнику данных.

### 4. AI не владеет математикой планирования

LLM в будущем может переводить естественный язык в типизированный запрос, например:

```text
«Недорого на неделю, побольше белка, рыбу не люблю»
        ↓
PlanRequest(...)
```

Но вычисление корзины, проверка бюджета и валидация ограничений остаются детерминированными.

### 5. Невозможный план — нормальный результат

Если бюджет, ассортимент или ограничения несовместимы, система не должна молча нарушать условия. Она должна вернуть явный `infeasible`-результат и объяснить причины.

## Границы первой версии

Первая область — **еда**.

Следующее естественное расширение — расходуемые бытовые товары:

- мыло;
- зубная паста;
- стиральный порошок;
- туалетная бумага;
- бытовая химия.

У них похожая модель: запас → расход → пополнение.

Одежда, электроника и другие долговечные покупки — отдельный класс задач и не должны искусственно встраиваться в модель регулярного пополнения.

OTC-лекарства также не входят в первый домен: система может когда-нибудь помогать с учётом запасов, но медицинские рекомендации требуют отдельной политики и границ безопасности.

## Текущий статус

**M8 — Household State & Learning реализован.**

M1–M7 доказали deterministic planning, attributable market evidence, application/lifecycle boundaries и durable plan history. M8 впервые добавляет историю **конкретного household namespace** как набор явных фактов, а не скрытую память модели:

```text
PurchaseEvent / InventoryCorrection / ConsumptionObservation
        ↓
HouseholdEventRepository
        ↓
HouseholdHistory
        ├── deterministic HouseholdState projection
        └── transparent ConsumptionEstimate
                         ↓
                 RecurringNeedSource
                         ↓
                M2 demand compilation
                         ↓
                 existing planner
```

События immutable и append-only. `ProcurementPlan` сам по себе **не** создаёт `PurchaseEvent`: запланировать покупку и реально купить товар — разные факты. `InventoryCorrection` не редактирует прошлые события, а добавляет новое absolute observation. Историческая проекция учитывает не только effective time, но и `recorded_at`, поэтому поздно внесённая коррекция не меняет то, что система могла знать раньше.

M8 learning остаётся полностью воспроизводимым. `ConsumptionEstimate` выводится только из explicit non-overlapping `ConsumptionObservation` и сохраняет:

- weighted daily quantity;
- sample count;
- exact total consumed evidence;
- exact observed microseconds;
- observed duration;
- min/max observed daily rate;
- `uncertainty` как descriptive max-minus-min spread.

`uncertainty` не выдаётся за confidence interval. Exact evidence сохраняется отдельно от rounded display estimate, поэтому `RecurringNeedSource` считает horizon напрямую из исходного рационального basis и округляет вверх только финальный demand.

Local-first event persistence реализована через `FileHouseholdEventRepository`: один integrity-checked JSON file на `HouseholdEventId`, no-overwrite identity и atomic hard-link publication. SHA-256 digest предназначен для corruption detection, а не для authentication.

M8 intentionally fail-closed на неоднозначной истории: overlapping consumption windows, расход выше tracked inventory, conflicting Item identity и `InventoryCorrection` внутри consumption interval не превращаются в молчаливую догадку.

Подробнее:

- [Архитектура](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)

## Текущая исполнимая гарантия

> Append-only household facts можно durability сохранить, детерминированно спроецировать в текущий inventory, вывести из explicit consumption observations прозрачную daily-rate estimate и превратить её в attributable recurring demand, который проходит через существующий M2/M3 planning core без скрытой model memory.

Проверка из активированного виртуального окружения:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python examples/m1_week_one.py
python examples/m2_meal_demand.py
python examples/m3_multi_objective.py
python examples/m4_market_acquisition.py
python examples/m5_globus_provider.py
python examples/m6_application_service.py
python examples/m7_plan_persistence.py
python examples/m8_household_learning.py
```

Опциональные **live smoke** (требуют сети и обращаются к публичному Globus demo catalog):

```bash
python examples/m5_globus_live.py
python examples/m6_globus_live.py
```

CI остаётся полностью offline. M8 по-прежнему не требует PostgreSQL, Redis, Docker orchestration, frontend, auth или LLM.
