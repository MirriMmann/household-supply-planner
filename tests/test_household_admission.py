from __future__ import annotations

from datetime import datetime, timedelta, timezone

from household_supply.domain import Item, Quantity
from household_supply.household import (
    ConsumptionObservation,
    HouseholdEventId,
    HouseholdHistory,
    InventoryCorrection,
    RecurringAdmissionStatus,
    admit_recurring_estimate,
    admitted_recurring_estimates,
    depletion_learning_report,
)


UTC = timezone.utc
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)
MILK = Item("milk", "Milk", "dairy")


def use(event_id: str, amount: str, start: datetime, end: datetime):
    return ConsumptionObservation(
        HouseholdEventId(event_id),
        MILK,
        Quantity(amount, "ml"),
        start,
        end,
        end,
    )


def count(event_id: str, amount: str, at: datetime):
    return InventoryCorrection(
        HouseholdEventId(event_id),
        MILK,
        Quantity(amount, "ml"),
        at,
        at,
        "stocktake",
    )


def test_no_positive_rate_is_insufficient_for_recurring_demand() -> None:
    history = HouseholdHistory(
        (
            count("d0", "1000", BASE),
            count("d1", "1000", BASE + timedelta(days=1)),
        )
    )
    report = depletion_learning_report(history, "milk")
    assert report is not None
    admission = admit_recurring_estimate(report)
    assert admission.status is RecurringAdmissionStatus.INSUFFICIENT
    assert admission.accepted_estimate is None


def test_short_direct_observation_is_provisional() -> None:
    history = HouseholdHistory(
        (
            use("u1", "2000", BASE, BASE + timedelta(minutes=10)),
        )
    )
    report = depletion_learning_report(history, "milk")
    assert report is not None
    assert report.estimate is not None
    admission = admit_recurring_estimate(report)
    assert admission.status is RecurringAdmissionStatus.PROVISIONAL
    assert admission.accepted_estimate is None


def test_one_day_of_evidence_is_accepted() -> None:
    history = HouseholdHistory(
        (
            use("u1", "500", BASE, BASE + timedelta(days=1)),
        )
    )
    report = depletion_learning_report(history, "milk")
    assert report is not None
    admission = admit_recurring_estimate(report)
    assert admission.status is RecurringAdmissionStatus.ACCEPTED
    assert admission.accepted_estimate == report.estimate


def test_short_stocktake_window_remains_raw_but_not_forecast_evidence() -> None:
    history = HouseholdHistory(
        (
            count("start", "2000", BASE),
            count("end", "0", BASE + timedelta(minutes=10)),
        )
    )
    report = depletion_learning_report(history, "milk")
    assert report is not None
    assert report.windows[0].inferred_depletion == Quantity("2000", "ml")
    assert report.windows[0].accepted_for_learning is False
    admission = admit_recurring_estimate(report)
    assert admission.status is RecurringAdmissionStatus.INSUFFICIENT
    assert admission.accepted_estimate is None


def test_bulk_admission_excludes_short_rate() -> None:
    history = HouseholdHistory(
        (
            use("u1", "2000", BASE, BASE + timedelta(minutes=10)),
        )
    )
    assert admitted_recurring_estimates(history) == ()
