<div align="center">

# Household Supply Planner

### Покупки, которые учитывают то, что уже есть дома.

**Local-first система планирования домашних запасов и закупок.**
Она хранит фактические остатки, учится по тому, что заканчивается, получает market evidence и строит детерминированный план покупки под бюджет.

[![CI](https://github.com/MirriMmann/household-supply-planner/actions/workflows/ci.yml/badge.svg)](https://github.com/MirriMmann/household-supply-planner/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-111111?logo=python\&logoColor=white)

</div>

<br>

```text
Что уже есть дома
        ↓
Что реально заканчивается
        ↓
Что можно купить сейчас
        ↓
Что стоит купить — и почему
```

Household Supply Planner — не ещё один grocery list и не каталог готовых рецептов.
Это маленькая planning system для повторяющихся домашних закупок:

* она отделяет **потребность** от конкретного товара в магазине;
* учитывает **то, что уже есть дома**;
* рассматривает цену как **наблюдение рынка**, а не вечное свойство товара;
* рассчитывает целые упаковки и бюджет **детерминированно**;
* меняет household state только после **фактического подтверждения** человеком.

---

## Один цикл вместо списка

### 1. Отметьте, что есть дома

Первый запуск не требует описывать весь шкаф или вводить граммы вручную.

Выберите несколько обычных товаров и ближайший понятный вариант:

```text
Нет · Половина · 1 упаковка · 2 упаковки
```

UI переводит этот выбор в точную `Quantity`; backend сохраняет обычный authoritative stocktake.

### 2. Составьте покупки

Укажите горизонт и бюджет. При необходимости добавьте то, что нужно обязательно.

Система объединяет:

```text
inventory
+ learned depletion
+ explicit needs
+ current market evidence
+ budget
```

и строит один `ProcurementPlan`.

### 3. После магазина подтвердите реальность

План — это рекомендация, не факт покупки.

Домашние запасы меняются только после того, как человек подтверждает, сколько действительно куплено.

### 4. Следующий план становится лучше

Последующие stocktakes позволяют выводить depletion между наблюдениями:

```text
было
+ подтверждённые покупки
- стало
=
фактическое выбытие
```

Эта история используется для будущего replenishment, но не создаёт вторую скрытую модель инвентаря.

---

## Попробовать локально

Никаких Docker, PostgreSQL, Redis или LLM для запуска не требуется.

```bash
git clone https://github.com/MirriMmann/household-supply-planner.git
cd household-supply-planner

python -m venv .venv
python -m pip install -e ".[web]"
python examples/m11_local_web.py --serve
```

Откройте:

```text
http://127.0.0.1:8765/
```

По умолчанию это полностью offline demo: интерфейс, planner, household history и persistence настоящие; market fixture детерминированный.

### С живым Globus Online

```bash
python examples/m11_local_web.py --serve --live-globus
```

Live composition использует реальный packaged-staples catalog Globus Online.
Перед сетевым acquisition выбираются только retailer listings для товаров, которые действительно нужны текущему запросу — полный каталог не сканируется на каждый plan.

> Live market mode зависит от доступности и текущей структуры публичного retailer surface. CI остаётся полностью offline.

---

## Почему результату можно доверять

### Детерминированный planner

Одинаковые typed inputs дают одинаковый planning result. Budget arithmetic, package rounding и hard constraints не делегируются генеративной модели.

### Evidence вместо догадок

`Item`, `SKU`, `Offer` и `MarketObservation` — разные сущности.

```text
молоко
!=
конкретная упаковка 1 л
!=
предложение магазина по конкретной цене в конкретный момент
```

Catalog resolution использует exact identity/bindings. Fuzzy product matching не становится источником market truth.

### Recommendation ≠ reality

```text
ProcurementPlan != PurchaseEvent
```

Сохранённый план не изменяет household state. Только подтверждённая фактическая покупка становится событием истории.

### Learning без скрытой магии

Система учится по наблюдаемым stocktakes и подтверждённым покупкам.
Если данные противоречат друг другу или появляется необъяснимый inflow, модель не выдумывает расход.

---

## Что уже работает

**M12 — First-Use Experience & Household Bootstrap**

Текущий vertical slice включает:

* deterministic package-aware procurement planner;
* budget constraints и infeasible results;
* multi-objective planning;
* canonical `Item` / `SKU` / market evidence model;
* real Globus Online provider и packaged-staples catalog;
* request-scoped live market acquisition;
* durable plan history;
* append-only household event history;
* stocktakes и purchase confirmations;
* depletion learning;
* recurring replenishment;
* Russian-first mobile-friendly local web UI;
* first-use onboarding без знания backend terminology.

CI проверяет Python 3.11 и 3.13, offline examples и compileability.

---

## Что пока намеренно ограничено

Проект уже имеет рабочий vertical slice, но ещё не позиционируется как законченный массовый consumer service.

Сейчас:

* основной domain — packaged food / household staples;
* реальный catalog пока имеет ограниченное practical coverage;
* live retailer integration — Globus Online;
* UI работает локально и по умолчанию bind'ится только на loopback;
* remote mode не имеет полноценной authentication model;
* Natural Language Interface ещё не реализован как основной пользовательский вход;
* multi-user/cloud sync и native mobile app не входят в текущий slice.

Ближайший этап — не добавление нового AI layer, а превращение работающего vertical slice в удобный бытовой продукт:

```text
practical catalog coverage
        +
real-use UX/UI audit
        ↓
faster stock updates
        +
in-store shopping mode
        ↓
daily-use household workflow
```

Natural Language Interface остаётся downstream adapter и должен появляться поверх workflow, который уже удобен без него.

---

## Архитектура в одном экране

```text
Browser UI
    ↓ typed commands
Household operations
    ↓
Demand compilation
    ↓
request-scoped market acquisition
    ↓
market evidence admission
    ↓
deterministic planner
    ↓
durable plan record
    ↓
human purchase confirmation
    ↓
household history
    ↓
depletion learning
    └────────────→ next replenishment
```

Ключевая граница:

```text
AI / UI may interpret intent
        ↓
typed request
        ↓
validation
        ↓
deterministic planning authority
```

Ни UI, ни будущий LLM не владеют ценой, бюджетной корректностью, market truth или фактом покупки.

Подробности:

* [Product Vision](docs/PRODUCT_VISION.md) — каким должен стать продукт;
* [Architecture](docs/ARCHITECTURE.md) — как устроена система и где проходят authority boundaries;
* [Roadmap](docs/ROADMAP.md) — как проект движется от текущего состояния к vision.

---

## Для разработчиков

Установить dev-зависимости и запустить весь offline suite:

```bash
python -m pip install -e ".[dev,web]"
python -m pytest
python -m compileall -q src examples tests
```

Основные слои:

```text
src/household_supply/
├── domain/       # Item, SKU, quantities, planning primitives
├── demand/       # demand sources and compilation
├── market/       # observations, catalog resolution, providers
├── planning/     # deterministic procurement optimization
├── household/    # event history, projection, depletion learning
├── application/  # orchestration, lifecycle, persistence
└── web/          # local browser surface
```

Архитектурный принцип проекта — **core first, infrastructure second**.
Domain/planning code не зависит от FastAPI, SQLAlchemy, PostgreSQL, retailer SDK или LLM SDK.

---

## Статус проекта

`Household Supply Planner` — рабочее техническое название; продуктовый бренд будет выбран позже.

Репозиторий развивается milestone-by-milestone с явными invariants и regression tests.
Текущая цель — превратить доказанный closed-loop vertical slice в удобный real-use household workflow, прежде чем расширять AI/interface layer.
