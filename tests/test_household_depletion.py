from __future__ import annotations

from datetime import datetime, timedelta, timezone

from household_supply.domain import Item, Quantity
from household_supply.household import (
    ConsumptionObservation,
    DepletionWindowStatus,
    HouseholdEventId,
    HouseholdHistory,
    InventoryCorrection,
    PurchaseEvent,
    depletion_learning_report,
    derive_stocktake_depletion_windows,
    estimate_all_depletion,
    estimate_depletion,
)


UTC = timezone.utc
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
MILK = Item("milk", "Milk", "dairy")


def count(event_id: str, amount: str, at: datetime, *, recorded_at=None):
    return InventoryCorrection(
        HouseholdEventId(event_id),
        MILK,
        Quantity(amount, "ml"),
        at,
        recorded_at or at,
        "stocktake",
    )


def purchase(event_id: str, amount: str, at: datetime):
    return PurchaseEvent(
        HouseholdEventId(event_id),
        MILK,
        Quantity(amount, "ml"),
        at,
        at,
    )


def use(event_id: str, amount: str, start: datetime, end: datetime):
    return ConsumptionObservation(
        HouseholdEventId(event_id),
        MILK,
        Quantity(amount, "ml"),
        start,
        end,
        end,
    )


def test_stocktakes_infer_depletion_without_consumption_events() -> None:
    history = HouseholdHistory(
        (
            count("start", "2000", BASE),
            count("end", "1200", BASE + timedelta(days=2)),
        )
    )
    windows = derive_stocktake_depletion_windows(history, "milk")
    assert len(windows) == 1
    assert windows[0].status is DepletionWindowStatus.USED
    assert windows[0].inferred_depletion == Quantity("800", "ml")

    estimate = estimate_depletion(history, "milk")
    assert estimate is not None
    assert estimate.daily_quantity == Quantity("400.000000000000", "ml")
    assert estimate.total_consumed == Quantity("800", "ml")
    assert estimate.sample_count == 1


def test_confirmed_purchase_is_added_between_stocktakes() -> None:
    history = HouseholdHistory(
        (
            count("start", "1000", BASE),
            purchase("buy", "1000", BASE + timedelta(days=1)),
            count("end", "1400", BASE + timedelta(days=2)),
        )
    )
    window = derive_stocktake_depletion_windows(history, "milk")[0]
    assert window.confirmed_purchases == Quantity("1000", "ml")
    assert window.purchase_event_ids == ("buy",)
    assert window.inferred_depletion == Quantity("600", "ml")
    estimate = estimate_depletion(history, "milk")
    assert estimate is not None
    assert estimate.daily_quantity == Quantity("300.000000000000", "ml")


def test_unexplained_inventory_increase_is_not_negative_consumption() -> None:
    history = HouseholdHistory(
        (
            count("start", "1000", BASE),
            count("end", "1200", BASE + timedelta(days=1)),
        )
    )
    report = depletion_learning_report(history, "milk")
    assert report is not None
    assert report.windows[0].status is DepletionWindowStatus.UNEXPLAINED_INCREASE
    assert report.windows[0].inferred_depletion is None
    assert report.estimate is None


def test_explicit_observation_conflict_invalidates_derived_window_but_not_direct_evidence() -> None:
    end = BASE + timedelta(days=1)
    history = HouseholdHistory(
        (
            count("start", "1000", BASE),
            use("direct", "200", BASE, end),
            count("end", "900", end),
        )
    )
    report = depletion_learning_report(history, "milk")
    assert report is not None
    assert report.windows[0].status is DepletionWindowStatus.EXPLICIT_CONFLICT
    assert report.direct_observation_ids_used == ("direct",)
    assert report.direct_observation_ids_shadowed == ()
    assert report.estimate is not None
    assert report.estimate.daily_quantity == Quantity("200.000000000000", "ml")


def test_accepted_stocktake_window_shadows_overlapping_direct_observation() -> None:
    end = BASE + timedelta(days=1)
    history = HouseholdHistory(
        (
            count("start", "1000", BASE),
            use("direct", "100", BASE, end),
            count("end", "700", end),
        )
    )
    report = depletion_learning_report(history, "milk")
    assert report is not None
    assert report.windows[0].status is DepletionWindowStatus.USED
    assert report.direct_observation_ids_used == ()
    assert report.direct_observation_ids_shadowed == ("direct",)
    assert report.estimate is not None
    assert report.estimate.total_consumed == Quantity("300", "ml")
    assert report.estimate.sample_count == 1


def test_zero_depletion_window_contributes_observed_time() -> None:
    history = HouseholdHistory(
        (
            count("d0", "1000", BASE),
            count("d1", "1000", BASE + timedelta(days=1)),
            count("d2", "500", BASE + timedelta(days=2)),
        )
    )
    report = depletion_learning_report(history, "milk")
    assert report is not None
    assert tuple(window.status for window in report.windows) == (
        DepletionWindowStatus.ZERO_DEPLETION,
        DepletionWindowStatus.USED,
    )
    assert report.estimate is not None
    assert report.estimate.daily_quantity == Quantity("250.000000000000", "ml")
    assert report.estimate.daily_min == Quantity("0E-12", "ml")
    assert report.estimate.daily_max == Quantity("500.000000000000", "ml")
    assert report.estimate.sample_count == 2
    assert report.estimate.observed_days.as_tuple().exponent == -12


def test_all_zero_stocktake_windows_produce_no_recurring_estimate() -> None:
    history = HouseholdHistory(
        (
            count("d0", "1000", BASE),
            count("d1", "1000", BASE + timedelta(days=1)),
        )
    )
    report = depletion_learning_report(history, "milk")
    assert report is not None
    assert report.windows[0].status is DepletionWindowStatus.ZERO_DEPLETION
    assert report.estimate is None


def test_late_recorded_stocktake_is_not_used_before_it_is_known() -> None:
    occurred = BASE + timedelta(days=1)
    recorded = BASE + timedelta(days=3)
    history = HouseholdHistory(
        (
            count("start", "1000", BASE),
            count("late", "500", occurred, recorded_at=recorded),
        )
    )
    assert estimate_depletion(history, "milk", as_of=BASE + timedelta(days=2)) is None
    late = estimate_depletion(history, "milk", as_of=BASE + timedelta(days=4))
    assert late is not None
    assert late.daily_quantity == Quantity("500.000000000000", "ml")


def test_purchase_at_start_count_timestamp_is_excluded_but_end_timestamp_is_included() -> None:
    end = BASE + timedelta(days=1)
    history = HouseholdHistory(
        (
            purchase("before-start-count", "500", BASE),
            count("start", "1000", BASE),
            purchase("before-end-count", "500", end),
            count("end", "1200", end),
        )
    )
    window = derive_stocktake_depletion_windows(history, "milk")[0]
    assert window.purchase_event_ids == ("before-end-count",)
    assert window.inferred_depletion == Quantity("300", "ml")


def test_depletion_estimate_is_deterministic_under_event_order() -> None:
    events = (
        count("d0", "1200", BASE),
        purchase("buy", "500", BASE + timedelta(hours=12)),
        count("d1", "1300", BASE + timedelta(days=1)),
        count("d2", "900", BASE + timedelta(days=2)),
    )
    forward = estimate_depletion(HouseholdHistory(events), "milk")
    reverse = estimate_depletion(HouseholdHistory(tuple(reversed(events))), "milk")
    assert forward == reverse


def test_estimate_all_depletion_is_sorted() -> None:
    rice = Item("rice", "Rice", "grain")
    history = HouseholdHistory(
        (
            count("milk-a", "1000", BASE),
            count("milk-b", "500", BASE + timedelta(days=1)),
            InventoryCorrection(
                HouseholdEventId("rice-a"), rice, Quantity("1000", "g"), BASE, BASE, "count"
            ),
            InventoryCorrection(
                HouseholdEventId("rice-b"),
                rice,
                Quantity("800", "g"),
                BASE + timedelta(days=1),
                BASE + timedelta(days=1),
                "count",
            ),
        )
    )
    assert tuple(estimate.item.id for estimate in estimate_all_depletion(history)) == (
        "milk",
        "rice",
    )
