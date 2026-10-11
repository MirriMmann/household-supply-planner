"""M12.15: one mixed plan, no anonymous per-kg pack or partial authorization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json

import pytest

from examples.m11_local_web import (
    CurrentDemoMarketProvider, build_demo_app, build_demo_catalog,
)
from household_supply.application.mixed_plan import (
    FileMixedPlanRepository, MixedPlanConflict, MixedPlanError,
    MixedPlanRecord, MixedPlanService, MixedPlanStorageError,
)
from household_supply.application.persistence import PlanId
from household_supply.application.service import PlanApplicationService
from household_supply.application.shopping_readiness import (
    ShoppingDemand, ShoppingReadinessService,
)
from household_supply.domain import Money, Quantity
from household_supply.domain.weighted import VerifiedWeightSaleRule
from household_supply.market.catalogs.globus_weight_candidates import WEIGHT_CANDIDATES
from household_supply.market.providers.globus_online import HttpTextResponse
from household_supply.market.providers.globus_weight import GlobusWeightPriceService


ONION = next(c for c in WEIGHT_CANDIDATES if c.item_id == "onion")


class FakeTransport:
    def __init__(self, price="50"):
        self.calls = []
        self.price = price

    def get(self, url, *, timeout_seconds):
        self.calls.append(url)
        assert url == ONION.source_url
        html = (
            "<html><body><header>Укажите адрес доставки Корзина</header>"
            f"<h1>Лук репчатый фас вес</h1><div>{self.price} сом/кг</div>"
            "<button>В корзину</button><div>Это демо-каталог</div></body></html>"
        )
        return HttpTextResponse(200, url, html)


def runtime(tmp_path, *, rule=True, price="50"):
    app = build_demo_app(tmp_path)
    catalog, entries = build_demo_catalog()
    clock = lambda: datetime.now(timezone.utc)
    household = app.api.api.operations.household
    planner = PlanApplicationService(
        catalog, (CurrentDemoMarketProvider(entries),), clock=clock
    )
    transport = FakeTransport(price)
    prices = GlobusWeightPriceService(transport=transport, clock=clock)
    provider_calls = []

    def provider(item_id: str, source_ref: str):
        provider_calls.append((item_id, source_ref))
        if not rule:
            return None
        assert item_id == "onion" and source_ref == ONION.source_url
        return VerifiedWeightSaleRule(
            "onion", ONION.source_url, clock() - timedelta(hours=1),
            Quantity("300", "g"), Quantity("100", "g"),
        )

    service = MixedPlanService(
        shopping=ShoppingReadinessService(household, catalog, clock=clock),
        package_planner=planner, prices=prices, sale_rules=provider,
        repository=FileMixedPlanRepository(tmp_path / "mixed-plans"), clock=clock,
    )
    return app, service, transport, provider_calls


def stock(api, item: str, amount: str, unit: str = "g"):
    result = api.handle("POST", "/household/stocktakes", {
        "event_id": f"mixed-stock-{item}",
        "item_id": item,
        "quantity": {"amount": amount, "unit": unit},
        "reason": "measured in kitchen",
    })
    assert result.status == 201, result.body


def mixed_needs():
    return (
        ShoppingDemand("onion", Quantity("350", "g")),
        ShoppingDemand("rice", Quantity("200", "g")),
    )


def with_known_stock(app):
    stock(app.api, "onion", "50")
    stock(app.api, "rice", "50")


def test_mixed_one_plan_uses_weight_minimum_and_full_pack_in_one_budget(tmp_path):
    app, svc, transport, rules = runtime(tmp_path)
    with_known_stock(app)
    before = app.api.handle("GET", "/household/history").body["event_count"]
    preview = svc.preview(demands=mixed_needs(), budget=Money("110", "KGS"))
    record = preview.record
    assert record.budget == Money("110", "KGS")
    assert record.estimated_total == Money("110", "KGS")  # 400g * 50/kg + one 1kg 90? see demo rice
    assert record.plan_id.value.startswith("mp-")
    assert record.source == "manual"
    assert {d.item_id for d in record.demands} == {"rice", "onion"}
    assert len(record.weights) == 1
    assert record.weights[0].record.quote.requested == Quantity("300", "g")
    assert record.weights[0].record.quote.sellable == Quantity("300", "g")
    assert record.weights[0].cost == Money("15", "KGS")
    assert len(record.packages) == 1
    assert record.packages[0].item_id == "rice"
    assert record.packages[0].packs == 1
    assert record.packages[0].cost == Money("95", "KGS")
    assert record.packages[0].source
    assert transport.calls == [ONION.source_url]
    assert rules == [("onion", ONION.source_url)]
    assert svc.repository.get(record.plan_id) is None
    assert app.api.handle("GET", "/household/history").body["event_count"] == before

    persisted = svc.confirm(record.plan_id)
    assert persisted == record
    assert svc.confirm(record.plan_id) == record
    assert svc.repository.get(record.plan_id) == record
    assert app.api.handle("GET", "/household/history").body["event_count"] == before
    reopened = FileMixedPlanRepository(tmp_path / "mixed-plans")
    assert reopened.get(record.plan_id) == record


def test_missing_seller_rule_blocks_entire_plan_no_ghost_package(tmp_path):
    app, svc, transport, rules = runtime(tmp_path, rule=False)
    with_known_stock(app)
    with pytest.raises(MixedPlanError, match="no seller-verified"):
        svc.preview(demands=mixed_needs(), budget=Money("1000", "KGS"))
    assert len(transport.calls) == 1 and len(rules) == 1
    assert list((tmp_path / "mixed-plans").glob("*.json")) == []
    assert app.api.handle("GET", "/plans?limit=10").body["plans"] == []


def test_budget_including_weight_rejects_incomplete_packaged_part(tmp_path):
    app, svc, _, _ = runtime(tmp_path)
    with_known_stock(app)
    with pytest.raises(MixedPlanError, match="packaged part cannot cover"):
        svc.preview(demands=mixed_needs(), budget=Money("100", "KGS"))
    with pytest.raises(MixedPlanError, match="weighted purchases alone"):
        svc.preview(demands=mixed_needs(), budget=Money("10", "KGS"))
    assert list((tmp_path / "mixed-plans").glob("*.json")) == []


def test_unknown_household_stock_never_means_zero(tmp_path):
    app, svc, transport, rules = runtime(tmp_path)
    stock(app.api, "onion", "50")
    with pytest.raises(MixedPlanError, match="inventory_unknown"):
        svc.preview(demands=mixed_needs(), budget=Money("1000", "KGS"))
    assert transport.calls == [] and rules == []


def test_stale_preview_blocked_after_stock_change(tmp_path):
    app, svc, _, _ = runtime(tmp_path)
    with_known_stock(app)
    pending = svc.preview(demands=mixed_needs(), budget=Money("110", "KGS"))
    stock(app.api, "milk", "100", unit="ml")
    with pytest.raises(MixedPlanConflict, match="household state changed"):
        svc.confirm(pending.record.plan_id)
    assert svc.repository.get(pending.record.plan_id) is None


def test_no_partial_plan_when_weight_source_is_wrong_currency_or_price(tmp_path):
    app, svc, _, _ = runtime(tmp_path, price="550")
    with_known_stock(app)
    with pytest.raises(MixedPlanError, match="weighted purchases alone"):
        svc.preview(demands=mixed_needs(), budget=Money("100", "KGS"))


def test_immutable_mixed_plan_fails_closed_when_tampered(tmp_path):
    app, svc, _, _ = runtime(tmp_path)
    with_known_stock(app)
    record = svc.confirm(svc.preview(
        demands=mixed_needs(), budget=Money("110", "KGS")
    ).record.plan_id)
    target = svc.repository.root / f"{record.plan_id.value}.json"
    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["plan"]["packages"][0]["packs"] = 2
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MixedPlanStorageError, match="integrity"):
        svc.repository.get(record.plan_id)


def test_mixed_plan_strictly_revalidates_lines_after_snapshot_reparse(tmp_path):
    app, svc, _, _ = runtime(tmp_path)
    with_known_stock(app)
    record = svc.preview(demands=mixed_needs(), budget=Money("110", "KGS")).record
    mapped = record.to_mapping()
    restored = MixedPlanRecord.from_mapping(mapped)
    assert restored == record
    mapped["estimated_total"]["amount"] = "1"
    with pytest.raises(MixedPlanError, match="estimated total"):
        MixedPlanRecord.from_mapping(mapped)


def test_weight_only_requested_shopping_can_save_governed_plan(tmp_path):
    app, svc, _, _ = runtime(tmp_path)
    stock(app.api, "onion", "50")
    pending = svc.preview(
        demands=(ShoppingDemand("onion", Quantity("350", "g")),),
        budget=Money("15", "KGS"),
    )
    assert pending.record.packages == ()
    assert len(pending.record.weights) == 1
    assert svc.confirm(pending.record.plan_id) == pending.record
    assert app.api.handle("GET", "/household/history").body["event_count"] == 1
