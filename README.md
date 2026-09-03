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

**M12 — First-Use Experience & Household Bootstrap реализован.**

M1–M10 уже дают deterministic planner, market evidence, durable plan history и closed-loop household operations. M11 впервые делает этот цикл доступным обычному локальному пользователю без ручной работы с Python/JSON:

```text
browser
   ↓
fixed local web assets
   ↓
existing M10 JSON API
   ├── stocktake
   ├── actual purchase
   ├── depletion evidence
   └── M9 replenishment plan
```

Web shell не получает новую planning authority. JavaScript отображает server evidence и собирает typed JSON requests; budget arithmetic, depletion inference, market admission и purchase semantics остаются в существующих Python layers.

M11 добавляет read-only browser discovery endpoint:

```text
GET /catalog
```

Он публикует только canonical `Item`/`SKU` + package quantity, необходимые UI. Retailer listing keys, seller identity и observations не копируются в frontend catalog.

Local UI после первого usability pass русскоязычный и mobile-first. Основной экран использует человеческую модель вместо backend-терминов:

- `Покупки` — период, бюджет и только обязательные пожелания;
- `Что дома` — быстрые варианты `Нет / Половина / 1 упаковка / 2 упаковки / Другое`;
- `История` — прошлые списки, изменения запасов и простое объяснение того, что система уже поняла.

Пользователь не выбирает `ml/g/l/kg` в основном stock-update flow и не видит `stocktake`, `depletion`, `explicit need`, `horizon` или `SKU` как продуктовые понятия. UI переводит упаковочные действия в точные typed `Quantity`, а backend contracts остаются прежними. Система предлагает список, человек только добавляет обязательные пожелания и после магазина подтверждает реальность.

Статические assets раздаются только из фиксированного package-resource allowlist (`/`, `/assets/app.js`, `/assets/styles.css`): arbitrary filesystem path serving отсутствует. HTML получает restrictive same-origin CSP, `nosniff`, `DENY` framing и `no-referrer`.

Runner по умолчанию bind'ится только на loopback (`127.0.0.1`). Web shell дополнительно reject'ит non-loopback/hostile `Host` headers и cross-origin unsafe requests, чтобы local bind не полагался только на сетевой интерфейс. Поскольку M11 ещё не имеет auth, попытка открыть unauthenticated UI на `0.0.0.0`/LAN fail-closed без explicit remote opt-in.

Для локального запуска demo host:

```bash
python -m pip install -e ".[dev,web]"
python examples/m11_local_web.py --serve
```

Затем открыть `http://127.0.0.1:8765/`. Default demo host остаётся полностью offline для CI и разработки.

После установки M5.1 real catalog pack тот же UI можно запустить с живым Globus market:

```bash
python examples/m11_local_web.py --serve --live-globus
```

Live composition использует полный canonical M5.1 catalog для browser discovery, но перед внешним acquisition выбирает только exact retailer listings для `Item`, реально присутствующих в текущем planning request. Поэтому запрос на молоко не сканирует весь catalog. Selection идёт через existing `CatalogBinding`, а не по названиям. Offline и live profiles используют разные default data directories, чтобы demo household history не смешивалась с real-catalog identity.

M12 добавляет первый запуск без README: пользователь выбирает несколько обычных домашних товаров, для каждого явно отмечает `Нет` / `Половина` / `1 упаковка` / `2 упаковки`, после чего браузер записывает обычные M10 `InventoryCorrection` через существующий stocktake API. Товары, которые обычно бывают дома, но отмечены как `Нет`, только предварительно попадают в «Нужно обязательно» и остаются редактируемыми до расчёта. Следующий шаг — первый внешний usability pilot без подсказок; Natural Language остаётся downstream adapter после этого evidence gate.

Подробнее:

- [Архитектура](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)

## Текущая исполнимая гарантия

> Русскоязычный local browser client может без знания backend-терминов отметить примерные остатки, получить список покупок, скорректировать обязательные товары и подтвердить фактическую покупку; exact quantities, planning и learning authority остаются в существующих backend layers.

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
python examples/m9_household_replenishment.py
python examples/m10_closed_loop_household.py
python examples/m11_local_web.py
```

Опциональные **live smoke** (требуют сети и обращаются к публичному Globus demo catalog):

```bash
python examples/m5_globus_live.py
python examples/m6_globus_live.py
```

CI остаётся полностью offline. M11 добавляет packaged HTML/CSS/vanilla JS и optional `uvicorn` runtime extra, но по-прежнему не требует PostgreSQL, Redis, Docker orchestration, auth или LLM.
