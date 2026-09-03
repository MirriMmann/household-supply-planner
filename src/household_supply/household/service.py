from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from household_supply.domain.money import DecimalLike

from .depletion import (
    DepletionLearningReport,
    depletion_learning_reports,
    estimate_all_depletion,
)
from .events import HouseholdEvent
from .history import HouseholdHistory
from .learning import ConsumptionEstimate
from .persistence import HouseholdEventRepository
from .projection import HouseholdState, project_household_state
from .recurring import RecurringNeedSource


@dataclass(frozen=True, slots=True)
class HouseholdLearningService:
    """Thin orchestration boundary over explicit household facts and derivations."""

    repository: HouseholdEventRepository

    def record(self, event: HouseholdEvent) -> None:
        self.repository.append(event)

    def history(self) -> HouseholdHistory:
        return self.repository.history()

    def state(self, *, as_of: datetime) -> HouseholdState:
        return project_household_state(self.history(), as_of=as_of)

    def depletion_reports(
        self, *, as_of: datetime | None = None
    ) -> tuple[DepletionLearningReport, ...]:
        return depletion_learning_reports(self.history(), as_of=as_of)

    def estimates(
        self, *, as_of: datetime | None = None
    ) -> tuple[ConsumptionEstimate, ...]:
        # M10 broadens the learning basis from explicit ConsumptionObservation
        # events to transparent depletion evidence derived between stocktakes.
        return estimate_all_depletion(self.history(), as_of=as_of)

    def recurring_need_source(
        self,
        *,
        source_id: str,
        horizon_days: DecimalLike,
        as_of: datetime | None = None,
    ) -> RecurringNeedSource:
        estimates = self.estimates(as_of=as_of)
        return RecurringNeedSource(source_id, horizon_days, estimates)
