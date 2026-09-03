from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from household_supply.application import (
    InMemoryPlanRepository,
    PlanApplicationService,
    PlanAsgiApp,
    PlanId,
    PlanLifecycleService,
    RequestedItem,
)
from household_supply.application.household_operations import (
    HouseholdOperationConflictError,
    HouseholdOperationError,
    HouseholdOperationsService,
    HouseholdPlanNotFoundError,
    PurchaseConfirmationCommand,
    StocktakeCommand,
)
from household_supply.application.household_operations_api import HouseholdClosedLoopJsonApi
from household_supply.application.replenishment import (
    HouseholdReplenishmentRequest,
    HouseholdReplenishmentService,
)
from household_supply.domain import (
    CatalogBinding,
    CatalogSnapshot,
    ExternalListingKey,
    Item,
    MarketAcquisitionBatch,
    MarketObservation,
    Money,
    Quantity,
    SKU,
)
from household_supply.household import (
    HouseholdEventId,
    HouseholdLearningService,
    FileHouseholdEventRepository,
    InMemoryHouseholdEventRepository,
)
from household_supply.market import StaticMarketProvider


UTC = timezone.utc
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def build_services(*, now: datetime = BASE):
    milk = Item("milk", "Milk", "dairy")
    oil = Item("oil", "Oil", "pantry")
    milk_sku = SKU("milk-1l", milk, "Milk 1L", Quantity(1, "l"))
    oil_sku = SKU("oil-1l", oil, "Oil 1L", Quantity(1, "l"))
    skus = (milk_sku, oil_sku)
    bindings = tuple(
        CatalogBinding(
            ExternalListingKey("fixture", "store", sku.id), sku.id, "fixture"
        )
        for sku in skus
    )
    catalog = CatalogSnapshot(skus, bindings)
    observations = tuple(
        MarketObservation(
            id=f"obs-{sku.id}",
            provider_id="fixture",
            seller_id="store",
            external_product_id=sku.id,
            price=Money("120" if sku.id == "milk-1l" else "190", "KGS"),
            observed_at=now,
            package_quantity=sku.package_quantity,
            source_ref=f"fixture://{sku.id}",
        )
        for sku in skus
    )
    provider = StaticMarketProvider(MarketAcquisitionBatch("fixture", now, observations))
    household_repo = InMemoryHouseholdEventRepository()
    household = HouseholdLearningService(household_repo)
    plan_repo = InMemoryPlanRepository()
    clock = MutableClock(now)
    planner = PlanApplicationService(catalog, (provider,), clock=clock)
    lifecycle = PlanLifecycleService(
        planner,
        plan_repo,
        clock=clock,
        id_factory=lambda: PlanId("plan-1"),
    )
    replenishment = HouseholdReplenishmentService(household, lifecycle, clock=clock)
    operations = HouseholdOperationsService(household, catalog, plan_repo, clock=clock)
    return operations, replenishment, clock, {"milk": milk, "oil": oil}


def test_stocktake_records_absolute_household_fact() -> None:
    operations, _, _, _ = build_services()
    event = operations.record_stocktake(
        StocktakeCommand(HouseholdEventId("count-1"), "milk", Quantity("1.5", "l"))
    )
    assert event.quantity_on_hand == Quantity("1.5", "l")
    assert operations.state(as_of=BASE).quantity_for("milk") == Quantity("1500", "ml")


def test_stocktake_retry_with_same_event_id_is_idempotent() -> None:
    operations, _, clock, _ = build_services()
    command = StocktakeCommand(HouseholdEventId("count-1"), "milk", Quantity("1500", "ml"))
    first = operations.record_stocktake(command)
    clock.value = BASE + timedelta(minutes=5)
    second = operations.record_stocktake(command)
    assert second == first
    assert len(operations.history().events) == 1


def test_stocktake_event_id_conflict_is_rejected() -> None:
    operations, _, _, _ = build_services()
    operations.record_stocktake(
        StocktakeCommand(HouseholdEventId("same"), "milk", Quantity("1", "l"))
    )
    with pytest.raises(HouseholdOperationConflictError):
        operations.record_stocktake(
            StocktakeCommand(HouseholdEventId("same"), "milk", Quantity("2", "l"))
        )


def test_stocktake_requires_catalog_item_and_compatible_dimension() -> None:
    operations, _, _, _ = build_services()
    with pytest.raises(Exception, match="not present in configured catalog"):
        operations.record_stocktake(
            StocktakeCommand(HouseholdEventId("unknown"), "rice", Quantity("1", "kg"))
        )
    with pytest.raises(HouseholdOperationError, match="dimension"):
        operations.record_stocktake(
            StocktakeCommand(HouseholdEventId("bad-unit"), "milk", Quantity("1", "piece"))
        )
    assert operations.history().events == ()


def test_manual_purchase_uses_exact_catalog_sku_and_updates_inventory() -> None:
    operations, _, _, _ = build_services()
    result = operations.record_purchase(
        PurchaseConfirmationCommand(HouseholdEventId("buy-1"), "milk-1l", 2)
    )
    assert result.plan_id is None
    assert result.planned_packs is None
    assert result.actual_packs == 2
    assert result.event.quantity == Quantity("2", "l")
    assert result.event.sku_id == "milk-1l"
    assert result.event.source_ref == "manual"
    assert operations.state(as_of=BASE).quantity_for("milk") == Quantity("2000", "ml")


def test_plan_linked_purchase_can_differ_from_planned_count() -> None:
    operations, replenishment, clock, _ = build_services()
    plan = replenishment.create(
        HouseholdReplenishmentRequest(
            Money("1000", "KGS"),
            7,
            (RequestedItem("milk", Quantity("1500", "ml")),),
        )
    ).plan_record
    assert plan.result.to_mapping()["purchases"][0]["packs"] == 2

    clock.value = BASE + timedelta(hours=1)
    result = operations.record_purchase(
        PurchaseConfirmationCommand(HouseholdEventId("actual-buy"), "milk-1l", 3),
        plan_id=plan.plan_id,
    )
    assert result.planned_packs == 2
    assert result.actual_packs == 3
    assert result.event.source_ref == "plan:plan-1"
    assert operations.state(as_of=clock.value).quantity_for("milk") == Quantity("3000", "ml")


def test_plan_linked_purchase_allows_unplanned_catalog_sku_with_zero_planned_packs() -> None:
    operations, replenishment, _, _ = build_services()
    plan = replenishment.create(
        HouseholdReplenishmentRequest(
            Money("1000", "KGS"),
            7,
            (RequestedItem("milk", Quantity("1", "l")),),
        )
    ).plan_record
    result = operations.record_purchase(
        PurchaseConfirmationCommand(HouseholdEventId("extra-oil"), "oil-1l", 1),
        plan_id=plan.plan_id,
    )
    assert result.planned_packs == 0
    assert result.event.item.id == "oil"


def test_unknown_plan_does_not_record_purchase() -> None:
    operations, _, _, _ = build_services()
    with pytest.raises(HouseholdPlanNotFoundError):
        operations.record_purchase(
            PurchaseConfirmationCommand(HouseholdEventId("buy"), "milk-1l", 1),
            plan_id=PlanId("missing"),
        )
    assert operations.history().events == ()


def test_closed_loop_stocktake_purchase_stocktake_drives_next_replenishment() -> None:
    operations, replenishment, clock, _ = build_services()
    operations.record_stocktake(
        StocktakeCommand(HouseholdEventId("start-count"), "milk", Quantity("2000", "ml"))
    )
    clock.value = BASE + timedelta(days=1)
    operations.record_purchase(
        PurchaseConfirmationCommand(HouseholdEventId("buy"), "milk-1l", 1)
    )
    clock.value = BASE + timedelta(days=2)
    operations.record_stocktake(
        StocktakeCommand(HouseholdEventId("end-count"), "milk", Quantity("1200", "ml"))
    )

    reports = operations.depletion_reports(as_of=clock.value)
    milk = next(report for report in reports if report.item.id == "milk")
    assert milk.estimate is not None
    assert milk.estimate.daily_quantity == Quantity("900.000000000000", "ml")

    # 900 ml/day * 7 days = 6300 ml; 1200 ml is on hand => buy 6 x 1 L.
    result = replenishment.create(HouseholdReplenishmentRequest(Money("1000", "KGS"), 7))
    stored = result.plan_record.result.to_mapping()
    assert stored["purchases"][0]["packs"] == 6
    assert stored["total_cost"] == {"amount": "720", "currency": "KGS"}


def test_future_household_mutation_is_rejected_before_append() -> None:
    operations, _, _, _ = build_services()
    with pytest.raises(HouseholdOperationError, match="recorded_at"):
        operations.record_stocktake(
            StocktakeCommand(
                HouseholdEventId("future"),
                "milk",
                Quantity("1", "l"),
                occurred_at=BASE + timedelta(days=1),
            )
        )
    assert operations.history().events == ()


def test_closed_loop_json_api_exposes_state_history_estimates_and_stocktake() -> None:
    _, replenishment, _, _ = build_services()
    api = HouseholdClosedLoopJsonApi(replenishment)
    created = api.handle(
        "POST",
        "/household/stocktakes",
        {
            "event_id": "count-1",
            "item_id": "milk",
            "quantity": {"amount": "1.5", "unit": "l"},
        },
    )
    assert created.status == 201
    assert created.body["household"]["balances"][0]["quantity"] == {
        "amount": "1.5E+3",
        "unit": "ml",
    }
    assert api.handle("GET", "/household/history").body["event_count"] == 1
    assert api.handle("GET", "/household/state").status == 200
    estimates = api.handle("GET", "/household/estimates")
    assert estimates.status == 200
    assert estimates.body["reports"][0]["estimate"] is None


def test_closed_loop_json_api_plan_purchase_and_unknown_plan() -> None:
    _, replenishment, clock, _ = build_services()
    plan = replenishment.create(
        HouseholdReplenishmentRequest(
            Money("1000", "KGS"),
            7,
            (RequestedItem("milk", Quantity("1500", "ml")),),
        )
    ).plan_record
    clock.value = BASE + timedelta(minutes=1)
    api = HouseholdClosedLoopJsonApi(replenishment)
    response = api.handle(
        "POST",
        f"/plans/{plan.plan_id.value}/purchases",
        {"event_id": "actual", "sku_id": "milk-1l", "packs": 3},
    )
    assert response.status == 201
    assert response.body["purchase"]["planned_packs"] == 2
    assert response.body["purchase"]["actual_packs"] == 3

    missing = api.handle(
        "POST",
        "/plans/missing/purchases",
        {"event_id": "missing-buy", "sku_id": "milk-1l", "packs": 1},
    )
    assert missing.status == 404


def test_closed_loop_json_api_is_strict() -> None:
    _, replenishment, _, _ = build_services()
    api = HouseholdClosedLoopJsonApi(replenishment)
    response = api.handle(
        "POST",
        "/household/stocktakes",
        {
            "event_id": "count",
            "item_id": "milk",
            "quantity": {"amount": "1", "unit": "l"},
            "inventory": [],
        },
    )
    assert response.status == 422
    assert api.handle("GET", "/household/state?x=1").status == 400


async def _asgi_request(app, *, method: str, path: str, payload=None):
    sent = []
    body = b"" if payload is None else json.dumps(payload).encode("utf-8")
    messages = iter([{"type": "http.request", "body": body, "more_body": False}])

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
        send,
    )
    return sent


def test_existing_asgi_adapter_reads_m10_mutation_bodies() -> None:
    _, replenishment, _, _ = build_services()
    app = PlanAsgiApp(HouseholdClosedLoopJsonApi(replenishment))
    sent = asyncio.run(
        _asgi_request(
            app,
            method="POST",
            path="/household/stocktakes",
            payload={
                "event_id": "count-asgi",
                "item_id": "milk",
                "quantity": {"amount": "1", "unit": "l"},
            },
        )
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = next(message for message in sent if message["type"] == "http.response.body")
    assert start["status"] == 201
    assert json.loads(body["body"])["event"]["event_type"] == "inventory_correction"


def test_plan_linked_purchase_rejects_catalog_package_identity_drift() -> None:
    operations, replenishment, _, items = build_services()
    plan = replenishment.create(
        HouseholdReplenishmentRequest(
            Money("1000", "KGS"),
            7,
            (RequestedItem("milk", Quantity("1500", "ml")),),
        )
    ).plan_record
    changed_sku = SKU("milk-1l", items["milk"], "Milk changed", Quantity(2, "l"))
    changed_catalog = CatalogSnapshot((changed_sku,), ())
    changed_operations = HouseholdOperationsService(
        operations.household,
        changed_catalog,
        operations.plans,
        clock=operations.clock,
    )
    with pytest.raises(HouseholdOperationConflictError, match="package conflicts"):
        changed_operations.record_purchase(
            PurchaseConfirmationCommand(HouseholdEventId("drift"), "milk-1l", 1),
            plan_id=plan.plan_id,
        )
    assert changed_operations.household.repository.get(HouseholdEventId("drift")) is None


def test_concurrent_same_stocktake_is_idempotent_with_file_repository(tmp_path) -> None:
    base_operations, _, clock, _ = build_services()
    household = HouseholdLearningService(FileHouseholdEventRepository(tmp_path / "events"))
    operations = HouseholdOperationsService(
        household,
        base_operations.catalog,
        base_operations.plans,
        clock=clock,
    )
    command = StocktakeCommand(
        HouseholdEventId("same-concurrent"), "milk", Quantity("1", "l")
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(pool.map(lambda _: operations.record_stocktake(command), range(8)))
    assert len(set(results)) == 1
    assert len(operations.history().events) == 1
    assert len(tuple((tmp_path / "events").glob("*.json"))) == 1

async def _asgi_raw_json(app, *, path: str, body: bytes):
    sent = []
    messages = iter([{"type": "http.request", "body": body, "more_body": False}])

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
        send,
    )
    return sent


@pytest.mark.parametrize(
    "body",
    [
        b'{"event_id":"x","event_id":"y","item_id":"milk","quantity":{"amount":"1","unit":"l"}}',
        b'{"event_id":"x","item_id":"milk","quantity":{"amount":NaN,"unit":"l"}}',
    ],
)
def test_asgi_rejects_ambiguous_or_non_finite_raw_json(body: bytes) -> None:
    _, replenishment, _, _ = build_services()
    app = PlanAsgiApp(HouseholdClosedLoopJsonApi(replenishment))
    sent = asyncio.run(_asgi_raw_json(app, path="/household/stocktakes", body=body))
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_body = next(
        message for message in sent if message["type"] == "http.response.body"
    )
    assert start["status"] == 400
    assert json.loads(response_body["body"]) == {"error": "invalid_json"}
