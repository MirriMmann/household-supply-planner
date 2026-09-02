from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from household_supply.domain._decimal import scale_decimal_ratio_up
from household_supply.domain.items import Item
from household_supply.domain.quantity import Quantity
from household_supply.domain.recipes import MealRequest


RECIPE_SCALING_DECIMAL_PLACES = 12


@dataclass(frozen=True, slots=True)
class DemandContribution:
    """One attributable, positive contribution emitted by a DemandSource."""

    source_id: str
    contribution_id: str
    item: Item
    quantity: Quantity

    def __post_init__(self) -> None:
        normalized_source_id = self.source_id.strip()
        normalized_contribution_id = self.contribution_id.strip()
        if not normalized_source_id:
            raise ValueError("contribution source id must not be empty")
        if not normalized_contribution_id:
            raise ValueError("contribution id must not be empty")
        if self.quantity.amount <= 0:
            raise ValueError("demand contribution quantity must be positive")
        object.__setattr__(self, "source_id", normalized_source_id)
        object.__setattr__(self, "contribution_id", normalized_contribution_id)


@runtime_checkable
class DemandSource(Protocol):
    """A bounded producer of attributable demand contributions.

    A demand source describes *why* an item is needed. It does not inspect
    inventory, market offers, budget, or planner state.
    """

    @property
    def source_id(self) -> str: ...

    def emit_contributions(self) -> tuple[DemandContribution, ...]: ...


@dataclass(frozen=True, slots=True)
class ExplicitNeed:
    item: Item
    quantity: Quantity

    def __post_init__(self) -> None:
        if self.quantity.amount <= 0:
            raise ValueError("explicit need quantity must be positive")


@dataclass(frozen=True, slots=True)
class ExplicitNeedSource:
    source_id: str
    needs: tuple[ExplicitNeed, ...]

    def __post_init__(self) -> None:
        normalized_id = self.source_id.strip()
        normalized_needs = tuple(self.needs)
        if not normalized_id:
            raise ValueError("demand source id must not be empty")
        if not normalized_needs:
            raise ValueError("explicit need source must contain at least one need")
        object.__setattr__(self, "source_id", normalized_id)
        object.__setattr__(self, "needs", normalized_needs)

    def emit_contributions(self) -> tuple[DemandContribution, ...]:
        return tuple(
            DemandContribution(
                source_id=self.source_id,
                contribution_id=f"explicit:{index}",
                item=need.item,
                quantity=need.quantity,
            )
            for index, need in enumerate(self.needs)
        )


@dataclass(frozen=True, slots=True)
class MealDemandSource:
    source_id: str
    meals: tuple[MealRequest, ...]

    def __post_init__(self) -> None:
        normalized_id = self.source_id.strip()
        normalized_meals = tuple(self.meals)
        if not normalized_id:
            raise ValueError("demand source id must not be empty")
        if not normalized_meals:
            raise ValueError("meal demand source must contain at least one meal")
        object.__setattr__(self, "source_id", normalized_id)
        object.__setattr__(self, "meals", normalized_meals)

    def emit_contributions(self) -> tuple[DemandContribution, ...]:
        emitted: list[DemandContribution] = []
        for meal_index, meal in enumerate(self.meals):
            for ingredient_index, ingredient in enumerate(meal.recipe.ingredients):
                base = ingredient.quantity.as_base()
                scaled_amount = scale_decimal_ratio_up(
                    base.base_amount,
                    meal.servings,
                    meal.recipe.servings,
                    decimal_places=RECIPE_SCALING_DECIMAL_PLACES,
                )
                emitted.append(
                    DemandContribution(
                        source_id=self.source_id,
                        contribution_id=(
                            f"meal:{meal_index}:{meal.recipe.id}:{ingredient_index}"
                        ),
                        item=ingredient.item,
                        quantity=Quantity(scaled_amount, base.base_unit),
                    )
                )
        return tuple(emitted)
