from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from household_supply.application import (
    HouseholdClosedLoopJsonApi,
    HouseholdReplenishmentService,
    InMemoryPlanRepository,
    PlanApplicationService,
    PlanLifecycleService,
)
from household_supply.domain import (
    CatalogBinding,
    CatalogSnapshot,
    ExternalListingKey,
    Item,
    MarketAcquisitionBatch,
    MarketObservation,
    Money,
    Quantity,
    SKU,
)
from household_supply.household import HouseholdLearningService, InMemoryHouseholdEventRepository
from household_supply.market import StaticMarketProvider
from household_supply.web import (
    HouseholdLocalWebApp,
    HouseholdWebJsonApi,
    serialize_web_catalog,
    serve_local_web,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)


def make_web_app() -> HouseholdLocalWebApp:
    milk = Item("milk", "Milk", "dairy")
    rice = Item("rice", "Rice", "pantry")
    milk_sku = SKU("milk-1l", milk, "Milk 1 L", Quantity("1", "l"))
    rice_sku = SKU("rice-1kg", rice, "Rice 1 kg", Quantity("1", "kg"))
    skus = (milk_sku, rice_sku)
    bindings = tuple(
        CatalogBinding(
            ExternalListingKey("fixture", "store-a", sku.id),
            sku.id,
            "test fixture",
        )
        for sku in skus
    )
    catalog = CatalogSnapshot(skus, bindings)
    observations = tuple(
        MarketObservation(
            id=f"obs-{sku.id}",
            provider_id="fixture",
            seller_id="store-a",
            external_product_id=sku.id,
            price=Money("120" if sku.id == "milk-1l" else "95", "KGS"),
            observed_at=NOW,
            package_quantity=sku.package_quantity,
            source_ref=f"fixture://{sku.id}",
        )
        for sku in skus
    )
    provider = StaticMarketProvider(MarketAcquisitionBatch("fixture", NOW, observations))
    planner = PlanApplicationService(catalog, (provider,), clock=lambda: NOW)
    plans = PlanLifecycleService(planner, InMemoryPlanRepository(), clock=lambda: NOW)
    household = HouseholdLearningService(InMemoryHouseholdEventRepository())
    replenishment = HouseholdReplenishmentService(household, plans, clock=lambda: NOW)
    api = HouseholdWebJsonApi(HouseholdClosedLoopJsonApi(replenishment), catalog)
    return HouseholdLocalWebApp(api)


async def asgi_request(
    app,
    *,
    method: str,
    path: str,
    body: bytes = b"",
    query: bytes = b"",
    content_type: bytes | None = None,
    host: bytes | None = b"127.0.0.1:8765",
    origin: bytes | None = None,
):
    sent = []
    messages = iter([{"type": "http.request", "body": body, "more_body": False}])

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    headers = []
    if host is not None:
        headers.append((b"host", host))
    if content_type is not None:
        headers.append((b"content-type", content_type))
    if origin is not None:
        headers.append((b"origin", origin))
    await app(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": query,
            "headers": headers,
        },
        receive,
        send,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    response_body = next(message for message in sent if message["type"] == "http.response.body")
    return start, response_body


def header_map(start) -> dict[bytes, bytes]:
    return {key.lower(): value for key, value in start["headers"]}


def test_web_catalog_is_canonical_and_does_not_expose_listing_bindings() -> None:
    app = make_web_app()
    payload = serialize_web_catalog(app.api.catalog)
    assert [item["item_id"] for item in payload["items"]] == ["milk", "rice"]
    assert [sku["sku_id"] for sku in payload["skus"]] == ["milk-1l", "rice-1kg"]
    assert payload["skus"][0]["package_quantity"] == {"amount": "1", "unit": "l"}
    encoded = json.dumps(payload)
    assert "external_product_id" not in encoded
    assert "seller_id" not in encoded


def test_catalog_route_is_read_only_strict_and_delegates_other_routes() -> None:
    app = make_web_app()
    assert app.api.handle("GET", "/catalog").status == 200
    assert app.api.handle("POST", "/catalog", {}).status == 405
    assert app.api.handle("GET", "/catalog?x=1").status == 400
    assert app.api.handle("GET", "/household/state").status == 200
    assert app.api.handle("GET", "/health").body == {"status": "ok"}


def test_local_web_serves_fixed_assets_with_browser_security_headers() -> None:
    app = make_web_app()
    start, body = asyncio.run(asgi_request(app, method="GET", path="/"))
    headers = header_map(start)
    assert start["status"] == 200
    assert headers[b"content-type"] == b"text/html; charset=utf-8"
    assert b"default-src 'self'" in headers[b"content-security-policy"]
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"
    text = body["body"].decode("utf-8")
    assert "Составить покупки" in text
    assert 'lang="ru"' in text
    assert "Что есть дома" in text
    assert "Нужно что-то обязательно?" in text
    for legacy in ("Extra needs", "Horizon, days", "Record a stocktake", "Depletion evidence", "Build replenishment plan"):
        assert legacy not in text
    assert '<script src="/assets/app.js" defer></script>' in text
    assert "<script>" not in text

    js_start, js_body = asyncio.run(asgi_request(app, method="GET", path="/assets/app.js"))
    assert js_start["status"] == 200
    assert b"text/javascript" in header_map(js_start)[b"content-type"]
    assert b"/household/stocktakes" in js_body["body"]
    assert b"/plans/" in js_body["body"]
    assert b"sessionStorage" in js_body["body"]
    assert b"formEventId" in js_body["body"]
    assert b".style" not in js_body["body"]


def test_local_web_head_and_static_route_policy() -> None:
    app = make_web_app()
    start, body = asyncio.run(asgi_request(app, method="HEAD", path="/assets/styles.css"))
    assert start["status"] == 200
    assert body["body"] == b""
    assert int(header_map(start)[b"content-length"]) > 100

    start, _ = asyncio.run(asgi_request(app, method="POST", path="/"))
    assert start["status"] == 405
    start, _ = asyncio.run(asgi_request(app, method="GET", path="/", query=b"x=1"))
    assert start["status"] == 400


def test_unknown_static_path_is_not_a_filesystem_read() -> None:
    app = make_web_app()
    start, body = asyncio.run(asgi_request(app, method="GET", path="/assets/../../pyproject.toml"))
    assert start["status"] == 404
    assert json.loads(body["body"])["error"] == "not_found"


def test_browser_surface_runs_stocktake_plan_and_purchase_confirmation() -> None:
    app = make_web_app()
    stocktake = {
        "event_id": "web-stocktake-1",
        "item_id": "milk",
        "quantity": {"amount": "2", "unit": "l"},
        "reason": "browser stocktake",
    }
    start, _ = asyncio.run(
        asgi_request(
            app,
            method="POST",
            path="/household/stocktakes",
            body=json.dumps(stocktake).encode(),
            content_type=b"application/json",
        )
    )
    assert start["status"] == 201

    plan = {
        "budget": {"amount": "1000", "currency": "KGS"},
        "horizon_days": "7",
        "explicit_needs": [
            {"item_id": "milk", "quantity": {"amount": "3", "unit": "l"}}
        ],
    }
    start, body = asyncio.run(
        asgi_request(
            app,
            method="POST",
            path="/plans",
            body=json.dumps(plan).encode(),
            content_type=b"application/json",
        )
    )
    assert start["status"] == 201
    created = json.loads(body["body"])
    record = created["plan"]
    assert record["result"]["purchases"][0]["packs"] == 1

    confirmation = {"event_id": "web-purchase-1", "sku_id": "milk-1l", "packs": 1}
    start, body = asyncio.run(
        asgi_request(
            app,
            method="POST",
            path=f"/plans/{record['plan_id']}/purchases",
            body=json.dumps(confirmation).encode(),
            content_type=b"application/json",
        )
    )
    assert start["status"] == 201
    confirmed = json.loads(body["body"])
    assert confirmed["purchase"]["actual_packs"] == 1
    assert confirmed["purchase"]["planned_packs"] == 1



def test_local_web_rejects_dns_rebinding_host_and_cross_origin_mutation() -> None:
    app = make_web_app()
    start, _ = asyncio.run(
        asgi_request(app, method="GET", path="/", host=b"attacker.example")
    )
    assert start["status"] == 400

    start, _ = asyncio.run(asgi_request(app, method="GET", path="/", host=None))
    assert start["status"] == 400

    stocktake = {
        "event_id": "origin-stocktake",
        "item_id": "milk",
        "quantity": {"amount": "1", "unit": "l"},
    }
    start, _ = asyncio.run(
        asgi_request(
            app,
            method="POST",
            path="/household/stocktakes",
            body=json.dumps(stocktake).encode(),
            content_type=b"application/json",
            origin=b"https://attacker.example",
        )
    )
    assert start["status"] == 403

    start, _ = asyncio.run(
        asgi_request(
            app,
            method="POST",
            path="/household/stocktakes",
            body=json.dumps(stocktake).encode(),
            content_type=b"application/json",
            origin=b"http://127.0.0.1:9999",
        )
    )
    assert start["status"] == 403

    start, _ = asyncio.run(
        asgi_request(
            app,
            method="POST",
            path="/household/stocktakes",
            body=json.dumps(stocktake).encode(),
            content_type=b"application/json",
            origin=b"http://127.0.0.1:8765",
        )
    )
    assert start["status"] == 201

def test_mass_market_russian_ux_contract_has_no_primary_unit_selector() -> None:
    app = make_web_app()
    start, body = asyncio.run(asgi_request(app, method="GET", path="/"))
    assert start["status"] == 200
    text = body["body"].decode("utf-8")
    assert "На сколько дней закупаемся?" in text
    assert "Сколько готовы потратить?" in text
    assert "Найти продукт" in text
    assert "Отметить запасы" in text
    assert 'id="stocktake-unit"' not in text
    assert 'id="stocktake-amount"' not in text
    assert 'id="plan-currency" type="hidden"' in text

    js_start, js_body = asyncio.run(asgi_request(app, method="GET", path="/assets/app.js"))
    assert js_start["status"] == 200
    javascript = js_body["body"].decode("utf-8")
    assert "Половина" in javascript
    assert "1 упаковка" in javascript
    assert "friendlyError" in javascript
    assert "collectMustHaves" in javascript


def test_local_web_refuses_remote_binding_by_default() -> None:
    with pytest.raises(ValueError, match="non-loopback"):
        serve_local_web(object(), host="0.0.0.0")
    with pytest.raises(ValueError, match="port"):
        serve_local_web(object(), port=0)
