"""M12.12: live per-kg *reference* pricing, separately gated from purchase plans."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from examples.m11_local_web import build_demo_app
from household_supply.domain.money import Money
from household_supply.domain.quantity import Quantity
from household_supply.domain.weighted import (
    WeightedPriceObservation, VerifiedWeightSaleRule,
    estimate_weight_cost, quote_verified_mass,
)
from household_supply.market.catalogs.globus_weight_candidates import WEIGHT_CANDIDATES
from household_supply.market.providers.globus_online import (
    GlobusOnlineFetchError, GlobusOnlineParseError,
    GlobusOnlineUnsupportedListingError, HttpTextResponse,
    parse_globus_online_weight_product,
)
from household_supply.market.providers.globus_weight import GlobusWeightPriceService
from household_supply.web.weighted_api import WeightedReferenceWebApi


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
ONION = next(row for row in WEIGHT_CANDIDATES if row.item_id == "onion")
PEPPER = next(row for row in WEIGHT_CANDIDATES if row.item_id == "black_pepper")


def _page(name: str, price: str, *, header="Укажите адрес доставки Корзина") -> str:
    return (
        f"<html><body><header>{header}</header>"
        f"<main><h1>{name}</h1><div>{price}</div>"
        "<button>В корзину</button>"
        "<div>Это демо-каталог</div><hr/></main>"
        "</body></html>"
    )


@dataclass
class FakeTransport:
    html: str
    final_url: str
    status: int = 200

    def __post_init__(self):
        self.calls = []

    def get(self, url: str, *, timeout_seconds: float) -> HttpTextResponse:
        self.calls.append((url, timeout_seconds))
        return HttpTextResponse(self.status, self.final_url, self.html)


def _service(item=ONION, *, price="52 сом/кг 52 сом/кг", final_url=None):
    transport = FakeTransport(
        _page(item.product_name, price),
        item.source_url if final_url is None else final_url,
    )
    return GlobusWeightPriceService(
        transport=transport, clock=lambda: NOW,
    ), transport


def test_weight_parser_only_accepts_product_scoped_unambiguous_kg_reference():
    result = parse_globus_online_weight_product(
        _page("Лук репчатый фас вес", "52 сом/кг 52 сом/кг")
    )
    assert result.price_per_kg == Money("52", "KGS")
    assert result.available
    assert result.name == "Лук репчатый фас вес"

    result = parse_globus_online_weight_product(
        _page("Чеснок вес", "69,9 сом/кг 69,9 сом/кг")
    )
    assert result.price_per_kg == Money("69.9", "KGS")
    for price in ("52 сом", "52 сом/кг 46 сом", "52 сом/кг 68 сом/кг", ""):
        with pytest.raises((GlobusOnlineParseError, GlobusOnlineUnsupportedListingError)):
            parse_globus_online_weight_product(_page("Лук", price))
    with pytest.raises(GlobusOnlineParseError, match="demo scope"):
        parse_globus_online_weight_product(
            _page("Лук", "52 сом/кг", header="Главная")
            .replace("Это демо-каталог", "")
        )
    with pytest.raises(GlobusOnlineParseError, match="h1"):
        parse_globus_online_weight_product(
            "<html><body>Это демо-каталог 52 сом/кг</body></html>"
        )


def test_parser_sold_out_never_makes_fake_price():
    response = parse_globus_online_weight_product(
        _page("Лук", "Раскупили")
    )
    assert response.price_per_kg is None
    assert response.available is False


def test_weight_reference_source_scoped_exact_decimal_cost_and_cents():
    svc, transport = _service(price="52 сом/кг 52 сом/кг")
    result = svc.estimate(item_id="onion", requested=Quantity("375", "g"))
    assert len(transport.calls) == 1
    assert transport.calls[0][0] == ONION.source_url
    assert result.requested == Quantity("375", "g")
    assert result.observation.reference_mass == Quantity("1", "kg")
    assert result.estimated_upper_bound == Money("19.50", "KGS")
    assert not result.is_purchase_quote
    assert result.observation.source_ref == ONION.source_url
    assert result.observation.observed_at == NOW

    pepper_svc, _ = _service(PEPPER, price="515 сом/кг")
    pepper = pepper_svc.estimate(item_id="black_pepper", requested=Quantity("15", "g"))
    assert pepper.observation.reference_mass == Quantity("1", "kg")
    assert pepper.estimated_upper_bound == Money("7.73", "KGS")


def test_weight_reference_rejects_injection_cross_listing_bad_mass():
    svc, transport = _service(final_url=PEPPER.source_url)
    with pytest.raises(GlobusOnlineFetchError):
        svc.estimate(item_id="onion", requested=Quantity("250", "g"))
    assert len(transport.calls) == 1
    with pytest.raises(ValueError):
        svc.estimate(item_id="unknown", requested=Quantity("200", "g"))
    with pytest.raises(ValueError):
        svc.estimate(item_id="onion", requested=Quantity("1", "piece"))
    with pytest.raises(ValueError):
        svc.estimate(item_id="onion", requested=Quantity("0", "kg"))


def test_verified_weight_rule_needs_min_and_increment_and_exact_evidence():
    obs = WeightedPriceObservation(
        "onion", ONION.source_url, NOW, Money("56", "KGS"),
        Quantity("1", "kg"), True,
    )
    # This is EXPLICIT SYNTHETIC TEST EVIDENCE, not a rule asserted for Globus.
    rule = VerifiedWeightSaleRule(
        "onion", ONION.source_url, NOW - timedelta(hours=1),
        Quantity("300", "g"), Quantity("100", "g"),
    )
    q = quote_verified_mass(obs, rule, Quantity("350", "g"))
    assert q.sellable == Quantity("400", "g")
    assert q.estimated_upper_bound == Money("22.40", "KGS")
    assert quote_verified_mass(obs, rule, Quantity("50", "g")).sellable == Quantity("300", "g")
    assert quote_verified_mass(obs, rule, Quantity("400", "g")).sellable == Quantity("400", "g")
    assert quote_verified_mass(obs, rule, Quantity("401", "g")).sellable == Quantity("500", "g")
    assert estimate_weight_cost(obs, Quantity("1", "g")) == Money("0.06", "KGS")

    with pytest.raises(ValueError, match="mismatched"):
        quote_verified_mass(obs, replace(rule, item_id="potato"), Quantity("350", "g"))
    with pytest.raises(ValueError, match="newer"):
        quote_verified_mass(obs, replace(rule, verified_at=NOW + timedelta(seconds=1)), Quantity("350", "g"))
    with pytest.raises(ValueError, match="positive mass"):
        VerifiedWeightSaleRule("onion", ONION.source_url, NOW, Quantity("0", "g"), Quantity("100", "g"))
    with pytest.raises(ValueError, match="positive mass"):
        VerifiedWeightSaleRule("onion", ONION.source_url, NOW, Quantity("100", "g"), Quantity("1", "piece"))
    with pytest.raises(ValueError, match="inconsistent|violates"):
        replace(q, sellable=Quantity("350", "g"))


def test_weight_endpoint_is_readonly_and_keeps_old_plan_and_stock_unchanged(tmp_path):
    app = build_demo_app(tmp_path)
    service, transport = _service(price="40 сом/кг")
    api = replace(app.api, weighted_api=WeightedReferenceWebApi(service))
    original = app.api.handle("GET", "/household/history").body
    assert app.api.handle("POST", "/catalog/weighted-candidates/estimate", {}).status == 404

    response = api.handle("POST", "/catalog/weighted-candidates/estimate", {
        "item_id": "onion",
        "quantity": {"amount": "250", "unit": "g"},
    })
    assert response.status == 200, response.body
    assert response.body["reference_unit_price"] == {
        "amount": "40", "currency": "KGS",
        "per": {"amount": "1", "unit": "kg"},
    }
    assert Decimal(response.body["estimated_upper_bound"]["amount"]) == Decimal("10")
    assert response.body["plannable"] is False
    assert response.body["estimate_only"] is True
    assert response.body["minimum_sellable_quantity"] is None
    assert response.body["weight_increment"] is None
    assert response.body["listing_available"] is True
    assert response.body["source_ref"] == ONION.source_url
    assert len(transport.calls) == 1

    assert api.handle("GET", "/household/history").body == original
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []
    assert api.handle("GET", "/catalog/weighted-candidates/estimate").status == 405
    assert api.handle("POST", "/catalog/weighted-candidates/estimate?x=1", {}).status == 400
    assert api.handle("POST", "/catalog/weighted-candidates/estimate", {
        "item_id": "onion",
        "quantity": {"amount": "1", "unit": "piece"},
    }).status == 422
    assert api.handle("POST", "/catalog/weighted-candidates/estimate", {
        "item_id": "onion",
        "quantity": {"amount": "250", "unit": "g"},
        "price": {"amount": "1", "currency": "KGS"},
    }).status == 422
    assert len(transport.calls) == 1


def test_weight_unavailable_listing_returns_no_cost_not_free_goods(tmp_path):
    app = build_demo_app(tmp_path)
    svc, _ = _service(price="Раскупили")
    api = replace(app.api, weighted_api=WeightedReferenceWebApi(svc))
    result = api.handle("POST", "/catalog/weighted-candidates/estimate", {
        "item_id": "onion", "quantity": {"amount": "2", "unit": "kg"},
    })
    assert result.status == 200, result.body
    assert result.body["reference_unit_price"] is None
    assert result.body["estimated_upper_bound"] is None
    assert result.body["listing_available"] is False
