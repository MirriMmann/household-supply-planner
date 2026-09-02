from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext

import pytest

from household_supply.demand import compile_demand_sources
from household_supply.domain import Item, Quantity
from household_supply.household import (
    ConsumptionEstimationError,
    ConsumptionObservation,
    HouseholdEventId,
    HouseholdHistory,
    HouseholdProjectionError,
    InventoryCorrection,
    PurchaseEvent,
    RecurringNeedSource,
    estimate_all_consumption,
    estimate_consumption,
    project_household_state,
)


UTC = timezone.utc
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


@pytest.fixture
def milk() -> Item:
    return Item("milk", "Milk", "dairy")


@pytest.fixture
def rice() -> Item:
    return Item("rice", "Rice", "grain")


def purchase(
    event_id: str,
    item: Item,
    amount: str,
    unit: str,
    *,
    at: datetime,
    recorded_at: datetime | None = None,
) -> PurchaseEvent:
    return PurchaseEvent(
        event_id=HouseholdEventId(event_id),
        item=item,
        quantity=Quantity(amount, unit),
        occurred_at=at,
        recorded_at=recorded_at or at,
    )


def consumption(
    event_id: str,
    item: Item,
    amount: str,
    unit: str,
    *,
    start: datetime,
    end: datetime,
    recorded_at: datetime | None = None,
) -> ConsumptionObservation:
    return ConsumptionObservation(
        event_id=HouseholdEventId(event_id),
        item=item,
        quantity_consumed=Quantity(amount, unit),
        period_start=start,
        period_end=end,
        recorded_at=recorded_at or end,
    )


def test_event_id_is_path_safe() -> None:
    assert str(HouseholdEventId("milk_2026-09-01")) == "milk_2026-09-01"
    for invalid in ("", "../escape", "A", "space here", "x/y", "-leading"):
        with pytest.raises(ValueError):
            HouseholdEventId(invalid)


def test_purchase_requires_positive_quantity(milk: Item) -> None:
    with pytest.raises(ValueError, match="positive"):
        purchase("p1", milk, "0", "ml", at=BASE)


def test_purchase_cannot_be_recorded_before_it_happened(milk: Item) -> None:
    with pytest.raises(ValueError, match="recorded_at"):
        purchase(
            "p1",
            milk,
            "1",
            "l",
            at=BASE,
            recorded_at=BASE - timedelta(seconds=1),
        )


def test_correction_requires_reason(milk: Item) -> None:
    with pytest.raises(ValueError, match="reason"):
        InventoryCorrection(
            HouseholdEventId("c1"), milk, Quantity("1", "l"), BASE, BASE, " "
        )


def test_consumption_requires_positive_nonempty_interval(milk: Item) -> None:
    with pytest.raises(ValueError, match="period_end"):
        consumption("c1", milk, "100", "ml", start=BASE, end=BASE)
    with pytest.raises(ValueError, match="positive"):
        consumption(
            "c2",
            milk,
            "0",
            "ml",
            start=BASE,
            end=BASE + timedelta(days=1),
        )


def test_consumption_cannot_be_recorded_before_period_end(milk: Item) -> None:
    with pytest.raises(ValueError, match="recorded_at"):
        consumption(
            "c1",
            milk,
            "100",
            "ml",
            start=BASE,
            end=BASE + timedelta(days=1),
            recorded_at=BASE + timedelta(hours=12),
        )


def test_history_rejects_duplicate_event_ids(milk: Item) -> None:
    first = purchase("same", milk, "1", "l", at=BASE)
    second = purchase("same", milk, "2", "l", at=BASE + timedelta(days=1))
    with pytest.raises(ValueError, match="duplicate"):
        HouseholdHistory((first, second))



def test_history_through_respects_when_fact_became_known(milk: Item) -> None:
    event = purchase(
        "late-purchase",
        milk,
        "1",
        "l",
        at=BASE,
        recorded_at=BASE + timedelta(days=2),
    )
    history = HouseholdHistory((event,))
    assert history.through(BASE + timedelta(days=1)).events == ()
    assert history.through(BASE + timedelta(days=3)).events == (event,)


def test_correction_at_consumption_interval_boundary_is_unambiguous(milk: Item) -> None:
    boundary = BASE + timedelta(days=1)
    history = HouseholdHistory(
        (
            InventoryCorrection(
                HouseholdEventId("count-start"),
                milk,
                Quantity("1000", "ml"),
                BASE,
                BASE,
                "starting count",
            ),
            consumption(
                "use1",
                milk,
                "200",
                "ml",
                start=BASE,
                end=boundary,
            ),
            InventoryCorrection(
                HouseholdEventId("count-end"),
                milk,
                Quantity("850", "ml"),
                boundary,
                boundary,
                "ending count",
            ),
        )
    )
    assert project_household_state(history, as_of=boundary).quantity_for("milk") == Quantity(
        "850", "ml"
    )

def test_projection_purchase_consumption_and_correction(milk: Item) -> None:
    history = HouseholdHistory(
        (
            purchase("p1", milk, "2", "l", at=BASE),
            consumption(
                "use1",
                milk,
                "500",
                "ml",
                start=BASE,
                end=BASE + timedelta(days=1),
            ),
            InventoryCorrection(
                HouseholdEventId("count1"),
                milk,
                Quantity("1400", "ml"),
                BASE + timedelta(days=2),
                BASE + timedelta(days=2),
                "manual fridge count",
            ),
            consumption(
                "use2",
                milk,
                "300",
                "ml",
                start=BASE + timedelta(days=2),
                end=BASE + timedelta(days=3),
            ),
        )
    )
    state = project_household_state(history, as_of=BASE + timedelta(days=4))
    assert state.quantity_for("milk") == Quantity("1100", "ml")
    assert tuple(lot.id for lot in state.inventory_snapshot().lots) == ("household:milk",)


def test_correction_at_same_timestamp_is_applied_after_consumption(milk: Item) -> None:
    at = BASE + timedelta(days=1)
    history = HouseholdHistory(
        (
            purchase("p1", milk, "1", "l", at=BASE),
            consumption("use1", milk, "200", "ml", start=BASE, end=at),
            InventoryCorrection(
                HouseholdEventId("count1"), milk, Quantity("900", "ml"), at, at, "count"
            ),
        )
    )
    assert project_household_state(history, as_of=at).quantity_for("milk") == Quantity(
        "900", "ml"
    )


def test_multiple_corrections_at_same_item_timestamp_are_ambiguous(milk: Item) -> None:
    at = BASE + timedelta(days=1)
    history = HouseholdHistory(
        (
            InventoryCorrection(
                HouseholdEventId("count1"), milk, Quantity("900", "ml"), at, at, "count a"
            ),
            InventoryCorrection(
                HouseholdEventId("count2"), milk, Quantity("800", "ml"), at, at, "count b"
            ),
        )
    )
    with pytest.raises(HouseholdProjectionError, match="multiple inventory corrections"):
        project_household_state(history, as_of=at)


def test_late_recorded_correction_does_not_rewrite_earlier_knowledge(milk: Item) -> None:
    occurred = BASE + timedelta(days=1)
    recorded = BASE + timedelta(days=3)
    history = HouseholdHistory(
        (
            purchase("p1", milk, "1", "l", at=BASE),
            InventoryCorrection(
                HouseholdEventId("late-count"),
                milk,
                Quantity("400", "ml"),
                occurred,
                recorded,
                "late manual count",
            ),
        )
    )
    before_record = project_household_state(
        history, as_of=BASE + timedelta(days=2)
    )
    after_record = project_household_state(
        history, as_of=BASE + timedelta(days=4)
    )
    assert before_record.quantity_for("milk") == Quantity("1000", "ml")
    assert after_record.quantity_for("milk") == Quantity("400", "ml")


def test_projection_rejects_consumption_without_inventory_basis(milk: Item) -> None:
    history = HouseholdHistory(
        (
            consumption(
                "use1",
                milk,
                "100",
                "ml",
                start=BASE,
                end=BASE + timedelta(days=1),
            ),
        )
    )
    with pytest.raises(HouseholdProjectionError, match="no tracked inventory"):
        project_household_state(history, as_of=BASE + timedelta(days=2))


def test_projection_rejects_consumption_above_tracked_balance(milk: Item) -> None:
    history = HouseholdHistory(
        (
            purchase("p1", milk, "100", "ml", at=BASE),
            consumption(
                "use1",
                milk,
                "101",
                "ml",
                start=BASE,
                end=BASE + timedelta(days=1),
            ),
        )
    )
    with pytest.raises(HouseholdProjectionError, match="exceeds tracked inventory"):
        project_household_state(history, as_of=BASE + timedelta(days=2))


def test_projection_rejects_overlapping_consumption(milk: Item) -> None:
    history = HouseholdHistory(
        (
            purchase("p1", milk, "2", "l", at=BASE),
            consumption(
                "use1",
                milk,
                "200",
                "ml",
                start=BASE,
                end=BASE + timedelta(days=2),
            ),
            consumption(
                "use2",
                milk,
                "200",
                "ml",
                start=BASE + timedelta(days=1),
                end=BASE + timedelta(days=3),
            ),
        )
    )
    with pytest.raises(HouseholdProjectionError, match="overlapping"):
        project_household_state(history, as_of=BASE + timedelta(days=4))



def test_projection_rejects_correction_inside_consumption_interval(milk: Item) -> None:
    history = HouseholdHistory(
        (
            purchase("p1", milk, "2", "l", at=BASE),
            InventoryCorrection(
                HouseholdEventId("count-mid"),
                milk,
                Quantity("1500", "ml"),
                BASE + timedelta(days=1),
                BASE + timedelta(days=1),
                "mid-window count",
            ),
            consumption(
                "use-window",
                milk,
                "400",
                "ml",
                start=BASE,
                end=BASE + timedelta(days=2),
            ),
        )
    )
    with pytest.raises(HouseholdProjectionError, match="splits a consumption"):
        project_household_state(history, as_of=BASE + timedelta(days=3))

def test_projection_rejects_conflicting_item_identity(milk: Item) -> None:
    other_milk = Item("milk", "Different Milk", "dairy")
    history = HouseholdHistory(
        (
            purchase("p1", milk, "1", "l", at=BASE),
            purchase("p2", other_milk, "1", "l", at=BASE + timedelta(days=1)),
        )
    )
    with pytest.raises(HouseholdProjectionError, match="conflicting"):
        project_household_state(history, as_of=BASE + timedelta(days=2))


def test_zero_corrected_balance_is_not_exported_as_inventory_lot(milk: Item) -> None:
    history = HouseholdHistory(
        (
            InventoryCorrection(
                HouseholdEventId("count"), milk, Quantity("0", "ml"), BASE, BASE, "empty"
            ),
        )
    )
    state = project_household_state(history, as_of=BASE)
    assert state.quantity_for("milk") == Quantity("0", "ml")
    assert state.inventory_snapshot().lots == ()


def test_weighted_consumption_estimate(milk: Item) -> None:
    # 300 ml / 1 day and 1200 ml / 3 days => weighted 375 ml/day.
    history = HouseholdHistory(
        (
            consumption(
                "c1",
                milk,
                "300",
                "ml",
                start=BASE,
                end=BASE + timedelta(days=1),
            ),
            consumption(
                "c2",
                milk,
                "1200",
                "ml",
                start=BASE + timedelta(days=1),
                end=BASE + timedelta(days=4),
            ),
        )
    )
    estimate = estimate_consumption(history, "milk")
    assert estimate is not None
    assert estimate.daily_quantity == Quantity("375.000000000000", "ml")
    assert estimate.daily_min == Quantity("300.000000000000", "ml")
    assert estimate.daily_max == Quantity("400.000000000000", "ml")
    assert estimate.uncertainty == Quantity("100.000000000000", "ml")
    assert estimate.sample_count == 2
    assert estimate.observed_days == Decimal("4.000000000000")
    assert estimate.total_consumed == Quantity("1500", "ml")
    assert estimate.observed_microseconds == 4 * 86_400_000_000


def test_estimate_is_deterministic_for_fractional_day(milk: Item) -> None:
    history = HouseholdHistory(
        (
            consumption(
                "c1",
                milk,
                "100",
                "ml",
                start=BASE,
                end=BASE + timedelta(hours=6),
            ),
        )
    )
    estimate = estimate_consumption(history, "milk")
    assert estimate is not None
    assert estimate.daily_quantity == Quantity("400.000000000000", "ml")
    assert estimate.observed_days == Decimal("0.250000000000")


def test_estimate_is_independent_of_decimal_context(milk: Item) -> None:
    history = HouseholdHistory(
        (
            consumption(
                "c1",
                milk,
                "1",
                "ml",
                start=BASE,
                end=BASE + timedelta(hours=7),
            ),
        )
    )
    values = []
    for precision in (6, 12, 28, 50):
        with localcontext() as context:
            context.prec = precision
            estimate = estimate_consumption(history, "milk")
            assert estimate is not None
            values.append(estimate.daily_quantity.amount)
    assert len(set(values)) == 1
    assert values[0] == Decimal("3.428571428571")


def test_estimate_as_of_uses_only_known_observations(milk: Item) -> None:
    first_end = BASE + timedelta(days=1)
    second_end = BASE + timedelta(days=2)
    history = HouseholdHistory(
        (
            consumption("c1", milk, "100", "ml", start=BASE, end=first_end),
            consumption(
                "c2",
                milk,
                "300",
                "ml",
                start=first_end,
                end=second_end,
                recorded_at=BASE + timedelta(days=4),
            ),
        )
    )
    early = estimate_consumption(history, "milk", as_of=BASE + timedelta(days=3))
    late = estimate_consumption(history, "milk", as_of=BASE + timedelta(days=5))
    assert early is not None and early.sample_count == 1
    assert early.daily_quantity == Quantity("100.000000000000", "ml")
    assert late is not None and late.sample_count == 2
    assert late.daily_quantity == Quantity("200.000000000000", "ml")


def test_estimate_rejects_overlapping_intervals(milk: Item) -> None:
    history = HouseholdHistory(
        (
            consumption(
                "c1", milk, "100", "ml", start=BASE, end=BASE + timedelta(days=2)
            ),
            consumption(
                "c2",
                milk,
                "100",
                "ml",
                start=BASE + timedelta(days=1),
                end=BASE + timedelta(days=3),
            ),
        )
    )
    with pytest.raises(ConsumptionEstimationError, match="overlapping"):
        estimate_consumption(history, "milk")


def test_estimate_all_is_sorted_by_item_id(milk: Item, rice: Item) -> None:
    history = HouseholdHistory(
        (
            consumption(
                "r1", rice, "100", "g", start=BASE, end=BASE + timedelta(days=1)
            ),
            consumption(
                "m1", milk, "200", "ml", start=BASE, end=BASE + timedelta(days=1)
            ),
        )
    )
    assert tuple(estimate.item.id for estimate in estimate_all_consumption(history)) == (
        "milk",
        "rice",
    )


def test_recurring_need_source_compiles_through_m2(milk: Item) -> None:
    history = HouseholdHistory(
        (
            consumption(
                "c1", milk, "250", "ml", start=BASE, end=BASE + timedelta(days=1)
            ),
        )
    )
    estimate = estimate_consumption(history, "milk")
    assert estimate is not None
    source = RecurringNeedSource("weekly", "7", (estimate,))
    compilation = compile_demand_sources((source,))
    assert compilation.demands[0].item == milk
    assert compilation.demands[0].quantity == Quantity("1750.000000000000", "ml")
    assert compilation.contributions[0].source_id == "weekly"
    assert compilation.contributions[0].contribution_id == "recurring:milk"


def test_recurring_need_fractional_horizon_rounds_up_deterministically(milk: Item) -> None:
    history = HouseholdHistory(
        (
            consumption(
                "c1", milk, "1", "ml", start=BASE, end=BASE + timedelta(days=3)
            ),
        )
    )
    estimate = estimate_consumption(history, "milk")
    assert estimate is not None
    contribution = RecurringNeedSource("window", "2.5", (estimate,)).emit_contributions()[0]
    assert contribution.quantity == Quantity("0.833333333334", "ml")


def test_recurring_need_rejects_duplicate_estimates(milk: Item) -> None:
    history = HouseholdHistory(
        (
            consumption(
                "c1", milk, "100", "ml", start=BASE, end=BASE + timedelta(days=1)
            ),
        )
    )
    estimate = estimate_consumption(history, "milk")
    assert estimate is not None
    with pytest.raises(ValueError, match="duplicate"):
        RecurringNeedSource("weekly", "7", (estimate, estimate))


def test_recurring_need_uses_exact_evidence_when_display_rate_rounds_to_zero(milk: Item) -> None:
    history = HouseholdHistory(
        (
            consumption(
                "tiny",
                milk,
                "0.0000000000001",
                "ml",
                start=BASE,
                end=BASE + timedelta(days=1),
            ),
        )
    )
    estimate = estimate_consumption(history, "milk")
    assert estimate is not None
    assert estimate.daily_quantity.amount == Decimal("0E-12")
    contribution = RecurringNeedSource("tiny-window", "1", (estimate,)).emit_contributions()[0]
    assert contribution.quantity == Quantity("0.000000000001", "ml")


def test_recurring_need_normalizes_estimate_order(milk: Item, rice: Item) -> None:
    history = HouseholdHistory(
        (
            consumption("m", milk, "100", "ml", start=BASE, end=BASE + timedelta(days=1)),
            consumption("r", rice, "50", "g", start=BASE, end=BASE + timedelta(days=1)),
        )
    )
    estimates = estimate_all_consumption(history)
    source = RecurringNeedSource("ordered", "1", tuple(reversed(estimates)))
    assert tuple(e.item.id for e in source.estimates) == ("milk", "rice")
    assert tuple(c.item.id for c in source.emit_contributions()) == ("milk", "rice")


def test_consumption_estimate_rejects_forged_central_rate(milk: Item) -> None:
    history = HouseholdHistory(
        (consumption("basis", milk, "100", "ml", start=BASE, end=BASE + timedelta(days=1)),)
    )
    estimate = estimate_consumption(history, "milk")
    assert estimate is not None
    with pytest.raises(ValueError, match="exact evidence basis"):
        type(estimate)(
            item=estimate.item,
            daily_quantity=Quantity("999", "ml"),
            sample_count=estimate.sample_count,
            observed_days=estimate.observed_days,
            total_consumed=estimate.total_consumed,
            observed_microseconds=estimate.observed_microseconds,
            daily_min=Quantity("999", "ml"),
            daily_max=Quantity("999", "ml"),
            uncertainty=Quantity("0", "ml"),
        )
