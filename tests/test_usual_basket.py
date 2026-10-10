from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from household_supply.application import (
    FileUsualBasketRepository,
    InMemoryUsualBasketRepository,
    UsualBasket,
    UsualBasketError,
    UsualBasketItem,
    UsualBasketPreparationService,
    RequestedItem,
    compose_usual_basket,
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
    admitted_recurring_estimates,
    project_household_state,
)
from household_supply.market import StaticMarketProvider
from household_supply.application import PlanApplicationService


NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)
BASE = NOW - timedelta(days=5)


def environment():
    milk = Item("milk", "Milk", "dairy")
    rice = Item("rice", "Rice", "pantry")
    oil = Item("oil", "Oil", "pantry")
    entries = (
        (milk, SKU("milk-1l", milk, "Milk 1L", Quantity(1, "l")), "120"),
        (rice, SKU("rice-1kg", rice, "Rice 1kg", Quantity(1, "kg")), "90"),
        (oil, SKU("oil-1l", oil, "Oil 1L", Quantity(1, "l")), "190"),
    )
    catalog = CatalogSnapshot(
        tuple(sku for _, sku, _ in entries),
        tuple(
            CatalogBinding(ExternalListingKey("fixture", "store-a", sku.id), sku.id, "fixture")
            for _, sku, _ in entries
        ),
    )
    observations = tuple(
        MarketObservation(
            id=f"obs-{item.id}", provider_id="fixture", seller_id="store-a",
            external_product_id=sku.id, price=Money(price, "KGS"),
            observed_at=NOW, package_quantity=sku.package_quantity,
            source_ref=f"fixture://{item.id}",
        )
        for item, sku, price in entries
    )
    provider = StaticMarketProvider(MarketAcquisitionBatch("fixture", NOW, observations))
    household = HouseholdLearningService(InMemoryHouseholdEventRepository())
    household.record(
        PurchaseEvent(HouseholdEventId("p-milk"), milk, Quantity("3", "l"), BASE, BASE)
    )
    household.record(
        ConsumptionObservation(
            HouseholdEventId("use-milk"), milk, Quantity("1", "l"),
            BASE, BASE + timedelta(days=2), BASE + timedelta(days=2),
        )
    )
    household.record(
        InventoryCorrection(
            HouseholdEventId("count-rice"), rice, Quantity("2", "kg"),
            BASE, BASE, "initial count",
        )
    )
    return catalog, provider, household


def preparation(basket, *, overrides=(), exclusions=(), household=None):
    catalog, provider, default_household = environment()
    household = household or default_household
    history = household.history()
    proposal = compose_usual_basket(
        basket=basket,
        catalog=catalog,
        state=project_household_state(history, as_of=NOW),
        admitted_estimates=admitted_recurring_estimates(history, as_of=NOW),
        budget=Money("1000", "KGS"),
        horizon_days=7,
        overrides=overrides,
        exclusions=exclusions,
    )
    return proposal, catalog, provider


def test_preference_identity_and_order_and_validation():
    basket = UsualBasket((
        UsualBasketItem("rice", Quantity("1", "kg")),
        UsualBasketItem("milk"),
    ))
    assert [i.item_id for i in basket.items] == ["milk", "rice"]
    with pytest.raises(UsualBasketError, match="duplicate"):
        UsualBasket((UsualBasketItem("milk"), UsualBasketItem("milk")))
    with pytest.raises(UsualBasketError, match="positive"):
        UsualBasketItem("milk", Quantity("0", "l"))


def test_existing_inventory_recurring_and_fallback_reach_existing_planner():
    basket = UsualBasket((
        UsualBasketItem("milk", Quantity("1", "l")),
        UsualBasketItem("rice", Quantity("1", "kg")),
    ))
    result, catalog, provider = preparation(basket)
    assert result.ready
    assert result.needs_clarification == ()
    assert [(c.item_id, c.basis) for c in result.choices] == [
        ("milk", "recurring"), ("rice", "fallback")
    ]
    assert [(c.source_id, c.item.id) for c in result.compilation.contributions] == [
        ("household:recurring", "milk"),
        ("routine:fallback", "rice"),
    ]
    assert [(d.item_id, d.quantity.as_base().amount) for d in result.application_request.demands] == [
        ("milk", Decimal("3500")),
        ("rice", Decimal("1000")),
    ]
    assert {i.item_id for i in result.application_request.inventory} == {"milk", "rice"}
    plan = PlanApplicationService(catalog, (provider,), clock=lambda: NOW).plan(
        result.application_request
    ).plan
    assert plan.total_cost.amount == Decimal("240")
    # Milk needs 3500 ml with 2000 ml already on hand; rice is covered in inventory.
    assert sum(p.packs for p in plan.purchases) == 2


def test_one_off_override_replaces_learned_rate_without_double_counting():
    basket = UsualBasket((UsualBasketItem("milk", Quantity("1", "l")),))
    result, _, _ = preparation(
        basket, overrides=(RequestedItem("milk", Quantity("500", "ml")),)
    )
    assert result.ready
    assert len(result.compilation.contributions) == 1
    assert result.compilation.contributions[0].source_id == "request:override"
    assert result.application_request.demands[0].quantity == Quantity("500", "ml")


def test_missing_quantity_blocks_candidate_instead_of_silent_skips():
    result, _, _ = preparation(UsualBasket((UsualBasketItem("oil"),)))
    assert not result.ready
    assert result.application_request is None
    assert result.needs_clarification == ("oil",)


def test_exclusions_are_one_off_and_do_not_edit_saved_preference():
    basket = UsualBasket((
        UsualBasketItem("milk"), UsualBasketItem("rice", Quantity("1", "kg")),
    ))
    result, _, _ = preparation(basket, exclusions=("milk",))
    assert result.ready
    assert result.application_request.demands[0].item_id == "rice"
    assert basket.items[0].item_id == "milk"
    with pytest.raises(UsualBasketError, match="outside"):
        preparation(basket, exclusions=("oil",))


def test_new_household_fallback_and_one_off_added_item():
    catalog, _, _ = environment()
    history = HouseholdLearningService(InMemoryHouseholdEventRepository()).history()
    state = project_household_state(history, as_of=NOW)
    result = compose_usual_basket(
        basket=UsualBasket((UsualBasketItem("rice", Quantity("500", "g")),)),
        catalog=catalog, state=state, admitted_estimates=(),
        budget=Money(100, "KGS"), horizon_days=7,
        overrides=(RequestedItem("oil", Quantity("1", "l")),),
    )
    assert result.ready
    assert [d.item_id for d in result.application_request.demands] == ["oil", "rice"]


def test_fail_closed_invalid_units_and_unknown_catalog_items():
    with pytest.raises(UsualBasketError, match="incompatible"):
        preparation(UsualBasket((UsualBasketItem("milk", Quantity("1", "kg")),)))
    with pytest.raises(Exception, match="not in catalog"):
        preparation(UsualBasket((UsualBasketItem("mystery", Quantity(1, "kg")),)))


def test_file_repository_round_trip_and_strict_invalid_format(tmp_path):
    path = tmp_path / "profile" / "usual-basket.json"
    repo = FileUsualBasketRepository(path)
    assert repo.load() == UsualBasket()
    basket = UsualBasket((
        UsualBasketItem("rice", Quantity("2", "kg")), UsualBasketItem("milk")
    ))
    repo.save(basket)
    assert FileUsualBasketRepository(path).load() == basket
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    repo.clear()
    assert repo.load() == UsualBasket()

    path.write_text('{"schema_version":1,"items":[{"item_id":"milk","fallback_quantity":{"amount":"NaN","unit":"l"}}]}', encoding="utf-8")
    with pytest.raises(Exception, match="corrupt"):
        repo.load()
    assert path.exists()


def test_preparation_service_reads_evidence_and_does_not_mutate_events():
    catalog, _, household = environment()
    basket = UsualBasket((UsualBasketItem("rice", Quantity("1", "kg")),))
    repository = InMemoryUsualBasketRepository(basket)
    service = UsualBasketPreparationService(
        household, catalog, repository, clock=lambda: NOW,
    )
    before = household.history()
    result = service.prepare(budget=Money(1000, "KGS"), horizon_days=7)
    assert result.ready
    assert [d.item_id for d in result.application_request.demands] == ["rice"]
    assert household.history() == before
    assert repository.load() == basket


def test_preference_alone_never_generates_demand_or_inventory_events():
    basket = UsualBasket((UsualBasketItem("milk"),))
    catalog, provider, household = environment()
    before = household.history()
    proposal = UsualBasketPreparationService(
        household, catalog, InMemoryUsualBasketRepository(basket),
        clock=lambda: NOW,
    ).prepare(budget=Money("1000", "KGS"), horizon_days=7)
    # The admitted rate is the reason, not membership in the usual basket.
    assert proposal.ready
    assert proposal.choices[0].basis == "recurring"
    assert household.history() == before

    empty_household = HouseholdLearningService(InMemoryHouseholdEventRepository())
    no_rate = UsualBasketPreparationService(
        empty_household, catalog, InMemoryUsualBasketRepository(basket),
        clock=lambda: NOW,
    ).prepare(budget=Money("1000", "KGS"), horizon_days=7)
    assert not no_rate.ready
    assert no_rate.needs_clarification == ("milk",)
    assert empty_household.history().events == ()


def test_partial_clarification_blocks_even_when_other_item_is_ready():
    result, _, _ = preparation(
        UsualBasket((
            UsualBasketItem("milk", Quantity("1", "l")),
            UsualBasketItem("oil"),
        ))
    )
    assert result.needs_clarification == ("oil",)
    assert not result.ready
    assert result.application_request is None


def test_same_preferences_and_evidence_produce_equal_candidates():
    basket = UsualBasket((
        UsualBasketItem("rice", Quantity("1", "kg")),
        UsualBasketItem("milk", Quantity("1", "l")),
    ))
    result_a, _, _ = preparation(basket)
    result_b, _, _ = preparation(basket)
    assert result_a == result_b


def test_file_repository_rejects_duplicate_preference_identity(tmp_path):
    path = tmp_path / "usual.json"
    path.write_text(
        '{"schema_version":1,"items":[{"item_id":"milk","fallback_quantity":null},'
        '{"item_id":"milk","fallback_quantity":null}]}',
        encoding="utf-8",
    )
    with pytest.raises(Exception, match="corrupt"):
        FileUsualBasketRepository(path).load()

def test_exclusion_wins_over_stale_incompatible_fallback():
    basket = UsualBasket((
        UsualBasketItem("milk", Quantity("1", "kg")),
        UsualBasketItem("rice", Quantity("1", "kg")),
    ))
    candidate, _, _ = preparation(basket, exclusions=("milk",))
    assert candidate.ready
    assert [d.item_id for d in candidate.application_request.demands] == ["rice"]
    assert [(c.item_id, c.basis) for c in candidate.choices] == [
        ("milk", "excluded"), ("rice", "fallback")
    ]


def test_exclusion_of_removed_catalog_item_does_not_block_other_items():
    basket = UsualBasket((
        UsualBasketItem("removed", Quantity("1", "piece")),
        UsualBasketItem("rice", Quantity("1", "kg")),
    ))
    candidate, _, _ = preparation(basket, exclusions=("removed",))
    assert candidate.ready
    assert candidate.application_request.demands[0].item_id == "rice"
