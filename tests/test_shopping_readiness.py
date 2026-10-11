"""M12.14: common shopping list includes ALL recipe ingredients and source gaps."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

import pytest

from examples.m11_local_web import build_demo_app, build_live_globus_app
from household_supply.application.kyrgyz_recipes import KYRGYZ_RECIPE_SPECS
from household_supply.application.shopping_readiness import (
    ShoppingDemand, ShoppingReadinessError, ShoppingReadinessService,
)
from household_supply.domain import Quantity


def _stock(api, item_id: str, amount: str, unit: str = "g"):
    response = api.handle("POST", "/household/stocktakes", {
        "event_id": f"m1214-{item_id}",
        "item_id": item_id,
        "quantity": {"amount": amount, "unit": unit},
        "reason": "observed inventory for shopping review",
    })
    assert response.status == 201, response.body


def _preview(api, *, recipe_id="kg-kuurdak-beef"):
    response = api.handle("POST", "/shopping/preview", {"recipe_id": recipe_id})
    assert response.status == 200, response.body
    return response.body


def test_full_kyrgyz_recipe_catalog_discovery_without_fake_commercial_support(tmp_path):
    app = build_live_globus_app(tmp_path)
    api = app.api
    candidates = api.handle("GET", "/shopping/recipe-candidates")
    assert candidates.status == 200
    ids = {item["recipe_id"] for item in candidates.body["recipes"]}
    assert ids == {spec[0] for spec in KYRGYZ_RECIPE_SPECS}
    assert len(ids) == 5
    assert all(item["definition_only"] for item in candidates.body["recipes"])
    assert api.handle("GET", "/shopping/preview").status == 405

    # Existing packaged recipe discovery MUST remain catalog-gated. No price
    # or shopping authorization leaks into its /recipes/{id}/quote path.
    allowed = api.handle("GET", "/recipes").body["recipes"]
    assert "kg-boorsok-unleavened" in {item["recipe_id"] for item in allowed}
    assert "kg-kuurdak-beef" not in {item["recipe_id"] for item in allowed}
    assert api.handle("POST", "/recipes/kg-kuurdak-beef/quote", {
        "budget": {"amount": "1000", "currency": "KGS"}
    }).status == 404


def test_mixed_recipe_never_silently_drops_unknown_meat_or_vegetables(tmp_path):
    api = build_live_globus_app(tmp_path).api
    before = api.handle("GET", "/household/history").body["event_count"]
    report = _preview(api)
    expected = next(spec for spec in KYRGYZ_RECIPE_SPECS if spec[0] == "kg-kuurdak-beef")
    assert {line["item_id"] for line in report["lines"]} == {
        entry[0] for entry in expected[5]
    }
    by_id = {line["item_id"]: line for line in report["lines"]}
    assert len(by_id) == 7
    assert by_id["beef"]["kind"] == "weighted_reference"
    assert by_id["beef"]["stock_status"] == "unverified"
    assert by_id["beef"]["missing"] is None
    assert by_id["beef"]["source_ref"].startswith("https://globus-online.kg/")
    assert by_id["sunflower_oil"]["kind"] == "fixed_package"
    assert by_id["salt"]["kind"] == "fixed_package"
    assert report["can_save_as_plan"] is False
    assert report["can_quote_packaged_only"] is False
    assert report["inventory_ready"] is False
    assert report["source"] == "recipe"
    assert report["recipe_id"] == "kg-kuurdak-beef"
    assert "inventory_unverified" in report["blockers"]
    assert all(not x["purchase_authorized"] for x in report["lines"])
    assert api.handle("GET", "/household/history").body["event_count"] == before
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_known_stock_yields_explicit_weight_shortages_not_authorized_mixed_plan(tmp_path):
    api = build_live_globus_app(tmp_path).api
    for item_id, grams in (("beef", "100"), ("onion", "50"), ("potato", "450"),
                           ("black_pepper", "0"), ("salt", "20")):
        _stock(api, item_id, grams)
    _stock(api, "sunflower_oil", "100", unit="ml")
    _stock(api, "water", "500", unit="ml")

    report = _preview(api)
    by_id = {line["item_id"]: line for line in report["lines"]}
    assert by_id["beef"]["missing"] == {"amount": "500", "unit": "g"}
    assert by_id["onion"]["missing"] == {"amount": "170", "unit": "g"}
    assert by_id["potato"]["missing"] == {"amount": "50", "unit": "g"}
    assert by_id["black_pepper"]["missing"] == {"amount": "2", "unit": "g"}
    assert by_id["salt"]["stock_status"] == "covered"
    assert by_id["water"]["stock_status"] == "covered"
    assert by_id["sunflower_oil"]["stock_status"] == "covered"
    assert not report["can_quote_packaged_only"]
    assert not report["can_save_as_plan"]
    assert report["blockers"] == ["weighted_sale_rule_unverified"]
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_all_on_hand_recipe_is_cookable_even_if_retailer_cannot_sell_weight(tmp_path):
    api = build_live_globus_app(tmp_path).api
    spec = next(spec for spec in KYRGYZ_RECIPE_SPECS if spec[0] == "kg-manty-beef")
    for item_id, amount, unit in spec[5]:
        _stock(api, item_id, amount, unit=unit)
    report = _preview(api, recipe_id="kg-manty-beef")
    assert report["inventory_ready"] is True
    assert report["status"] == "covered"
    assert report["blockers"] == []
    assert all(line["review_status"] == "already_on_hand" for line in report["lines"])
    assert not report["can_save_as_plan"]
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_manual_shopping_lines_aggregate_repeated_items_before_subtracting_stock(tmp_path):
    api = build_demo_app(tmp_path).api
    _stock(api, "rice", "100")
    request = {
        "demands": [
            {"item_id": "rice", "quantity": {"amount": "80", "unit": "g"}},
            {"item_id": "rice", "quantity": {"amount": "75", "unit": "g"}},
            {"item_id": "onion", "quantity": {"amount": "0.25", "unit": "kg"}},
        ]
    }
    result = api.handle("POST", "/shopping/preview", request)
    assert result.status == 200, result.body
    assert result.body["source"] == "manual"
    by_id = {line["item_id"]: line for line in result.body["lines"]}
    assert by_id["rice"]["required"] == {"amount": "155", "unit": "g"}
    assert by_id["rice"]["missing"] == {"amount": "55", "unit": "g"}
    assert by_id["rice"]["kind"] == "fixed_package"
    assert by_id["onion"]["kind"] == "weighted_reference"
    assert by_id["onion"]["required"] == {"amount": "250", "unit": "g"}
    assert by_id["onion"]["missing"] is None  # unknown != zero
    assert not result.body["can_save_as_plan"]


def test_malformed_mixed_shopping_inputs_fail_without_inventing_market_data(tmp_path):
    api = build_demo_app(tmp_path).api
    bad = [
        {},
        {"recipe_id": "does-not-exist"},
        {"recipe_id": "kg-kuurdak-beef", "demands": []},
        {"demands": []},
        {"demands": [{"item_id": "onion", "quantity": {"amount": "0", "unit": "g"}}]},
        {"demands": [{"item_id": "onion", "quantity": {"amount": "1", "unit": "piece"}},
                     {"item_id": "onion", "quantity": {"amount": "1", "unit": "g"}}]},
        {"demands": [{"item_id": "onion", "quantity": {"amount": "1", "unit": "g"},
                     "seller_rule": {"min": "1g", "step": "1g"}}]},
    ]
    for request in bad:
        response = api.handle("POST", "/shopping/preview", request)
        assert response.status == 422, (request, response.body)
    assert api.handle("POST", "/shopping/preview?price=1", {}).status == 400
    assert api.handle("POST", "/shopping/recipe-candidates", {}).status == 405
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []
    assert api.handle("GET", "/household/history").body["event_count"] == 0


def test_manual_unknown_item_and_wrong_dimension_are_visible_blockers(tmp_path):
    api = build_demo_app(tmp_path).api
    payload = {"demands": [
        {"item_id": "unlisted-fresh-herb", "quantity": {"amount": "12", "unit": "g"}},
        {"item_id": "onion", "quantity": {"amount": "2", "unit": "piece"}},
    ]}
    response = api.handle("POST", "/shopping/preview", payload)
    assert response.status == 200
    by_id = {line["item_id"]: line for line in response.body["lines"]}
    assert by_id["unlisted-fresh-herb"]["kind"] == "unsupported_item"
    assert by_id["unlisted-fresh-herb"]["source_ref"] is None
    assert by_id["onion"]["kind"] == "unsupported_units"
    assert not response.body["can_quote_packaged_only"]
    assert not response.body["can_save_as_plan"]
