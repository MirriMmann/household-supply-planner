"""Small original starter catalog; no scraped or chef-attributed recipes.

Each recipe uses canonical Item IDs and measured quantities. Future recipe packs
may add cuisines, attribution and source links without changing planning code.
"""

from __future__ import annotations

from dataclasses import dataclass

from household_supply.domain import CatalogSnapshot, Quantity, Recipe, RecipeIngredient
from .kyrgyz_recipes import KYRGYZ_RECIPE_SPECS
from .models import catalog_items_by_id


@dataclass(frozen=True, slots=True)
class CuratedRecipe:
    recipe: Recipe
    category: str
    cuisine: str
    steps: tuple[str, ...]
    source_url: str | None = None
    attribution: str | None = None

    def __post_init__(self) -> None:
        if not self.category.strip() or not self.cuisine.strip() or not self.steps:
            raise ValueError("recipe category, cuisine and steps are required")
        if (self.attribution is None) != (self.source_url is None):
            raise ValueError("attributed recipes require a verifiable source link")


# ID, name, category, cuisine, servings, ingredients, brief ORIGINAL method.
# Culinary instructions below are composed for this pilot; they are not copies
# of a published chef's text. All quantities are deliberately explicit.
_STARTER = (
    ("milk-rice", "Молочный рис", "завтрак", "домашняя", 2,
     (("rice", "120", "g"), ("milk", "400", "ml")),
     ("Промойте рис.", "Сварите рис в молоке на слабом огне до готовности.")),
    ("rice-with-oil", "Рис с растительным маслом", "гарнир", "домашняя", 2,
     (("rice", "140", "g"), ("sunflower_oil", "20", "ml"), ("water", "300", "ml")),
     ("Сварите рис в воде.", "Перед подачей добавьте растительное масло.")),
    ("oats-milk", "Овсяная каша на молоке", "завтрак", "домашняя", 2,
     (("oatmeal", "100", "g"), ("milk", "350", "ml")),
     ("Нагрейте молоко.", "Всыпьте овсянку и доведите до готовности.")),
    ("oats-water", "Овсяная каша на воде", "завтрак", "домашняя", 2,
     (("oatmeal", "100", "g"), ("water", "400", "ml")),
     ("Вскипятите воду.", "Приготовьте овсяные хлопья до мягкости.")),
    ("semolina-milk", "Манная каша", "завтрак", "домашняя", 2,
     (("semolina", "65", "g"), ("milk", "500", "ml"), ("sugar", "20", "g")),
     ("Нагрейте молоко.", "Постепенно всыпьте манку, помешивая.", "Добавьте сахар.")),
    ("rice-sweet", "Сладкая рисовая каша", "завтрак", "домашняя", 2,
     (("rice", "120", "g"), ("milk", "450", "ml"), ("sugar", "25", "g")),
     ("Промойте рис.", "Варите с молоком до мягкости.", "Добавьте сахар.")),
    ("buckwheat-simple", "Гречневая каша", "гарнир", "домашняя", 2,
     (("buckwheat", "160", "g"), ("water", "350", "ml"), ("salt", "3", "g")),
     ("Промойте гречку.", "Варите в подсоленной воде до готовности.")),
    ("pasta-oil", "Макароны с маслом", "обед", "домашняя", 2,
     (("pasta", "160", "g"), ("sunflower_oil", "25", "ml"), ("salt", "3", "g"), ("water", "500", "ml")),
     ("Сварите макароны в подсоленной воде.", "Добавьте масло перед подачей.")),
    ("pasta-sauce", "Макароны с соусом", "обед", "домашняя", 2,
     (("pasta", "180", "g"), ("sauce", "100", "g"), ("water", "500", "ml")),
     ("Сварите макароны.", "Прогрейте с готовым соусом.")),
    ("omelet-basic", "Омлет", "завтрак", "домашняя", 2,
     (("eggs", "3", "piece"), ("milk", "80", "ml"), ("sunflower_oil", "10", "ml")),
     ("Смешайте яйца с молоком.", "Приготовьте под крышкой на смазанной сковороде.")),
    ("omelet-cheese", "Сырный омлет", "завтрак", "домашняя", 2,
     (("eggs", "3", "piece"), ("cheese", "60", "g"), ("milk", "60", "ml")),
     ("Взбейте яйца и молоко.", "Посыпьте сыром и доведите до готовности.")),
    ("cheese-toast", "Сырные тосты", "перекус", "домашняя", 2,
     (("bread", "4", "piece"), ("cheese", "80", "g")),
     ("Положите сыр на хлеб.", "Подогрейте до расплавления сыра.")),
    ("egg-sandwich", "Бутерброд с яйцом", "завтрак", "домашняя", 2,
     (("bread", "4", "piece"), ("eggs", "2", "piece")),
     ("Приготовьте яйца до полной готовности.", "Выложите на хлеб.")),
    ("curd-yogurt", "Творог с йогуртом", "завтрак", "домашняя", 2,
     (("cottage_cheese", "250", "g"), ("yogurt", "150", "g")),
     ("Смешайте творог с йогуртом.", "Подавайте охлаждённым.")),
    ("rice-egg", "Рис с яйцом", "обед", "домашняя", 2,
     (("rice", "150", "g"), ("eggs", "2", "piece"), ("sunflower_oil", "15", "ml"), ("water", "300", "ml")),
     ("Заранее сварите рис.", "Прогрейте на сковороде и добавьте яйца до полной готовности.")),
)


@dataclass(frozen=True, slots=True)
class RecipeCatalogCoverage:
    recipe_id: str
    missing_item_ids: tuple[str, ...]
    incompatible_unit_item_ids: tuple[str, ...]

    @property
    def supported(self) -> bool:
        return not (self.missing_item_ids or self.incompatible_unit_item_ids)


def _coverage(spec: tuple, *, items: dict, packages: dict) -> RecipeCatalogCoverage:
    recipe_id, _, _, _, _, ingredients, _ = spec
    missing = []
    incompatible = []
    for item_id, amount, unit in ingredients:
        if item_id not in items:
            missing.append(item_id)
        elif not any(
            Quantity(amount, unit).compatible_with(package)
            for package in packages.get(item_id, ())
        ):
            incompatible.append(item_id)
    return RecipeCatalogCoverage(
        recipe_id, tuple(sorted(set(missing))), tuple(sorted(set(incompatible)))
    )


def _catalog_packages(catalog: CatalogSnapshot) -> dict:
    packages = {}
    for sku in catalog.skus:
        packages.setdefault(sku.item.id, []).append(sku.package_quantity)
    return packages


def kyrgyz_recipe_coverage(catalog: CatalogSnapshot) -> tuple[RecipeCatalogCoverage, ...]:
    """Audit all five candidates, even when no recipe is publishable.

    Coverage is about catalog item identity and package-compatible units.
    It is NOT a promise of fresh market offers, stock on hand, or feasible cost.
    """
    items = catalog_items_by_id(catalog)
    packages = _catalog_packages(catalog)
    return tuple(
        _coverage(spec, items=items, packages=packages)
        for spec in KYRGYZ_RECIPE_SPECS
    )


def _supported_recipe_pack(catalog: CatalogSnapshot, specs: tuple) -> tuple[CuratedRecipe, ...]:
    items = catalog_items_by_id(catalog)
    packages = _catalog_packages(catalog)
    recipes = []
    for spec in specs:
        ident, name, category, cuisine, servings, ingredients, steps = spec
        if not _coverage(spec, items=items, packages=packages).supported:
            # Keep every ingredient in candidate definitions; never remove one
            # to make a recipe appear cookable or give it an invented quote.
            continue
        recipe = Recipe(
            ident, name, servings,
            tuple(
                RecipeIngredient(items[item_id], Quantity(amount, unit))
                for item_id, amount, unit in ingredients
            ),
        )
        recipes.append(CuratedRecipe(recipe, category, cuisine, tuple(steps)))
    return tuple(sorted(recipes, key=lambda entry: entry.recipe.id))


def starter_recipe_pack(catalog: CatalogSnapshot) -> tuple[CuratedRecipe, ...]:
    """The original 15-recipe starter pack, filtered by exact catalog coverage."""
    return _supported_recipe_pack(catalog, _STARTER)


def kyrgyz_recipe_pack(catalog: CatalogSnapshot) -> tuple[CuratedRecipe, ...]:
    """Only publish Kyrgyz dishes whose EVERY ingredient has a compatible SKU."""
    return _supported_recipe_pack(catalog, KYRGYZ_RECIPE_SPECS)


def available_recipe_pack(catalog: CatalogSnapshot) -> tuple[CuratedRecipe, ...]:
    """One catalog-gated pool for the existing read-only/confirmation APIs."""
    recipes = (*starter_recipe_pack(catalog), *kyrgyz_recipe_pack(catalog))
    ids = [entry.recipe.id for entry in recipes]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate recipe identity across content packs")
    return tuple(sorted(recipes, key=lambda entry: entry.recipe.id))
