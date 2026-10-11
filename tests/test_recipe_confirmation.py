"""Recipe quote → explicit plan confirmation, without fabricated purchases."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest

from examples.m11_local_web import build_demo_app
from household_supply.application.persistence import _require_decision_basis


def _stock(api, item_id: str, amount: str, unit: str, index: int) -> None:
    response = api.handle("POST", "/household/stocktakes", {
        "event_id": f"recipe-confirm-stock-{index}",
        "item_id": item_id,
        "quantity": {"amount": amount, "unit": unit},
        "reason": "kitchen stock check",
    })
    assert response.status == 201, response.body


def _quote(api, amount: str = "500"):
    return api.handle("POST", "/recipes/milk-rice/quote", {
        "budget": {"amount": amount, "currency": "KGS"},
    })


def _confirm(api, token: str):
    return api.handle("POST", "/recipes/milk-rice/confirm", {
        "preview_id": token,
    })


def _prepare(api) -> None:
    _stock(api, "milk", "100", "ml", 1)
    _stock(api, "rice", "1", "kg", 2)


def test_recipe_confirm_saves_exact_quote_and_never_purchases(tmp_path):
    api = build_demo_app(tmp_path).api
    _prepare(api)
    before = api.handle("GET", "/household/history").body
    quote = _quote(api)
    assert quote.status == 200, quote.body
    assert quote.body["preview_id"].startswith("rc-")
    assert quote.body["plan"]["status"] == "feasible"
    assert quote.body["estimated_purchase_cost"] == {"amount": "120", "currency": "KGS"}
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []

    token = quote.body["preview_id"]
    saved = _confirm(api, token)
    assert saved.status == 201, saved.body
    record = saved.body["plan"]
    assert record["plan_id"] == token
    assert record["result"] == quote.body["plan"]
    assert record["request"]["demands"] == [{
        "item_id": "milk", "quantity": {"amount": "300", "unit": "ml"},
    }]
    basis = record["request"]["decision_basis"]
    assert basis["kind"] == "recipe"
    assert basis["preview_id"] == token
    assert basis["recipe_id"] == "milk-rice"
    assert basis["servings"] == "2"
    assert len(basis["household_event_ids"]) == 2
    assert basis["explicit_needs"] == [{
        "item_id": "milk", "quantity": {"amount": "300", "unit": "ml"},
    }]
    assert basis["contributions"] == [{
        "source_id": "recipe:milk-rice",
        "contribution_id": "ingredient:milk",
        "item_id": "milk",
        "quantity": {"amount": "300", "unit": "ml"},
    }]
    assert record["market_evidence"]["offers"]
    assert api.handle("GET", "/household/history").body == before
    assert len(api.handle("GET", "/plans?limit=10").body["plans"]) == 1

    # The saved PlanId is the retry identity, even across process restarts.
    assert _confirm(api, token).body["plan"] == record
    restarted = build_demo_app(tmp_path).api
    assert _confirm(restarted, token).body["plan"] == record
    assert len(restarted.handle("GET", "/plans?limit=10").body["plans"]) == 1
    assert restarted.handle("GET", "/household/history").body == before


def test_recipe_cannot_confirm_unknown_stock_covered_or_unaffordable(tmp_path):
    api = build_demo_app(tmp_path).api
    assert _quote(api).body["preview_id"] is None
    _prepare(api)
    assert _quote(api, amount="0").body["preview_id"] is None
    _stock(api, "milk", "1", "l", 3)
    covered = _quote(api)
    assert covered.body["recipe"]["status"] == "covered"
    assert covered.body["preview_id"] is None
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_recipe_confirmation_rejects_stale_inventory_and_expiration(tmp_path):
    app = build_demo_app(tmp_path)
    api = app.api
    _prepare(api)
    token = _quote(api).body["preview_id"]
    _stock(api, "milk", "1", "l", 3)
    stale = _confirm(api, token)
    assert stale.status == 409
    assert stale.body["error"] == "stale_preview"
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []

    token = _quote(api).body["preview_id"]
    assert token is None  # Fully covered needs no shopping plan.
    _stock(api, "milk", "100", "ml", 4)
    token = _quote(api).body["preview_id"]
    pending = api.recipe_api.confirmation.pending[token]
    api.recipe_api.confirmation.pending[token] = replace(
        pending, staged_at=pending.staged_at - timedelta(hours=1),
    )
    assert _confirm(api, token).status == 409
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_recipe_confirmation_reset_clears_pending_previews(tmp_path):
    api = build_demo_app(tmp_path).api
    _prepare(api)
    token = _quote(api).body["preview_id"]
    assert api.handle("POST", "/local-data/reset", {"confirmation": "RESET"}).status == 200
    assert _confirm(api, token).status == 409
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_recipe_confirmation_strict_schema_and_wrong_identity(tmp_path):
    api = build_demo_app(tmp_path).api
    _prepare(api)
    quote = _quote(api)
    token = quote.body["preview_id"]
    assert api.handle("POST", "/recipes/milk-rice/confirm", {}).status == 422
    assert api.handle("POST", "/recipes/milk-rice/confirm", {
        "preview_id": token, "unexpected": "ignored",
    }).status == 422
    assert api.handle("POST", "/recipes/milk-rice/confirm", {
        "preview_id": "rc-unknown",
    }).status == 409
    assert api.handle("POST", "/recipes/absent/confirm", {
        "preview_id": token,
    }).status == 404

    basis = _confirm(api, token).body["plan"]["request"]["decision_basis"]
    for change in ("amount", "unknown", "duplicate"):
        corrupted = deepcopy(basis)
        if change == "amount":
            missing = next(
                item for item in corrupted["ingredients"]
                if item["item_id"] == "milk"
            )
            missing["missing"]["amount"] = "100"
        elif change == "unknown":
            corrupted["unexpected"] = True
        else:
            corrupted["explicit_needs"].append(deepcopy(corrupted["explicit_needs"][0]))
        with pytest.raises(ValueError):
            _require_decision_basis(corrupted)
