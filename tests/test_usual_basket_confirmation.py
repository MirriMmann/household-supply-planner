"""Product gate for inspect → confirm → persist, without synthetic purchases."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from examples.m11_local_web import build_demo_app


def save_milk(api):
    response = api.handle("POST", "/household/usual-basket", {
        "items": [{
            "item_id": "milk",
            "fallback_quantity": {"amount": "1", "unit": "l"},
        }]
    })
    assert response.status == 200, response.body


def preview(api, *, amount="300"):
    return api.handle("POST", "/household/usual-basket/preview", {
        "budget": {"amount": amount, "currency": "KGS"},
        "horizon_days": "7",
    })


def confirm(api, token):
    return api.handle("POST", "/household/usual-basket/confirm", {
        "preview_id": token,
    })


def test_explicit_confirmation_records_exact_displayed_plan_and_no_purchase(tmp_path):
    app = build_demo_app(tmp_path)
    api = app.api
    save_milk(api)
    before = api.handle("GET", "/household/history").body

    candidate = preview(api)
    assert candidate.status == 200, candidate.body
    token = candidate.body["preview_id"]
    assert token.startswith("ub-")
    assert candidate.body["preview_only"]
    assert candidate.body["plan"]["status"] == "feasible"
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []

    stored = confirm(api, token)
    assert stored.status == 201, stored.body
    record = stored.body["plan"]
    assert record["plan_id"] == token
    # Complete, already inspected result, including chosen package, is frozen.
    assert record["result"] == candidate.body["plan"]
    basis = record["request"]["decision_basis"]
    assert basis["kind"] == "usual_basket"
    assert basis["preview_id"] == token
    assert basis["as_of"]
    assert basis["horizon_days"] == "7"
    assert basis["choices"] == [{"item_id": "milk", "basis": "fallback"}]
    assert basis["explicit_needs"] == [{
        "item_id": "milk", "quantity": {"amount": "1", "unit": "l"}
    }]
    assert basis["contributions"][0]["source_id"] == "routine:fallback"
    assert basis["preferences"] == [{
        "item_id": "milk", "fallback_quantity": {"amount": "1", "unit": "l"}
    }]
    assert record["market_evidence"]["offers"]
    assert api.handle("GET", "/household/history").body == before
    assert api.handle("GET", "/plans?limit=10").body["plans"][0]["plan_id"] == token

    # Duplicate POST, even after a server restart, is idempotent.
    assert confirm(api, token).body["plan"] == record
    restarted = build_demo_app(tmp_path)
    assert confirm(restarted.api, token).body["plan"] == record
    assert len(restarted.api.handle("GET", "/plans?limit=10").body["plans"]) == 1
    assert restarted.api.handle("GET", "/household/history").body == before


def test_stale_preview_is_rejected_after_a_real_stocktake(tmp_path):
    api = build_demo_app(tmp_path).api
    save_milk(api)
    token = preview(api).body["preview_id"]
    stocktake = api.handle("POST", "/household/stocktakes", {
        "event_id": "routine-between-preview-and-confirm",
        "item_id": "milk",
        "quantity": {"amount": "2", "unit": "l"},
        "reason": "observed changed inventory",
    })
    assert stocktake.status == 201, stocktake.body
    stale = confirm(api, token)
    assert stale.status == 409
    assert stale.body["error"] == "stale_preview"
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []
    assert api.handle("GET", "/household/history").body["event_count"] == 1


def test_preference_edit_rejects_outdated_preview_without_ghost_plan(tmp_path):
    api = build_demo_app(tmp_path).api
    save_milk(api)
    token = preview(api).body["preview_id"]
    changed = api.handle("POST", "/household/usual-basket", {
        "items": [{
            "item_id": "milk",
            "fallback_quantity": {"amount": "2", "unit": "l"},
        }]
    })
    assert changed.status == 200
    assert confirm(api, token).status == 409
    new = preview(api)
    assert new.body["preview_id"] != token
    assert confirm(api, new.body["preview_id"]).status == 201
    assert len(api.handle("GET", "/plans?limit=10").body["plans"]) == 1


def test_preview_expires_without_persisting_and_reset_invalidates_it(tmp_path):
    app = build_demo_app(tmp_path)
    api = app.api
    save_milk(api)
    token = preview(api).body["preview_id"]
    svc = api.usual_basket_api.confirmation
    old = svc.pending[token]
    svc.pending[token] = replace(
        old, staged_at=old.staged_at - timedelta(hours=1)
    )
    assert confirm(api, token).status == 409
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []

    new = preview(api).body["preview_id"]
    assert api.handle("POST", "/local-data/reset", {"confirmation": "RESET"}).status == 200
    assert confirm(api, new).status == 409
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_no_confirmation_token_for_incomplete_or_infeasible_proposal(tmp_path):
    api = build_demo_app(tmp_path).api
    assert api.handle("POST", "/household/usual-basket", {
        "items": [{"item_id": "milk"}]
    }).status == 200
    incomplete = preview(api)
    assert incomplete.status == 200
    assert not incomplete.body["ready"]
    assert incomplete.body["preview_id"] is None
    assert confirm(api, None).status == 422
    assert len(api.handle("GET", "/plans?limit=10").body["plans"]) == 0

    save_milk(api)
    infeasible = preview(api, amount="0")
    assert infeasible.status == 200
    assert infeasible.body["plan"]["status"] != "feasible"
    assert infeasible.body["preview_id"] is None
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_invalid_confirmation_has_no_side_effects(tmp_path):
    api = build_demo_app(tmp_path).api
    for payload in [
        {"preview_id": "invalid"},
        {"preview_id": ""},
        {"preview_id": "ub-123", "unexpected": True},
        {},
    ]:
        response = api.handle("POST", "/household/usual-basket/confirm", payload)
        assert response.status in {409, 422}, response.body
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_explicitly_confirmed_plan_can_enter_existing_purchase_flow(tmp_path):
    api = build_demo_app(tmp_path).api
    save_milk(api)
    record = confirm(api, preview(api).body["preview_id"]).body["plan"]
    assert api.handle("GET", "/household/history").body["event_count"] == 0
    # Distinct later authority: an actual purchase is an explicit POST.
    bought = api.handle("POST", f"/plans/{record['plan_id']}/purchases", {
        "event_id": "bought-from-usual-plan",
        "sku_id": "milk-1l", "packs": 1,
    })
    assert bought.status == 201, bought.body
    assert api.handle("GET", "/household/history").body["event_count"] == 1


def test_confirmation_never_acquires_a_second_market_snapshot(tmp_path):
    app = build_demo_app(tmp_path)
    api = app.api
    save_milk(api)
    provider = api.usual_basket_api.planner.providers[0]
    original_acquire = provider.acquire
    counts = []

    def tracked_acquire():
        counts.append(1)
        return original_acquire()

    provider.acquire = tracked_acquire
    candidate = preview(api)
    assert candidate.status == 200
    assert len(counts) == 1
    token = candidate.body["preview_id"]
    stored = confirm(api, token)
    assert stored.status == 201
    assert len(counts) == 1, "Confirmation must persist displayed evidence exactly"
    assert stored.body["plan"]["result"] == candidate.body["plan"]


def test_override_and_exclusion_are_explained_without_double_demand(tmp_path):
    api = build_demo_app(tmp_path).api
    assert api.handle("POST", "/household/usual-basket", {
        "items": [
            {"item_id": "milk", "fallback_quantity": {"amount": "1", "unit": "l"}},
            {"item_id": "rice", "fallback_quantity": {"amount": "1", "unit": "kg"}},
        ]
    }).status == 200

    candidate = api.handle("POST", "/household/usual-basket/preview", {
        "budget": {"amount": "1000", "currency": "KGS"},
        "horizon_days": "7",
        "overrides": [{
            "item_id": "milk", "quantity": {"amount": "2", "unit": "l"}
        }],
        "exclusions": ["rice"],
    })
    assert candidate.status == 200, candidate.body
    assert candidate.body["choices"] == [
        {"item_id": "milk", "basis": "override"},
        {"item_id": "rice", "basis": "excluded"},
    ]
    saved = confirm(api, candidate.body["preview_id"])
    assert saved.status == 201, saved.body
    basis = saved.body["plan"]["request"]["decision_basis"]
    assert basis["explicit_needs"] == [
        {"item_id": "milk", "quantity": {"amount": "2", "unit": "l"}}
    ]
    assert [(x["source_id"], x["item_id"]) for x in basis["contributions"]] == [
        ("request:override", "milk")
    ]
    demand = saved.body["plan"]["request"]["demands"]
    assert len(demand) == 1
    assert demand[0]["item_id"] == "milk"
    assert demand[0]["quantity"]["unit"] == "ml"
    assert Decimal(demand[0]["quantity"]["amount"]) == Decimal("2000")
    assert api.handle("GET", "/household/history").body["event_count"] == 0


def test_browser_preview_response_guard_is_present():
    from importlib.resources import files

    script = files("household_supply.web").joinpath("assets/app.js").read_text(
        encoding="utf-8"
    )
    assert "usualBasketPreviewRevision" in script
    assert "const revision = state.usualBasketPreviewRevision" in script
    assert "if (revision !== state.usualBasketPreviewRevision) return;" in script
    assert "if (revision === state.usualBasketPreviewRevision)" in script
