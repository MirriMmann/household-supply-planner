"""Kyrgyz recipe pack: exact ingredients, real SKU coverage, no invented offers."""

from __future__ import annotations

from examples.m11_local_web import build_demo_catalog, build_live_globus_app
from household_supply.application.kyrgyz_recipes import KYRGYZ_RECIPE_SPECS
from household_supply.application.recipe_catalog import (
    available_recipe_pack,
    kyrgyz_recipe_coverage,
    kyrgyz_recipe_pack,
    starter_recipe_pack,
)
from household_supply.domain import CatalogSnapshot, Item, Quantity, SKU
from household_supply.market.catalogs.globus_demo_staples import (
    build_globus_demo_staples_catalog,
)


EXPECTED_IDS = {
    "kg-boorsok-unleavened",
    "kg-kuurdak-beef",
    "kg-manty-beef",
    "kg-oromo-beef-potato",
    "kg-lagman-hand-pulled",
}


def test_five_measured_candidates_no_hidden_ingredients_or_placeholder_items():
    assert {spec[0] for spec in KYRGYZ_RECIPE_SPECS} == EXPECTED_IDS
    assert len(KYRGYZ_RECIPE_SPECS) == len(EXPECTED_IDS)
    for identity, name, category, cuisine, servings, ingredients, steps in KYRGYZ_RECIPE_SPECS:
        assert identity.startswith("kg-")
        assert name and category and "кыргызская" in cuisine
        assert servings > 0 and steps
        item_ids = [entry[0] for entry in ingredients]
        assert len(item_ids) == len(set(item_ids))
        assert all(Quantity(amount, unit).amount > 0 for _, amount, unit in ingredients)


def test_globus_supported_boorsok_only_while_other_four_remain_catalog_gaps():
    catalog, _ = build_globus_demo_staples_catalog()
    coverage = {row.recipe_id: row for row in kyrgyz_recipe_coverage(catalog)}
    assert set(coverage) == EXPECTED_IDS
    assert coverage["kg-boorsok-unleavened"].supported
    assert not coverage["kg-boorsok-unleavened"].missing_item_ids
    assert not coverage["kg-boorsok-unleavened"].incompatible_unit_item_ids

    expected_missing = {
        "kg-kuurdak-beef": {"beef", "potato", "onion", "black_pepper"},
        "kg-manty-beef": {"beef", "onion", "black_pepper"},
        "kg-oromo-beef-potato": {"beef", "potato", "onion", "black_pepper"},
        "kg-lagman-hand-pulled": {
            "beef", "onion", "carrot", "tomato", "bell_pepper", "garlic", "black_pepper",
        },
    }
    for recipe_id, missing in expected_missing.items():
        assert not coverage[recipe_id].supported
        assert set(coverage[recipe_id].missing_item_ids) == missing
        assert coverage[recipe_id].incompatible_unit_item_ids == ()

    published = kyrgyz_recipe_pack(catalog)
    assert [recipe.recipe.id for recipe in published] == ["kg-boorsok-unleavened"]
    assert published[0].recipe.name == "Боорсок из пресного теста"
    assert {part.item.id for part in published[0].recipe.ingredients} == {
        "flour", "water", "salt", "sunflower_oil",
    }
    combined = available_recipe_pack(catalog)
    assert len(combined) == len(starter_recipe_pack(catalog)) + 1
    assert len({recipe.recipe.id for recipe in combined}) == len(combined)


def test_offline_demo_does_not_fake_sunflower_oil_or_unknown_staples():
    catalog, _ = build_demo_catalog()
    assert kyrgyz_recipe_pack(catalog) == ()
    boorsok = next(
        row for row in kyrgyz_recipe_coverage(catalog)
        if row.recipe_id == "kg-boorsok-unleavened"
    )
    assert set(boorsok.missing_item_ids) == {"flour", "water", "salt", "sunflower_oil"}
    assert boorsok.incompatible_unit_item_ids == ()


def test_all_five_appear_only_with_real_item_ids_and_matching_package_units():
    # Synthetic catalog identities, NOT prices, market offers or stock readings.
    by_id = {}
    for _, _, _, _, _, ingredients, _ in KYRGYZ_RECIPE_SPECS:
        for item_id, amount, unit in ingredients:
            by_id.setdefault(item_id, (amount, unit))
    catalog = CatalogSnapshot(tuple(
        SKU(
            f"test-{item_id}", Item(item_id, item_id, "test"),
            f"test {item_id}", Quantity(amount, unit),
        )
        for item_id, (amount, unit) in sorted(by_id.items())
    ), ())
    assert all(row.supported for row in kyrgyz_recipe_coverage(catalog))
    assert {entry.recipe.id for entry in kyrgyz_recipe_pack(catalog)} == EXPECTED_IDS


def test_incompatible_package_unit_excludes_entire_recipe():
    catalog, _ = build_globus_demo_staples_catalog()
    # Replace just the one flour SKU's package dimension; no inference that
    # mass of flour can be met with a 'piece' package.
    skus = tuple(
        SKU(sku.id, sku.item, sku.name, Quantity("1", "piece"), brand=sku.brand)
        if sku.item.id == "flour" else sku
        for sku in catalog.skus
    )
    incompatible = CatalogSnapshot(skus, catalog.bindings)
    gap = next(
        x for x in kyrgyz_recipe_coverage(incompatible)
        if x.recipe_id == "kg-boorsok-unleavened"
    )
    assert "flour" in gap.incompatible_unit_item_ids
    assert not gap.supported
    assert kyrgyz_recipe_pack(incompatible) == ()


def test_existing_web_flow_lists_only_purchasable_catalog_supported_dishes(tmp_path):
    # GET recipe discovery/unknown-stock quote must never contact live Globus.
    api = build_live_globus_app(tmp_path).api
    listing = api.handle("GET", "/recipes")
    assert listing.status == 200
    identities = {recipe["recipe_id"] for recipe in listing.body["recipes"]}
    assert "kg-boorsok-unleavened" in identities
    assert not (identities & (EXPECTED_IDS - {"kg-boorsok-unleavened"}))
    boorsok = next(
        item for item in listing.body["recipes"]
        if item["recipe_id"] == "kg-boorsok-unleavened"
    )
    assert boorsok["status"] == "unverified"
    assert boorsok["cuisine"] == "кыргызская"
    assert all(ingredient["status"] == "unverified" for ingredient in boorsok["ingredients"])
    quote = api.handle("POST", "/recipes/kg-boorsok-unleavened/quote", {
        "budget": {"amount": "1000", "currency": "KGS"}
    })
    assert quote.status == 200
    assert quote.body["plan"] is None
    assert quote.body["preview_id"] is None
    assert api.handle("POST", "/recipes/kg-kuurdak-beef/quote", {
        "budget": {"amount": "1000", "currency": "KGS"}
    }).status == 404
    assert api.handle("GET", "/plans?limit=12").body["plans"] == []
    assert api.handle("GET", "/household/history").body["event_count"] == 0
