from __future__ import annotations

from dataclasses import dataclass

from household_supply.household import (
    HouseholdEventRepositoryError,
    HouseholdLearningService,
)

from .persistence import PlanRepository, PlanRepositoryError


class LocalDataResetError(RuntimeError):
    """A destructive local reset could not be completed."""


@dataclass(frozen=True, slots=True)
class LocalDataResetResult:
    household_events_deleted: int
    plans_deleted: int

    def __post_init__(self) -> None:
        if self.household_events_deleted < 0 or self.plans_deleted < 0:
            raise ValueError("reset deletion counts must not be negative")


@dataclass(frozen=True, slots=True)
class LocalDataResetService:
    """Explicit destructive boundary for one local household profile.

    Household evidence is cleared before historical plans. If the second store
    fails, stale plans may remain visible, but stale learning cannot continue to
    influence new household replenishment runs.
    """

    household: HouseholdLearningService
    plans: PlanRepository

    def reset(self) -> LocalDataResetResult:
        try:
            household_deleted = self.household.repository.clear()
        except HouseholdEventRepositoryError as exc:
            raise LocalDataResetError(
                "could not clear household event history"
            ) from exc

        try:
            plans_deleted = self.plans.clear()
        except PlanRepositoryError as exc:
            raise LocalDataResetError(
                "household history was cleared but plan history could not be cleared"
            ) from exc

        return LocalDataResetResult(
            household_events_deleted=household_deleted,
            plans_deleted=plans_deleted,
        )
