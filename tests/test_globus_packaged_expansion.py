"""M12.11: use actual packaged SKU links without pretending weight goods are packs."""

from __future__ import annotations

from urllib.parse import urlsplit

from examples.m11_local_web import build_demo_app
from household_supply.application.recipe_catalog import kyrgyz_recipe_coverage
from household_supply.market.catalogs.globus_demo_staples import (
    build_globus_demo_staples_catalog,
)
from household_supply.market.catalogs.globus_weight_candidates import (
    WEIGHT_CANDIDATES,
    serialize_weight_candidates,
)
from household_supply.market.providers.globus_online import GlobusOnlineListing


PACKAGED_ITEMS = {
    "paprika": 2,
    "turmeric": 2,
    "dried_parsley": 1,
    "dried_basil": 1,
    "canned_corn": 3,
    "tomato_paste": 3,
    "vinegar_9_percent": 1,
    "breadcrumbs": 1,
    "pepper_blend": 1,
    "dishwashing_liquid": 2,
    "toothpaste": 2,
    "liquid_hand_soap": 1,
    "dish_sponge": 2,
    "toilet_paper_roll": 2,
    "trash_bag_30l": 1,
    "trash_bag_60l": 1,
    "laundry_powder_color": 1,
    "shampoo": 2,
}
WEIGHT_ITEM_IDS = {
    "onion", "potato", "beef", "beef_mince", "carrot", "tomato",
    "bell_pepper", "garlic", "chicken_fillet", "black_pepper",
}


def test_exact_new_packaged_stock_coverage_and_binding_integrity():
    catalog, listings = build_globus_demo_staples_catalog()
    assert len(catalog.skus) == 90
    assert len({sku.item.id for sku in catalog.skus}) == 45
    assert len(catalog.bindings) == len(catalog.skus) == len(listings)
    assert len({listing.external_product_id for listing in listings}) == len(listings)
    assert len({sku.id for sku in catalog.skus}) == len(catalog.skus)

    for item_id, count in PACKAGED_ITEMS.items():
        skus = [sku for sku in catalog.skus if sku.item.id == item_id]
        assert len(skus) == count
        assert len({sku.package_quantity.dimension for sku in skus}) == 1
        assert all(sku.package_quantity.amount > 0 for sku in skus)
        assert all(sku.package_quantity.unit in {"g", "kg", "ml", "piece"} for sku in skus)

    # A pepper BLEND is not pure black pepper, and concentrated tomato paste
    # is not a measured fresh tomato ingredient in a recipe.
    ids = {sku.item.id for sku in catalog.skus}
    assert not WEIGHT_ITEM_IDS & ids
    assert "pepper_blend" in ids
    assert "black_pepper" not in ids
    assert "tomato_paste" in ids
    assert "tomato" not in ids
    assert {sku.package_quantity.amount for sku in catalog.skus if sku.item.id == "toilet_paper_roll"} == {4, 8}
    assert {sku.package_quantity.amount for sku in catalog.skus if sku.item.id == "dish_sponge"} == {1, 7}
    assert {sku.package_quantity.amount for sku in catalog.skus if sku.item.id == "trash_bag_30l"} == {20}
    assert "trash_bag_30l" != "trash_bag_60l"


def test_weight_reference_sources_are_bounded_and_not_offer_authority():
    catalog, listings = build_globus_demo_staples_catalog()
    listed_ids = {listing.external_product_id for listing in listings}
    candidate_ids = [row.item_id for row in WEIGHT_CANDIDATES]
    assert len(WEIGHT_CANDIDATES) == len(WEIGHT_ITEM_IDS) == 10
    assert set(candidate_ids) == WEIGHT_ITEM_IDS
    assert len(set(candidate_ids)) == len(candidate_ids)
    refs = [GlobusOnlineListing(candidate.source_url) for candidate in WEIGHT_CANDIDATES]
    assert not listed_ids & {ref.external_product_id for ref in refs}
    assert len({ref.external_product_id for ref in refs}) == len(refs)
    rows = serialize_weight_candidates()
    assert len(rows) == 10
    assert all(row["plannable"] is False for row in rows)
    assert all(row["blocked_reason"] == "variable_weight_not_supported" for row in rows)
    assert all("price" not in row and "available" not in row for row in rows)
    assert all(
        urlsplit(row["source_url"]).hostname == "globus-online.kg"
        for row in rows
    )


def test_readonly_weight_sources_do_not_change_plans_or_household(tmp_path):
    api = build_demo_app(tmp_path).api
    before = api.handle("GET", "/household/history").body
    result = api.handle("GET", "/catalog/weighted-candidates")
    assert result.status == 200
    assert len(result.body["candidates"]) == 10
    assert all(not record["plannable"] for record in result.body["candidates"])
    assert "Весовые" in result.body["notice"]
    assert api.handle("POST", "/catalog/weighted-candidates", {}).status == 405
    assert api.handle("GET", "/catalog/weighted-candidates?q=onion").status == 400
    assert api.handle("GET", "/household/history").body == before
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_recipes_stay_blocked_without_meat_vegetable_and_pure_pepper_skus():
    catalog, _ = build_globus_demo_staples_catalog()
    gaps = {row.recipe_id: row for row in kyrgyz_recipe_coverage(catalog)}
    assert gaps["kg-boorsok-unleavened"].supported
    assert not gaps["kg-kuurdak-beef"].supported
    assert not gaps["kg-manty-beef"].supported
    assert not gaps["kg-oromo-beef-potato"].supported
    assert not gaps["kg-lagman-hand-pulled"].supported
    assert "black_pepper" in gaps["kg-kuurdak-beef"].missing_item_ids
