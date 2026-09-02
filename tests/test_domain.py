from __future__ import annotations

from decimal import Decimal

import pytest

from household_supply.domain import CurrencyMismatchError, Money, Quantity


def test_money_uses_exact_decimal_representation() -> None:
    assert (Money("0.1", "KGS") + Money("0.2", "KGS")).amount == Decimal("0.3")


def test_money_rejects_cross_currency_arithmetic() -> None:
    with pytest.raises(CurrencyMismatchError):
        _ = Money(1, "KGS") + Money(1, "USD")


def test_quantity_converts_mass_exactly() -> None:
    assert Quantity("1.25", "kg").to("g") == Quantity("1250", "g")


def test_quantity_rejects_cross_dimension_conversion() -> None:
    with pytest.raises(ValueError, match="incompatible quantity units"):
        Quantity(1, "kg").to("l")


def test_inventory_lot_with_sku_requires_compatible_quantity_dimension() -> None:
    from household_supply.domain import InventoryLot, Item, SKU

    milk = Item("milk", "Milk")
    milk_sku = SKU("milk-1l", milk, "Milk 1L", Quantity(1, "l"))

    with pytest.raises(ValueError, match="compatible with sku package"):
        InventoryLot("milk-home", milk, Quantity(500, "g"), sku=milk_sku)


def test_inventory_snapshot_rejects_duplicate_lot_ids() -> None:
    from household_supply.domain import InventoryLot, InventorySnapshot, Item

    rice = Item("rice", "Rice")
    lot_a = InventoryLot("same", rice, Quantity(100, "g"))
    lot_b = InventoryLot("same", rice, Quantity(200, "g"))

    with pytest.raises(ValueError, match="duplicate lot ids"):
        InventorySnapshot((lot_a, lot_b))


def test_market_snapshot_rejects_duplicate_offer_ids() -> None:
    from datetime import UTC, datetime
    from household_supply.domain import Item, MarketSnapshot, Offer, SKU

    now = datetime(2026, 9, 2, tzinfo=UTC)
    rice = Item("rice", "Rice")
    rice_sku = SKU("rice-1kg", rice, "Rice 1kg", Quantity(1, "kg"))
    offer_a = Offer("same", rice_sku, "a", Money(100, "KGS"), now, "fixture")
    offer_b = Offer("same", rice_sku, "b", Money(90, "KGS"), now, "fixture")

    with pytest.raises(ValueError, match="duplicate offer ids"):
        MarketSnapshot(now, (offer_a, offer_b))


def test_market_snapshot_rejects_future_offer_observation() -> None:
    from datetime import UTC, datetime, timedelta
    from household_supply.domain import Item, MarketSnapshot, Offer, SKU

    now = datetime(2026, 9, 2, tzinfo=UTC)
    rice = Item("rice", "Rice")
    rice_sku = SKU("rice-1kg", rice, "Rice 1kg", Quantity(1, "kg"))
    future_offer = Offer(
        "future",
        rice_sku,
        "store",
        Money(100, "KGS"),
        now + timedelta(minutes=1),
        "fixture",
    )

    with pytest.raises(ValueError, match="later than market snapshot"):
        MarketSnapshot(now, (future_offer,))


def test_decimal_domain_values_reject_float_inputs() -> None:
    with pytest.raises(TypeError, match="float is not accepted"):
        Money(0.1, "KGS")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="float is not accepted"):
        Quantity(0.1, "kg")  # type: ignore[arg-type]


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_decimal_domain_values_reject_non_finite_inputs(value: str) -> None:
    with pytest.raises(ValueError, match="must be finite"):
        Money(value, "KGS")
    with pytest.raises(ValueError, match="must be finite"):
        Quantity(value, "g")


def test_quantity_addition_is_exact_beyond_decimal_context_precision() -> None:
    a = Quantity("1", "g")
    b = Quantity("0.3333333333333333333333333333", "g")
    c = Quantity("0.3333333333333333333333333333", "g")

    assert (a + b) + c == a + (b + c)
    assert (a + b) + c == Quantity("1.6666666666666666666666666666", "g")


def test_money_arithmetic_is_independent_of_ambient_decimal_context() -> None:
    from decimal import localcontext

    left = Money("123456789.123456789", "KGS")
    right = Money("0.000000001", "KGS")

    observed = []
    for precision in (6, 12, 28, 50):
        with localcontext() as context:
            context.prec = precision
            observed.append(
                (
                    left + right,
                    left - right,
                    left * 3,
                )
            )

    assert observed == [
        (
            Money("123456789.123456790", "KGS"),
            Money("123456789.123456788", "KGS"),
            Money("370370367.370370367", "KGS"),
        )
    ] * 4


def test_domain_collection_inputs_are_frozen_as_tuples() -> None:
    from datetime import UTC, datetime

    from household_supply import (
        Demand,
        InventoryLot,
        InventorySnapshot,
        Item,
        MarketSnapshot,
        Offer,
        PlanningPolicy,
        PlanningProblem,
        ProcurementPlan,
        PlanStatus,
        Quantity,
        SKU,
    )

    now = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)
    rice = Item("rice", "Rice", aliases=["grain"])
    sku = SKU("rice-1kg", rice, "Rice 1kg", Quantity(1, "kg"))
    offer = Offer("offer-rice", sku, "store-a", Money(100, "KGS"), now, "fixture")
    lot = InventoryLot("lot-rice", rice, Quantity(100, "g"))

    aliases = ["grain"]
    alias_item = Item("rice-2", "Rice 2", aliases=aliases)
    aliases.append("mutated")
    assert alias_item.aliases == ("grain",)

    lots = [lot]
    inventory = InventorySnapshot(lots)
    lots.clear()
    assert inventory.lots == (lot,)

    offers = [offer]
    market = MarketSnapshot(now, offers)
    offers.clear()
    assert market.offers == (offer,)

    demands = [Demand(rice, Quantity(500, "g"))]
    problem = PlanningProblem(
        demands,
        inventory,
        market,
        PlanningPolicy(Money(1000, "KGS")),
    )
    demands.clear()
    assert len(problem.demands) == 1

    purchases = []
    reasons = ["reason"]
    plan = ProcurementPlan(
        status=PlanStatus.INFEASIBLE,
        purchases=purchases,
        requirement_coverage=[],
        projected_leftovers=[],
        total_cost=Money(0, "KGS"),
        budget_remaining=Money(1000, "KGS"),
        infeasibility_reasons=reasons,
    )
    purchases.append("mutated")
    reasons.append("mutated")
    assert plan.purchases == ()
    assert plan.infeasibility_reasons == ("reason",)
