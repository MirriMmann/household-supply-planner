"""M12.7 product gate: reuse approved inputs, recompute all facts and offers."""

from __future__ import annotations

from examples.m11_local_web import build_demo_app


def set_routine(api, *, amount="1"):
    response = api.handle("POST", "/household/usual-basket", {
        "items": [{
            "item_id": "milk",
            "fallback_quantity": {"amount": amount, "unit": "l"},
        }],
    })
    assert response.status == 200, response.body


def make_preview(api, *, budget="500", days="7"):
    candidate = api.handle("POST", "/household/usual-basket/preview", {
        "budget": {"amount": budget, "currency": "KGS"},
        "horizon_days": days,
    })
    assert candidate.status == 200, candidate.body
    return candidate.body


def save_preview(api, *, budget="500", days="7"):
    candidate = make_preview(api, budget=budget, days=days)
    assert candidate["preview_id"]
    saved = api.handle("POST", "/household/usual-basket/confirm", {
        "preview_id": candidate["preview_id"]
    })
    assert saved.status == 201, saved.body
    return saved.body["plan"]


def repeat_settings(api):
    response = api.handle("GET", "/household/usual-basket/last-settings")
    assert response.status == 200, response.body
    return response.body["repeat_settings"]


def test_repeat_unavailable_without_explicitly_confirmed_routine(tmp_path):
    api = build_demo_app(tmp_path).api
    assert repeat_settings(api) is None
    set_routine(api)
    assert make_preview(api)["preview_id"]
    # A preview alone does not establish the user's reusable preference.
    assert repeat_settings(api) is None
    assert api.handle("POST", "/household/usual-basket/last-settings", {}).status == 405
    assert api.handle("GET", "/household/usual-basket/last-settings?x=1").status == 400


def test_repeat_defaults_survive_restart_without_another_database(tmp_path):
    api = build_demo_app(tmp_path).api
    set_routine(api)
    first = save_preview(api, budget="700", days="3")
    assert repeat_settings(api) == {
        "source_plan_id": first["plan_id"],
        "budget": {"amount": "700", "currency": "KGS"},
        "horizon_days": "3",
    }
    reopened = build_demo_app(tmp_path).api
    assert repeat_settings(reopened) == repeat_settings(api)
    assert reopened.handle("GET", "/household/history").body["event_count"] == 0


def test_only_confirmed_routines_set_defaults_even_when_another_plan_is_newer(tmp_path):
    api = build_demo_app(tmp_path).api
    set_routine(api)
    first = save_preview(api, budget="700", days="3")
    generic = api.handle("POST", "/plans", {
        "budget": {"amount": "1000", "currency": "KGS"},
        "horizon_days": "14",
        "explicit_needs": [{
            "item_id": "milk", "quantity": {"amount": "2", "unit": "l"}
        }],
    })
    assert generic.status == 201, generic.body
    defaults = repeat_settings(api)
    assert defaults["source_plan_id"] == first["plan_id"]
    assert defaults["budget"]["amount"] == "700"
    assert defaults["horizon_days"] == "3"


def test_repeat_recalculates_after_stocktake_and_does_not_reuse_old_plan(tmp_path):
    api = build_demo_app(tmp_path).api
    set_routine(api)
    first = save_preview(api, budget="500", days="7")
    previous_count = len(api.handle("GET", "/plans?limit=10").body["plans"])
    assert previous_count == 1

    corrected = api.handle("POST", "/household/stocktakes", {
        "event_id": "repeat-stale-inventory",
        "item_id": "milk",
        "quantity": {"amount": "2", "unit": "l"},
        "reason": "actual household stocktake",
    })
    assert corrected.status == 201, corrected.body
    settings = repeat_settings(api)
    assert settings["source_plan_id"] == first["plan_id"]
    fresh = make_preview(
        api,
        budget=settings["budget"]["amount"],
        days=settings["horizon_days"],
    )
    assert fresh["preview_id"] != first["plan_id"]
    # Reuse only the old budget/horizon; actual inventory comes from live history.
    assert fresh["plan"]["status"] == "feasible"
    assert fresh["plan"]["purchases"] == []
    assert len(api.handle("GET", "/plans?limit=10").body["plans"]) == previous_count
    assert api.handle("GET", "/household/history").body["event_count"] == 1


def test_repeat_selects_newest_approved_routine_settings(tmp_path):
    api = build_demo_app(tmp_path).api
    set_routine(api)
    first = save_preview(api, budget="500", days="7")
    second = save_preview(api, budget="850", days="14")
    latest = repeat_settings(api)
    assert latest["source_plan_id"] == second["plan_id"]
    assert latest["source_plan_id"] != first["plan_id"]
    assert latest["budget"]["amount"] == "850"
    assert latest["horizon_days"] == "14"


def test_repeat_defaults_disappear_after_explicit_reset(tmp_path):
    api = build_demo_app(tmp_path).api
    set_routine(api)
    save_preview(api)
    assert repeat_settings(api)
    reset = api.handle("POST", "/local-data/reset", {"confirmation": "RESET"})
    assert reset.status == 200
    assert repeat_settings(api) is None


def test_stand_has_explicit_repeat_action_and_no_stale_sku_replay():
    from importlib.resources import files

    assets = files("household_supply.web").joinpath("assets")
    html = assets.joinpath("index.html").read_text(encoding="utf-8")
    js = assets.joinpath("app.js").read_text(encoding="utf-8")
    assert 'id="repeat-usual-basket"' in html
    assert '"/household/usual-basket/last-settings"' in js
    assert 'byId("repeat-usual-basket").addEventListener("click", repeatUsualBasket)' in js
    assert "await previewUsualBasket()" in js
    assert "applyPreviousRoutineInputs(current.repeat_settings)" in js
    assert "revision !== state.usualBasketPreviewRevision" in js
