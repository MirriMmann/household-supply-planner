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

**M6 — Application Service + Minimal JSON/CLI/ASGI Boundary реализован.**

M1–M5 доказали demand, optimization, market evidence и первый live retailer adapter. M6 впервые собирает эти слои в одну тонкую прикладную операцию:

```text
JSON / CLI / ASGI request
        ↓
ApplicationPlanRequest
        ↓
catalog preflight
        ↓
PlanApplicationService
        ↓
MarketProvider[]
        ↓
M4 MarketCompilation
        ↓
PlanningProblem
        ↓
M3 planner
        ↓
ApplicationPlanResult
        ↓
JSON response
```

Application boundary намеренно не содержит planner logic. `PlanApplicationService` только валидирует request против exact configured catalog, получает attributable market evidence, собирает уже существующий `PlanningProblem` и вызывает M3. Неизвестный `item_id`, несовместимая единица или бессмысленная objective policy отклоняются **до market acquisition**, поэтому ошибочный пользовательский ввод не запускает внешнюю сеть.

Freshness также принадлежит application host: service использует timezone-aware injected clock после acquisition, поэтому M4 `max_observation_age` измеряется относительно фактического application capture time, а не времени, которое принёс сам provider.

M6 добавляет три тонких adapter surface:

- `PlanJsonApi` — transport-neutral `POST /plans` / `GET /health`;
- `run_plan_cli()` — injected CLI adapter, который читает тот же JSON contract из файла/stdin;
- `PlanAsgiApp` — dependency-free ASGI wrapper с bounded JSON body.

ASGI/CLI получают готовый `PlanApplicationService` от embedding host. Поэтому core не захардкоживает Globus, FastAPI, конкретный каталог или способ запуска сервера. Будущий M5.1 catalog pack можно подключить к тому же service без изменения HTTP/CLI semantics.

`infeasible` остаётся нормальным результатом планирования (`200` с `status=infeasible`), а не transport error. Ошибка input contract даёт `422`, ошибка market acquisition — `502`. Неожиданные programming/runtime failures не маскируются под пользовательскую ошибку.

Минимальный JSON request:

```json
{
  "budget": {"amount": "3000", "currency": "KGS"},
  "demands": [
    {"item_id": "milk", "quantity": {"amount": "1500", "unit": "ml"}},
    {"item_id": "oil", "quantity": {"amount": "500", "unit": "ml"}}
  ],
  "inventory": [
    {
      "lot_id": "opened-milk",
      "item_id": "milk",
      "quantity": {"amount": "250", "unit": "ml"}
    }
  ]
}
```

Денежные и дробные quantity values в JSON рекомендуется передавать строками. JSON `float` намеренно отклоняется, чтобы transport не вносил binary rounding до domain layer.

Подробнее:

- [Архитектура](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)

## Текущая исполнимая гарантия

> Типизированный application request с budget, explicit demand, optional inventory и soft objective policy проходит catalog preflight, получает текущий market snapshot через M4/M5 и возвращает self-validating procurement result через неизменённый M3 planner. JSON, CLI и ASGI adapters не содержат скрытой planning semantics.

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
```

Опциональные **live smoke** (требуют сети и обращаются к публичному Globus demo catalog):

```bash
python examples/m5_globus_live.py
python examples/m6_globus_live.py
```

CI остаётся полностью offline: retailer transport заменяется deterministic fixture implementation. M6 по-прежнему не требует PostgreSQL, Redis, Docker orchestration, frontend, auth или LLM.
