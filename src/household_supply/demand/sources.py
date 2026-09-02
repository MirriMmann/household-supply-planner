from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from household_supply.domain.demand import Demand
from household_supply.domain.items import Item
from household_supply.domain.quantity import Quantity
from household_supply.domain.recipes import MealRequest


@runtime_checkable
class DemandSource(Protocol):
    """A bounded producer of demand contributions.

    A demand source describes *why* an item is needed. It does not inspect
    inventory, market offers, budget, or planner state.
    """

    @property
    def source_id(self) -> str: ...

    def emit_demands(self) -> tuple[Demand, ...]: ...


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

    def emit_demands(self) -> tuple[Demand, ...]:
        return tuple(
            Demand(
                item=need.item,
                quantity=need.quantity,
                source=f"explicit:{self.source_id}",
            )
            for need in self.needs
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

    def emit_demands(self) -> tuple[Demand, ...]:
        emitted: list[Demand] = []
        for meal_index, meal in enumerate(self.meals):
            scale = meal.servings / meal.recipe.servings
            for ingredient in meal.recipe.ingredients:
                base = ingredient.quantity.as_base()
                emitted.append(
                    Demand(
                        item=ingredient.item,
                        quantity=Quantity(base.base_amount * scale, base.base_unit),
                        source=(
                            f"meal:{self.source_id}:"
                            f"{meal_index}:{meal.recipe.id}"
                        ),
                    )
                )
        return tuple(emitted)
