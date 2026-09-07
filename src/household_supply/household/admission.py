from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .depletion import DepletionLearningReport, depletion_learning_reports
from .history import HouseholdHistory
from .learning import ConsumptionEstimate


MICROSECONDS_PER_DAY = 86_400_000_000
MIN_RECURRING_OBSERVED_MICROSECONDS = MICROSECONDS_PER_DAY


class RecurringAdmissionStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    PROVISIONAL = "provisional"
    ACCEPTED = "accepted"


@dataclass(frozen=True, slots=True)
class RecurringEstimateAdmission:
    """Whether descriptive depletion evidence may drive future recurring demand."""

    item_id: str
    status: RecurringAdmissionStatus
    estimate: ConsumptionEstimate | None
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        item_id = self.item_id.strip()
        reasons = tuple(self.reasons)
        if not item_id:
            raise ValueError("recurring admission item_id must not be empty")
        if self.estimate is not None and self.estimate.item.id != item_id:
            raise ValueError("recurring admission estimate Item does not match item_id")
        if self.status is RecurringAdmissionStatus.ACCEPTED and self.estimate is None:
            raise ValueError("accepted recurring admission requires an estimate")
        if self.status is RecurringAdmissionStatus.INSUFFICIENT and self.estimate is not None:
            raise ValueError("insufficient recurring admission must not expose an estimate")
        if self.status is RecurringAdmissionStatus.ACCEPTED and reasons:
            raise ValueError("accepted recurring admission must not contain rejection reasons")
        object.__setattr__(self, "item_id", item_id)
        object.__setattr__(self, "reasons", reasons)

    @property
    def accepted_estimate(self) -> ConsumptionEstimate | None:
        if self.status is not RecurringAdmissionStatus.ACCEPTED:
            return None
        return self.estimate


def admit_recurring_estimate(
    report: DepletionLearningReport,
) -> RecurringEstimateAdmission:
    """Admit only evidence observed across at least one day."""

    estimate = report.estimate
    if estimate is None:
        return RecurringEstimateAdmission(
            item_id=report.item.id,
            status=RecurringAdmissionStatus.INSUFFICIENT,
            estimate=None,
            reasons=("no_positive_rate_estimate",),
        )

    if estimate.observed_microseconds < MIN_RECURRING_OBSERVED_MICROSECONDS:
        return RecurringEstimateAdmission(
            item_id=report.item.id,
            status=RecurringAdmissionStatus.PROVISIONAL,
            estimate=estimate,
            reasons=("requires_at_least_one_observed_day",),
        )

    return RecurringEstimateAdmission(
        item_id=report.item.id,
        status=RecurringAdmissionStatus.ACCEPTED,
        estimate=estimate,
    )


def admitted_recurring_estimates(
    history: HouseholdHistory,
    *,
    as_of: datetime | None = None,
) -> tuple[ConsumptionEstimate, ...]:
    accepted: list[ConsumptionEstimate] = []
    for report in depletion_learning_reports(history, as_of=as_of):
        estimate = admit_recurring_estimate(report).accepted_estimate
        if estimate is not None:
            accepted.append(estimate)
    return tuple(accepted)
