from __future__ import annotations

from dataclasses import dataclass

from .money import Money


@dataclass(frozen=True, slots=True)
class SurplusPenaltyRate:
    """Soft objective cost per one base unit of over-purchased quantity."""

    item_id: str
    cost_per_base_unit: Money

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("surplus penalty item_id must not be empty")
        if self.cost_per_base_unit.amount < 0:
            raise ValueError("surplus penalty rate must not be negative")


@dataclass(frozen=True, slots=True)
class MultiObjectivePolicy:
    """Soft scoring policy layered on top of the hard M1 PlanningProblem."""

    additional_store_penalty: Money
    surplus_penalties: tuple[SurplusPenaltyRate, ...] = ()

    def __post_init__(self) -> None:
        if self.additional_store_penalty.amount < 0:
            raise ValueError("additional store penalty must not be negative")
        item_ids = [penalty.item_id for penalty in self.surplus_penalties]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("multi-objective policy contains duplicate surplus item ids")
        for penalty in self.surplus_penalties:
            if penalty.cost_per_base_unit.currency != self.additional_store_penalty.currency:
                raise ValueError("all objective penalties must use the same currency")

    @classmethod
    def zero(cls, currency: str) -> MultiObjectivePolicy:
        return cls(additional_store_penalty=Money.zero(currency))

    def surplus_rate_for(self, item_id: str) -> Money:
        for penalty in self.surplus_penalties:
            if penalty.item_id == item_id:
                return penalty.cost_per_base_unit
        return Money.zero(self.additional_store_penalty.currency)
