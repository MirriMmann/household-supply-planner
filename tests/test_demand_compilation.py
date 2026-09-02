from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from household_supply import (
    ExplicitNeed,
    ExplicitNeedSource,
    InventoryLot,
    InventorySnapshot,
    Item,
    MarketSnapshot,
    MealDemandSource,
    MealRequest,
    Money,
    Offer,
    PlanningPolicy,
    PlanningProblem,
    Quantity,
    Recipe,
    RecipeIngredient,
    SKU,
    build_plan,
    compile_demand_sources,
)


def _recipe(
    *,
    recipe_id: str = "chicken-rice",
    servings: int | str = 2,
    rice: Item | None = None,
    chicken: Item | None = None,
) -> Recipe:
    rice = rice or Item("rice", "Rice")
    chicken = chicken or Item("chicken", "Chicken")
    return Recipe(
        recipe_id,
        "Chicken with rice",
        servings,
        (
            RecipeIngredient(rice, Quantity(300, "g")),
            RecipeIngredient(chicken, Quantity(400, "g")),
        ),
    )


def test_meal_source_scales_recipe_from_base_servings() -> None:
    recipe = _recipe(servings=2)
    source = MealDemandSource("dinner", (MealRequest(recipe, 5),))

    compilation = compile_demand_sources((source,))
    demands = {demand.item.id: demand.quantity for demand in compilation.demands}

    assert demands["rice"] == Quantity(750, "g")
    assert demands["chicken"] == Quantity(1000, "g")


def test_meal_scaling_preserves_exact_decimal_values() -> None:
    rice = Item("rice", "Rice")
    recipe = Recipe(
        "rice-bowl",
        "Rice bowl",
        3,
        (RecipeIngredient(rice, Quantity(1, "kg")),),
    )
    source = MealDemandSource("meal", (MealRequest(recipe, "1.5"),))

    compilation = compile_demand_sources((source,))

    assert compilation.demands[0].quantity == Quantity(500, "g")
    assert compilation.demands[0].quantity.amount == Decimal("500.0")


def test_same_item_is_aggregated_across_multiple_recipes() -> None:
    rice = Item("rice", "Rice")
    chicken = Item("chicken", "Chicken")
    first = _recipe(recipe_id="first", rice=rice, chicken=chicken)
    second = Recipe(
        "second",
        "Rice porridge",
        1,
        (RecipeIngredient(rice, Quantity(250, "g")),),
    )
    source = MealDemandSource(
        "weekend",
        (MealRequest(first, 2), MealRequest(second, 1)),
    )

    compilation = compile_demand_sources((source,))
    demands = {demand.item.id: demand.quantity for demand in compilation.demands}

    assert demands["rice"] == Quantity(550, "g")
    assert demands["chicken"] == Quantity(400, "g")
    assert len(compilation.contributions) == 3


def test_same_item_is_aggregated_across_meal_and_explicit_sources() -> None:
    rice = Item("rice", "Rice")
    chicken = Item("chicken", "Chicken")
    meal_source = MealDemandSource(
        "meal-plan",
        (MealRequest(_recipe(rice=rice, chicken=chicken), 2),),
    )
    explicit_source = ExplicitNeedSource(
        "extra-rice",
        (ExplicitNeed(rice, Quantity(200, "g")),),
    )

    compilation = compile_demand_sources((meal_source, explicit_source))
    demands = {demand.item.id: demand.quantity for demand in compilation.demands}

    assert demands["rice"] == Quantity(500, "g")
    assert demands["chicken"] == Quantity(400, "g")
    assert {entry.source for entry in compilation.contributions} == {
        "meal:meal-plan:0:chicken-rice",
        "explicit:extra-rice",
    }


def test_compilation_is_deterministically_ordered_by_item_id() -> None:
    z_item = Item("z", "Z")
    a_item = Item("a", "A")
    source = ExplicitNeedSource(
        "explicit",
        (
            ExplicitNeed(z_item, Quantity(1, "piece")),
            ExplicitNeed(a_item, Quantity(1, "piece")),
        ),
    )

    compilation = compile_demand_sources((source,))

    assert [demand.item.id for demand in compilation.demands] == ["a", "z"]


def test_compilation_rejects_duplicate_source_ids() -> None:
    rice = Item("rice", "Rice")
    first = ExplicitNeedSource("same", (ExplicitNeed(rice, Quantity(1, "kg")),))
    second = ExplicitNeedSource("same", (ExplicitNeed(rice, Quantity(1, "kg")),))

    with pytest.raises(ValueError, match="duplicate demand source id"):
        compile_demand_sources((first, second))


def test_compilation_rejects_conflicting_item_identity_for_same_id() -> None:
    rice_a = Item("rice", "Rice")
    rice_b = Item("rice", "Completely different item")
    first = ExplicitNeedSource("a", (ExplicitNeed(rice_a, Quantity(1, "kg")),))
    second = ExplicitNeedSource("b", (ExplicitNeed(rice_b, Quantity(1, "kg")),))

    with pytest.raises(ValueError, match="conflicting item identity"):
        compile_demand_sources((first, second))


def test_compilation_rejects_incompatible_units_for_same_item() -> None:
    rice = Item("rice", "Rice")
    first = ExplicitNeedSource("mass", (ExplicitNeed(rice, Quantity(1, "kg")),))
    second = ExplicitNeedSource("count", (ExplicitNeed(rice, Quantity(1, "piece")),))

    with pytest.raises(ValueError, match="incompatible demand units"):
        compile_demand_sources((first, second))


def test_recipe_requires_positive_servings_and_ingredients() -> None:
    rice = Item("rice", "Rice")
    ingredient = RecipeIngredient(rice, Quantity(100, "g"))

    with pytest.raises(ValueError, match="servings must be positive"):
        Recipe("bad", "Bad", 0, (ingredient,))
    with pytest.raises(ValueError, match="at least one ingredient"):
        Recipe("empty", "Empty", 1, ())


def test_meal_request_and_explicit_need_require_positive_quantities() -> None:
    rice = Item("rice", "Rice")
    recipe = Recipe(
        "rice",
        "Rice",
        1,
        (RecipeIngredient(rice, Quantity(100, "g")),),
    )

    with pytest.raises(ValueError, match="meal request servings must be positive"):
        MealRequest(recipe, 0)
    with pytest.raises(ValueError, match="explicit need quantity must be positive"):
        ExplicitNeed(rice, Quantity(0, "g"))


def test_recipe_servings_reject_float_for_exact_scaling() -> None:
    rice = Item("rice", "Rice")
    ingredient = RecipeIngredient(rice, Quantity(100, "g"))

    with pytest.raises(TypeError, match="float is not accepted"):
        Recipe("rice", "Rice", 1.5, (ingredient,))  # type: ignore[arg-type]


def test_inventory_is_applied_after_cross_source_aggregation() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    rice = Item("rice", "Rice")
    recipe = Recipe(
        "rice-meal",
        "Rice meal",
        1,
        (RecipeIngredient(rice, Quantity(400, "g")),),
    )
    meal_source = MealDemandSource("meal", (MealRequest(recipe, 1),))
    explicit_source = ExplicitNeedSource(
        "extra",
        (ExplicitNeed(rice, Quantity(300, "g")),),
    )
    compilation = compile_demand_sources((meal_source, explicit_source))

    rice_sku = SKU("rice-500g", rice, "Rice 500g", Quantity(500, "g"))
    offer = Offer(
        "rice-offer",
        rice_sku,
        "store-a",
        Money(100, "KGS"),
        now,
        "fixture",
    )
    problem = PlanningProblem(
        demands=compilation.demands,
        inventory=InventorySnapshot(
            (InventoryLot("rice-home", rice, Quantity(250, "g")),)
        ),
        market=MarketSnapshot(now, (offer,)),
        policy=PlanningPolicy(Money(1000, "KGS")),
    )

    plan = build_plan(problem)

    assert plan.total_cost == Money(100, "KGS")
    assert plan.requirement_coverage[0].required == Quantity(700, "g")
    assert plan.requirement_coverage[0].inventory_used == Quantity(250, "g")
    assert plan.requirement_coverage[0].purchased == Quantity(500, "g")
    assert plan.projected_leftovers[0].quantity == Quantity(50, "g")


def test_compiled_meal_demands_flow_through_existing_m1_planner() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    rice = Item("rice", "Rice")
    chicken = Item("chicken", "Chicken")
    recipe = _recipe(rice=rice, chicken=chicken)
    compilation = compile_demand_sources(
        (MealDemandSource("dinners", (MealRequest(recipe, 4),)),)
    )

    rice_sku = SKU("rice-1kg", rice, "Rice 1kg", Quantity(1, "kg"))
    chicken_sku = SKU(
        "chicken-1kg", chicken, "Chicken 1kg", Quantity(1, "kg")
    )
    market = MarketSnapshot(
        now,
        (
            Offer(
                "rice-offer",
                rice_sku,
                "store",
                Money(120, "KGS"),
                now,
                "fixture",
            ),
            Offer(
                "chicken-offer",
                chicken_sku,
                "store",
                Money(380, "KGS"),
                now,
                "fixture",
            ),
        ),
    )
    problem = PlanningProblem(
        demands=compilation.demands,
        inventory=InventorySnapshot(),
        market=market,
        policy=PlanningPolicy(Money(1000, "KGS")),
    )

    plan = build_plan(problem)

    assert plan.total_cost == Money(500, "KGS")
    assert {coverage.item_id: coverage.required for coverage in plan.requirement_coverage} == {
        "chicken": Quantity(800, "g"),
        "rice": Quantity(600, "g"),
    }


def test_m2_collections_are_normalized_to_immutable_tuples() -> None:
    rice = Item("rice", "Rice")
    ingredient = RecipeIngredient(rice, Quantity(100, "g"))
    ingredient_list = [ingredient]
    recipe = Recipe("rice", "Rice", 1, ingredient_list)  # type: ignore[arg-type]
    ingredient_list.clear()

    need_list = [ExplicitNeed(rice, Quantity(100, "g"))]
    source = ExplicitNeedSource(" explicit ", need_list)  # type: ignore[arg-type]
    need_list.clear()

    assert recipe.ingredients == (ingredient,)
    assert source.source_id == "explicit"
    assert len(source.needs) == 1


def test_compiler_rejects_missing_or_empty_demand_sources() -> None:
    with pytest.raises(ValueError, match="at least one demand source"):
        compile_demand_sources(())

    class EmptySource:
        source_id = "empty"

        def emit_demands(self):
            return ()

    with pytest.raises(ValueError, match="emitted no demands"):
        compile_demand_sources((EmptySource(),))


def test_demand_aggregation_is_independent_of_source_order() -> None:
    item = Item("powder", "Powder")
    large = ExplicitNeedSource(
        "large",
        (ExplicitNeed(item, Quantity("1", "g")),),
    )
    thirds = ExplicitNeedSource(
        "thirds",
        (
            ExplicitNeed(item, Quantity("0.3333333333333333333333333333", "g")),
            ExplicitNeed(item, Quantity("0.3333333333333333333333333333", "g")),
        ),
    )

    forward = compile_demand_sources((large, thirds))
    reverse = compile_demand_sources((thirds, large))

    assert forward.demands == reverse.demands
    assert forward.demands[0].quantity == Quantity(
        "1.6666666666666666666666666666", "g"
    )
