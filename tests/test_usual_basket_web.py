from __future__ import annotations

import json

from examples.m11_local_web import build_demo_app
from household_supply.application import FileUsualBasketRepository, UsualBasket


def test_local_demo_routine_update_preview_and_reset(tmp_path):
    app = build_demo_app(tmp_path)
    api = app.api
    assert api.handle("GET", "/household/usual-basket").body == {
        "usual_basket": {"items": []}
    }

    saved = api.handle("POST", "/household/usual-basket", {
        "items": [{
            "item_id": "milk",
            "fallback_quantity": {"amount": "1", "unit": "l"},
        }]
    })
    assert saved.status == 200, saved.body
    assert FileUsualBasketRepository(tmp_path / "usual-basket.json").load().items[0].item_id == "milk"

    before = api.handle("GET", "/household/history").body
    preview = api.handle("POST", "/household/usual-basket/preview", {
        "budget": {"amount": "300", "currency": "KGS"},
        "horizon_days": "7",
    })
    assert preview.status == 200, preview.body
    assert preview.body["ready"]
    assert preview.body["preview_only"]
    assert preview.body["choices"] == [{"item_id": "milk", "basis": "fallback"}]
    assert preview.body["plan"]["status"] == "feasible"
    assert api.handle("GET", "/household/history").body == before
    assert api.handle("GET", "/plans?limit=12").body["plans"] == []

    other = build_demo_app(tmp_path)
    assert other.api.handle("GET", "/household/usual-basket").body == saved.body
    reset = other.api.handle("POST", "/local-data/reset", {"confirmation": "RESET"})
    assert reset.status == 200
    assert FileUsualBasketRepository(tmp_path / "usual-basket.json").load() == UsualBasket()


def test_routine_missing_quantity_does_not_contact_market(tmp_path):
    api = build_demo_app(tmp_path).api
    status = api.handle("POST", "/household/usual-basket", {
        "items": [{"item_id": "milk"}]
    })
    assert status.status == 200
    response = api.handle("POST", "/household/usual-basket/preview", {
        "budget": {"amount": "100", "currency": "KGS"},
        "horizon_days": "7",
    })
    assert response.status == 200
    assert response.body["plan"] is None
    assert response.body["needs_clarification"] == ["milk"]
    assert response.body["preview_only"] is True


def test_invalid_routine_write_keeps_previous_file(tmp_path):
    app = build_demo_app(tmp_path)
    valid = {"items": [{"item_id": "milk", "fallback_quantity": {"amount": "1", "unit": "l"}}]}
    assert app.api.handle("POST", "/household/usual-basket", valid).status == 200
    before = (tmp_path / "usual-basket.json").read_bytes()
    for payload in [
        {"items": [{"item_id": "milk", "fallback_quantity": {"amount": "1", "unit": "kg"}}]},
        {"items": [{"item_id": "nope"}]},
        {"items": [{"item_id": "milk"}, {"item_id": "milk"}]},
    ]:
        response = app.api.handle("POST", "/household/usual-basket", payload)
        assert response.status == 422, response.body
        assert (tmp_path / "usual-basket.json").read_bytes() == before
    assert app.api.handle("POST", "/household/usual-basket", {"items": [], "extra": True}).status == 422
    assert app.api.handle("GET", "/household/usual-basket?x=1").status == 400


def test_preview_rejects_unexpected_inputs_and_keeps_profile(tmp_path):
    app = build_demo_app(tmp_path)
    assert app.api.handle("POST", "/household/usual-basket", {
        "items": [{"item_id": "rice", "fallback_quantity": {"amount": "1", "unit": "kg"}}],
    }).status == 200
    for payload in [
        {"budget": {"amount": "100", "currency": "KGS"}, "horizon_days": "-1"},
        {"budget": {"amount": "100", "currency": "KGS"}, "horizon_days": "7", "unexpected": 1},
        {"budget": {"amount": "100", "currency": "KGS"}, "horizon_days": "7", "exclusions": ["missing"]},
    ]:
        assert app.api.handle("POST", "/household/usual-basket/preview", payload).status == 422
    assert len(app.api.handle("GET", "/household/usual-basket").body["usual_basket"]["items"]) == 1


def test_routine_panel_is_only_local_stand_and_preview_is_read_only():
    from importlib.resources import files

    html = files("household_supply.web").joinpath("assets/index.html").read_text(encoding="utf-8")
    script = files("household_supply.web").joinpath("assets/app.js").read_text(encoding="utf-8")
    assert 'id="usual-basket-panel"' in html
    assert 'id="usual-basket-list"' in html
    assert 'id="preview-usual-basket"' in html
    assert "state.usualBasketDraft" in script
    assert 'request("/household/usual-basket/preview"' in script
    assert "response.preview_only" not in script or "preview" in html
    assert "loadUsualBasket()" in script
    assert "saveUsualBasket()" in script
