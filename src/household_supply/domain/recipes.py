from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .items import Item
from .money import DecimalLike, as_decimal
from .quantity import Quantity


@dataclass(frozen=True, slots=True)
class RecipeIngredient:
    item: Item
    quantity: Quantity

    def __post_init__(self) -> None:
        if self.quantity.amount <= 0:
            raise ValueError("recipe ingredient quantity must be positive")


@dataclass(frozen=True, slots=True)
class Recipe:
    id: str
    name: str
    servings: Decimal
    ingredients: tuple[RecipeIngredient, ...]

    def __init__(
        self,
        id: str,
        name: str,
        servings: DecimalLike,
        ingredients: tuple[RecipeIngredient, ...],
    ) -> None:
        normalized_id = id.strip()
        normalized_name = name.strip()
        normalized_servings = as_decimal(servings)

        if not normalized_id:
            raise ValueError("recipe id must not be empty")
        if not normalized_name:
            raise ValueError("recipe name must not be empty")
        if normalized_servings <= 0:
            raise ValueError("recipe servings must be positive")
        normalized_ingredients = tuple(ingredients)
        if not normalized_ingredients:
            raise ValueError("recipe must contain at least one ingredient")

        seen_items: dict[str, Item] = {}
        for ingredient in normalized_ingredients:
            previous = seen_items.get(ingredient.item.id)
            if previous is not None and previous != ingredient.item:
                raise ValueError(
                    f"recipe contains conflicting item identity: {ingredient.item.id}"
                )
            seen_items[ingredient.item.id] = ingredient.item

        object.__setattr__(self, "id", normalized_id)
        object.__setattr__(self, "name", normalized_name)
        object.__setattr__(self, "servings", normalized_servings)
        object.__setattr__(self, "ingredients", normalized_ingredients)


@dataclass(frozen=True, slots=True)
class MealRequest:
    recipe: Recipe
    servings: Decimal

    def __init__(self, recipe: Recipe, servings: DecimalLike) -> None:
        normalized_servings = as_decimal(servings)
        if normalized_servings <= 0:
            raise ValueError("meal request servings must be positive")
        object.__setattr__(self, "recipe", recipe)
        object.__setattr__(self, "servings", normalized_servings)
