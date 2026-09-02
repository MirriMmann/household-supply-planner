from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from household_supply.application import (
    InMemoryPlanRepository,
    PlanApplicationService,
    PlanId,
    PlanLifecycleService,
    RequestedItem,
)
from household_supply.application.replenishment import (
    HouseholdReplenishmentPreparation,
    HouseholdReplenishmentRequest,
    HouseholdReplenishmentRequestError,
    HouseholdReplenishmentResult,
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
    ConsumptionObservation,
    HouseholdEventId,
    HouseholdLearningService,
    InMemoryHouseholdEventRepository,
    InventoryCorrection,
    PurchaseEvent,
)
from household_supply.market import StaticMarketProvider


UTC = timezone.utc
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
NOW = BASE + timedelta(days=4)


def catalog_and_provider(*, include_oil: bool = True):
    milk = Item("milk", "Milk", "dairy")
    milk_sku = SKU("milk-1l", milk, "Milk 1L", Quantity(1, "l"))
    entries = [(milk, milk_sku, "120")]
    if include_oil:
        oil = Item("oil", "Oil", "pantry")
        oil_sku = SKU("oil-1l", oil, "Oil 1L", Quantity(1, "l"))
        entries.append((oil, oil_sku, "190"))

    bindings = []
    observations = []
    skus = []
    for item, sku, price in entries:
        skus.append(sku)
        key = ExternalListingKey("fixture", "store-a", sku.id)
        bindings.append(CatalogBinding(key, sku.id, "fixture"))
        observations.append(
            MarketObservation(
                id=f"obs-{item.id}",
                provider_id="fixture",
                seller_id="store-a",
                external_product_id=sku.id,
                price=Money(price, "KGS"),
                observed_at=NOW,
                package_quantity=sku.package_quantity,
                source_ref=f"fixture://{item.id}",
            )
        )
    catalog = CatalogSnapshot(tuple(skus), tuple(bindings))
    batch = MarketAcquisitionBatch("fixture", NOW, tuple(observations))
    return catalog, StaticMarketProvider(batch), {item.id: item for item, _, _ in entries}


def household_with_milk_history(milk: Item) -> HouseholdLearningService:
    repo = InMemoryHouseholdEventRepository()
    service = HouseholdLearningService(repo)
    service.record(
        PurchaseEvent(
            HouseholdEventId("p1"), milk, Quantity("2", "l"), BASE, BASE
        )
    )
    service.record(
        ConsumptionObservation(
            HouseholdEventId("use1"),
            milk,
            Quantity("500", "ml"),
            BASE,
            BASE + timedelta(days=1),
            BASE + timedelta(days=1),
        )
    )
    service.record(
        InventoryCorrection(
            HouseholdEventId("count1"),
            milk,
            Quantity("1400", "ml"),
            BASE + timedelta(days=2),
            BASE + timedelta(days=2),
            "manual count",
        )
    )
    service.record(
        ConsumptionObservation(
            HouseholdEventId("use2"),
            milk,
            Quantity("300", "ml"),
            BASE + timedelta(days=2),
            BASE + timedelta(days=3),
            BASE + timedelta(days=3),
        )
    )
    return service


def make_replenishment_service(*, household=None, include_oil: bool = True):
    catalog, provider, items = catalog_and_provider(include_oil=include_oil)
    if household is None:
        household = household_with_milk_history(items["milk"])
    planner = PlanApplicationService(catalog, (provider,), clock=lambda: NOW)
    lifecycle = PlanLifecycleService(
        planner,
        InMemoryPlanRepository(),
        clock=lambda: NOW,
        id_factory=lambda: PlanId("plan-m9"),
    )
    return HouseholdReplenishmentService(household, lifecycle, clock=lambda: NOW), items


def test_replenishment_runs_household_history_to_persisted_plan() -> None:
    service, _ = make_replenishment_service()
    result = service.create(HouseholdReplenishmentRequest(Money(1000, "KGS"), 7))

    prep = result.preparation
    assert prep.state.quantity_for("milk") == Quantity("1100", "ml")
    assert prep.estimates[0].daily_quantity == Quantity("400.000000000000", "ml")
    assert prep.demand_compilation.demands[0].quantity == Quantity(
        "2800.000000000000", "ml"
    )
    assert prep.application_request.inventory[0].quantity == Quantity("1100", "ml")

    stored_result = result.plan_record.result.to_mapping()
    assert stored_result["status"] == "feasible"
    assert stored_result["total_cost"] == {"amount": "240", "currency": "KGS"}
    assert stored_result["purchases"][0]["packs"] == 2
    assert stored_result["projected_leftovers"] == [
        {"item_id": "milk", "quantity": {"amount": "300.000000000000", "unit": "ml"}}
    ]


def test_explicit_and_recurring_demands_are_compiled_together() -> None:
    service, _ = make_replenishment_service()
    result = service.create(
        HouseholdReplenishmentRequest(
            Money(1000, "KGS"),
            7,
            (RequestedItem("milk", Quantity("200", "ml")),),
        )
    )
    compilation = result.preparation.demand_compilation
    assert compilation.demands[0].quantity == Quantity("3000.000000000000", "ml")
    assert {c.source_id for c in compilation.contributions} == {
        "household:recurring",
        "request:explicit",
    }
    assert result.plan_record.result.to_mapping()["projected_leftovers"] == [
        {"item_id": "milk", "quantity": {"amount": "100.000000000000", "unit": "ml"}}
    ]


def test_unrelated_household_inventory_is_not_sent_to_market_application() -> None:
    catalog, provider, items = catalog_and_provider(include_oil=False)
    repo = InMemoryHouseholdEventRepository()
    household = HouseholdLearningService(repo)
    rice = Item("rice", "Rice", "grain")
    household.record(
        InventoryCorrection(
            HouseholdEventId("rice-count"),
            rice,
            Quantity("2", "kg"),
            BASE,
            BASE,
            "pantry count",
        )
    )
    planner = PlanApplicationService(catalog, (provider,), clock=lambda: NOW)
    lifecycle = PlanLifecycleService(
        planner,
        InMemoryPlanRepository(),
        clock=lambda: NOW,
        id_factory=lambda: PlanId("explicit-plan"),
    )
    service = HouseholdReplenishmentService(household, lifecycle, clock=lambda: NOW)

    result = service.create(
        HouseholdReplenishmentRequest(
            Money(1000, "KGS"),
            7,
            (RequestedItem("milk", Quantity("500", "ml")),),
        )
    )
    assert result.preparation.state.quantity_for("rice") == Quantity("2000", "g")
    assert result.preparation.application_request.inventory == ()
    assert result.plan_record.result.to_mapping()["total_cost"] == {
        "amount": "120",
        "currency": "KGS",
    }


def test_unknown_learned_item_fails_before_market_acquisition() -> None:
    catalog, _, items = catalog_and_provider(include_oil=False)
    rice = Item("rice", "Rice", "grain")
    household_repo = InMemoryHouseholdEventRepository()
    household = HouseholdLearningService(household_repo)
    household.record(
        PurchaseEvent(HouseholdEventId("rp"), rice, Quantity("2", "kg"), BASE, BASE)
    )
    household.record(
        ConsumptionObservation(
            HouseholdEventId("ru"),
            rice,
            Quantity("500", "g"),
            BASE,
            BASE + timedelta(days=1),
            BASE + timedelta(days=1),
        )
    )

    class CountingProvider:
        provider_id = "fixture"

        def __init__(self) -> None:
            self.calls = 0

        def acquire(self):
            self.calls += 1
            raise AssertionError("market must not be acquired")

    provider = CountingProvider()
    planner = PlanApplicationService(catalog, (provider,), clock=lambda: NOW)
    lifecycle = PlanLifecycleService(planner, InMemoryPlanRepository(), clock=lambda: NOW)
    service = HouseholdReplenishmentService(household, lifecycle, clock=lambda: NOW)

    with pytest.raises(Exception, match="not present in configured catalog"):
        service.create(HouseholdReplenishmentRequest(Money(1000, "KGS"), 7))
    assert provider.calls == 0


def test_empty_household_and_no_explicit_need_fails_before_market() -> None:
    service, _ = make_replenishment_service(
        household=HouseholdLearningService(InMemoryHouseholdEventRepository())
    )
    with pytest.raises(HouseholdReplenishmentRequestError, match="requires explicit"):
        service.create(HouseholdReplenishmentRequest(Money(1000, "KGS"), 7))


def test_explicit_need_order_is_canonical_and_duplicates_rejected() -> None:
    request = HouseholdReplenishmentRequest(
        Money(1000, "KGS"),
        7,
        (
            RequestedItem("oil", Quantity("1", "l")),
            RequestedItem("milk", Quantity("1", "l")),
        ),
    )
    assert [need.item_id for need in request.explicit_needs] == ["milk", "oil"]

    with pytest.raises(HouseholdReplenishmentRequestError, match="duplicate explicit"):
        HouseholdReplenishmentRequest(
            Money(1000, "KGS"),
            7,
            (
                RequestedItem("milk", Quantity("1", "l")),
                RequestedItem("milk", Quantity("1", "l")),
            ),
        )


def test_replenishment_rejects_naive_clock() -> None:
    service, _ = make_replenishment_service()
    broken = HouseholdReplenishmentService(
        service.household,
        service.plans,
        clock=lambda: NOW.replace(tzinfo=None),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        broken.prepare(HouseholdReplenishmentRequest(Money(1000, "KGS"), 7))


def test_preparation_is_self_validating() -> None:
    service, _ = make_replenishment_service()
    prep = service.prepare(HouseholdReplenishmentRequest(Money(1000, "KGS"), 7))
    with pytest.raises(ValueError, match="state does not match"):
        HouseholdReplenishmentPreparation(
            request=prep.request,
            catalog=prep.catalog,
            history=prep.history,
            as_of=prep.as_of,
            state=type(prep.state)(prep.state.as_of, (), prep.state.applied_event_ids),
            estimates=prep.estimates,
            demand_compilation=prep.demand_compilation,
            application_request=prep.application_request,
        )


def test_result_rejects_plan_record_from_different_request() -> None:
    service, _ = make_replenishment_service()
    prep = service.prepare(HouseholdReplenishmentRequest(Money(1000, "KGS"), 7))
    other = service.plans.create(
        type(prep.application_request)(
            demands=(RequestedItem("milk", Quantity("100", "ml")),),
            inventory=(),
            budget=Money(1000, "KGS"),
        )
    )
    with pytest.raises(ValueError, match="does not match"):
        HouseholdReplenishmentResult(prep, other)

from household_supply.application import PlanAsgiApp
from household_supply.application.replenishment_api import (
    HouseholdReplenishmentJsonApi,
    parse_household_replenishment_payload,
)


def test_replenishment_json_api_creates_explainable_persisted_plan() -> None:
    service, _ = make_replenishment_service()
    api = HouseholdReplenishmentJsonApi(service)
    response = api.handle(
        "POST",
        "/plans",
        {"budget": {"amount": "1000", "currency": "KGS"}, "horizon_days": "7"},
    )
    assert response.status == 201
    assert response.body["plan"]["plan_id"] == "plan-m9"
    assert response.body["household"]["state"]["balances"] == [
        {"item_id": "milk", "quantity": {"amount": "1100", "unit": "ml"}}
    ]
    assert response.body["household"]["demand"]["demands"] == [
        {
            "item_id": "milk",
            "quantity": {"amount": "2800.000000000000", "unit": "ml"},
        }
    ]
    assert response.body["plan"]["result"]["total_cost"] == {
        "amount": "240",
        "currency": "KGS",
    }

    stored = api.handle("GET", "/plans/plan-m9")
    assert stored.status == 200
    assert stored.body["plan_id"] == "plan-m9"


def test_replenishment_json_contract_is_strict_and_exact() -> None:
    with pytest.raises(Exception, match="float is not accepted"):
        parse_household_replenishment_payload(
            {
                "budget": {"amount": "1000", "currency": "KGS"},
                "horizon_days": 7.5,
            }
        )
    with pytest.raises(Exception, match="unknown fields"):
        parse_household_replenishment_payload(
            {
                "budget": {"amount": "1000", "currency": "KGS"},
                "horizon_days": "7",
                "inventory": [],
            }
        )


def test_replenishment_api_rejects_raw_application_contract() -> None:
    service, _ = make_replenishment_service()
    response = HouseholdReplenishmentJsonApi(service).handle(
        "POST",
        "/plans",
        {
            "budget": {"amount": "1000", "currency": "KGS"},
            "demands": [
                {"item_id": "milk", "quantity": {"amount": "1", "unit": "l"}}
            ],
        },
    )
    assert response.status == 422
    assert response.body["error"] == "invalid_request"


def test_replenishment_api_maps_ambiguous_household_history_to_conflict() -> None:
    _, _, items = catalog_and_provider()
    milk = items["milk"]
    repo = InMemoryHouseholdEventRepository()
    household = HouseholdLearningService(repo)
    household.record(PurchaseEvent(HouseholdEventId("p"), milk, Quantity("2", "l"), BASE, BASE))
    household.record(
        ConsumptionObservation(
            HouseholdEventId("a"),
            milk,
            Quantity("100", "ml"),
            BASE,
            BASE + timedelta(days=2),
            BASE + timedelta(days=2),
        )
    )
    household.record(
        ConsumptionObservation(
            HouseholdEventId("b"),
            milk,
            Quantity("100", "ml"),
            BASE + timedelta(days=1),
            BASE + timedelta(days=3),
            BASE + timedelta(days=3),
        )
    )
    service, _ = make_replenishment_service(household=household)
    response = HouseholdReplenishmentJsonApi(service).handle(
        "POST",
        "/plans",
        {"budget": {"amount": "1000", "currency": "KGS"}, "horizon_days": "7"},
    )
    assert response.status == 409
    assert response.body["error"] == "household_state_conflict"


async def _asgi_request(app, payload: bytes):
    sent = []
    messages = iter(
        [
            {
                "type": "http.request",
                "body": payload,
                "more_body": False,
            }
        ]
    )

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http",
            "method": "POST",
            "path": "/plans",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        },
        receive,
        send,
    )
    return sent


def test_replenishment_api_runs_through_existing_asgi_adapter() -> None:
    import asyncio
    import json

    service, _ = make_replenishment_service()
    app = PlanAsgiApp(HouseholdReplenishmentJsonApi(service))
    sent = asyncio.run(
        _asgi_request(
            app,
            json.dumps(
                {
                    "budget": {"amount": "1000", "currency": "KGS"},
                    "horizon_days": "7",
                }
            ).encode("utf-8"),
        )
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    body = next(message for message in sent if message["type"] == "http.response.body")
    assert start["status"] == 201
    assert json.loads(body["body"])["plan"]["plan_id"] == "plan-m9"
