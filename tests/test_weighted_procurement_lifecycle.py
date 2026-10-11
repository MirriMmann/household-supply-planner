"""End-to-end opt-in weighted plans: verified seller rules, persistence, real facts.

These use a SYNTHETIC attested minimum/step. No assertion that public Globus
currently permits the tested weights or prices.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from household_supply.application.weighted_lifecycle import (
    FileWeightedPlanRepository, WeightedCompletionClaim, WeightedProcurementConflict,
    WeightedProcurementError, WeightedProcurementService, WeightedStorageError,
)
from household_supply.domain import Money, Quantity
from household_supply.domain.weighted import VerifiedWeightSaleRule
from household_supply.household import (
    FileHouseholdEventRepository, HouseholdEventId, HouseholdLearningService,
    PurchaseEvent,
)
from household_supply.market.catalogs.globus_weight_candidates import WEIGHT_CANDIDATES
from household_supply.market.providers.globus_online import HttpTextResponse
from household_supply.market.providers.globus_weight import GlobusWeightPriceService
from household_supply.web.weighted_procurement_api import WeightedProcurementWebApi
from examples.m11_local_web import build_demo_app


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
ONION = next(x for x in WEIGHT_CANDIDATES if x.item_id == "onion")


class SyntheticTransport:
    def __init__(self, price="52"):
        self.price = price
        self.calls = 0

    def get(self, url, *, timeout_seconds):
        self.calls += 1
        assert url == ONION.source_url
        html = (
            "<html><body><header>Укажите адрес доставки Корзина</header>"
            f"<main><h1>Лук репчатый фас вес</h1><div>{self.price} сом/кг</div>"
            "<button>В корзину</button><div>Это демо-каталог</div></main></body></html>"
        )
        return HttpTextResponse(200, url, html)


def _runtime(root, *, source=True, now=NOW, price="52"):
    repo = FileWeightedPlanRepository(root / "weighted")
    household = HouseholdLearningService(FileHouseholdEventRepository(root / "events"))
    transport = SyntheticTransport(price)
    prices = GlobusWeightPriceService(transport=transport, clock=lambda: now)
    calls = []

    def verified_rule(item_id, source_ref):
        calls.append((item_id, source_ref))
        if not source:
            return None
        assert (item_id, source_ref) == ("onion", ONION.source_url)
        return VerifiedWeightSaleRule(
            "onion", ONION.source_url, now - timedelta(hours=1),
            Quantity("300", "g"), Quantity("100", "g"),
        )
    service = WeightedProcurementService(
        prices=prices, sale_rules=verified_rule, repository=repo,
        household=household, clock=lambda: now,
    )
    return service, transport, calls


def test_preview_does_not_mutate_stored_plan_or_household(tmp_path):
    service, transport, calls = _runtime(tmp_path)
    p = service.preview("onion", Quantity("350", "g"), Money("25", "KGS"))
    assert p.record.plan_id.value.startswith("wp-")
    assert p.record.quote.requested == Quantity("350", "g")
    assert p.record.quote.sellable == Quantity("400", "g")
    assert p.record.quote.estimated_upper_bound == Money("20.80", "KGS")
    assert p.record.budget == Money("25", "KGS")
    assert transport.calls == 1
    assert calls == [("onion", ONION.source_url)]
    assert service.repository.get_plan(p.record.plan_id) is None
    assert service.household.history().events == ()


def test_explicit_plan_then_actual_receipt_updates_exact_mass_only_once(tmp_path):
    service, _, _ = _runtime(tmp_path)
    preview = service.preview("onion", Quantity("350", "g"), Money("25", "KGS"))
    record = service.save_plan(preview.record.plan_id)
    assert record.quote.sellable == Quantity("400", "g")
    assert service.save_plan(preview.record.plan_id) == record
    assert service.household.history().events == ()

    event_id = HouseholdEventId("receipt-onion-001")
    receipt = service.confirm_actual(
        plan_id=record.plan_id, event_id=event_id,
        actual_mass=Quantity("380", "g"), actual_cost=Money("19.76", "KGS"),
        receipt_ref="receipt:supplied-001",
    )
    assert receipt.actual_mass == Quantity("380", "g")
    assert receipt.actual_cost == Money("19.76", "KGS")
    assert len(service.household.history().events) == 1
    fact = service.household.history().events[0]
    assert isinstance(fact, PurchaseEvent)
    assert fact.quantity == Quantity("380", "g")
    assert fact.sku_id == ""
    assert fact.source_ref.startswith(f"weighted-plan:{record.plan_id.value}:")
    assert service.household.state(as_of=NOW).quantity_for("onion") == Quantity("380", "g")

    # Restart reconstructs the exact approved plan/receipt; no extra purchase.
    reopened, _, _ = _runtime(tmp_path)
    assert reopened.repository.get_plan(record.plan_id) == record
    assert reopened.repository.get_receipt(event_id) == receipt
    again = reopened.confirm_actual(
        plan_id=record.plan_id, event_id=event_id,
        actual_mass=Quantity("380", "g"), actual_cost=Money("19.76", "KGS"),
        receipt_ref="receipt:supplied-001",
    )
    assert again == receipt
    assert len(reopened.household.history().events) == 1


def test_budget_and_missing_sale_rule_fail_closed_without_events(tmp_path):
    no_rule, transport, _ = _runtime(tmp_path / "untrusted", source=False)
    with pytest.raises(WeightedProcurementError, match="no trusted seller"):
        no_rule.preview("onion", Quantity("350", "g"), Money("100", "KGS"))
    assert transport.calls == 1
    assert no_rule.household.history().events == ()

    service, _, _ = _runtime(tmp_path / "budget")
    with pytest.raises(WeightedProcurementError, match="budget"):
        service.preview("onion", Quantity("350", "g"), Money("20", "KGS"))
    assert service.household.history().events == ()


def test_actual_overlimit_or_changed_receipt_or_second_event_rejected(tmp_path):
    service, _, _ = _runtime(tmp_path)
    p = service.preview("onion", Quantity("350", "g"), Money("25", "KGS"))
    plan = service.save_plan(p.record.plan_id)
    event_id = HouseholdEventId("receipt-100")
    kwargs = {
        "plan_id": plan.plan_id,
        "event_id": event_id,
        "actual_mass": Quantity("380", "g"),
        "actual_cost": Money("19.76", "KGS"),
        "receipt_ref": "receipt:100",
    }
    with pytest.raises(WeightedProcurementConflict, match="mass exceeds"):
        service.confirm_actual(**{**kwargs, "actual_mass": Quantity("401", "g")})
    with pytest.raises(WeightedProcurementConflict, match="cost exceeds"):
        service.confirm_actual(**{**kwargs, "actual_cost": Money("21", "KGS")})
    assert service.household.history().events == ()
    service.confirm_actual(**kwargs)
    with pytest.raises(WeightedProcurementConflict, match="already claimed|ID conflicts"):
        service.confirm_actual(**{**kwargs, "actual_cost": Money("19.50", "KGS")})
    with pytest.raises(WeightedProcurementConflict, match="already claimed|ID conflicts"):
        service.confirm_actual(**{**kwargs, "receipt_ref": "receipt:other"})
    with pytest.raises(WeightedProcurementConflict, match="another completed purchase"):
        service.confirm_actual(**{**kwargs, "event_id": HouseholdEventId("receipt-101")})
    assert len(service.household.history().events) == 1


def test_tampered_plan_and_receipt_fail_integrity_check(tmp_path):
    service, _, _ = _runtime(tmp_path)
    p = service.preview("onion", Quantity("350", "g"), Money("25", "KGS"))
    plan = service.save_plan(p.record.plan_id)
    target = service.repository.root / "plans" / f"{plan.plan_id.value}.json"
    raw = json.loads(target.read_text())
    raw["body"]["budget"]["amount"] = "9999"
    target.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(WeightedStorageError, match="integrity"):
        service.repository.get_plan(plan.plan_id)


def test_past_price_and_future_seller_rule_are_not_accepted(tmp_path):
    service, _, _ = _runtime(tmp_path / "stale")
    # Provider's price evidence predates the application clock by > 20 minutes.
    service.clock = lambda: NOW + timedelta(hours=2)
    with pytest.raises(WeightedProcurementConflict, match="stale"):
        service.preview("onion", Quantity("350", "g"), Money("25", "KGS"))


def test_preview_is_not_restartable_without_explicit_save(tmp_path):
    service, _, _ = _runtime(tmp_path)
    p = service.preview("onion", Quantity("350", "g"), Money("25", "KGS"))
    restarted, _, _ = _runtime(tmp_path)
    with pytest.raises(WeightedProcurementConflict, match="no verified weighted preview"):
        restarted.save_plan(p.record.plan_id)
    assert restarted.repository.get_plan(p.record.plan_id) is None


def test_opt_in_http_requires_explicit_preview_save_and_real_receipt(tmp_path):
    app = build_demo_app(tmp_path)
    service, transport, _ = _runtime(tmp_path)
    # Share the same event store as the normal HSM household operations API.
    service.household = app.api.api.operations.household
    from dataclasses import replace

    api = replace(
        app.api, weighted_procurement_api=WeightedProcurementWebApi(service)
    )
    assert app.api.handle("POST", "/weighted/plans/preview", {}).status == 404
    initial = api.handle("GET", "/household/state").body["household"]

    preview = api.handle("POST", "/weighted/plans/preview", {
        "item_id": "onion",
        "quantity": {"amount": "350", "unit": "g"},
        "budget": {"amount": "25", "currency": "KGS"},
    })
    assert preview.status == 200, preview.body
    plan_id = preview.body["plan"]["plan_id"]
    assert preview.body["persisted"] is False
    assert preview.body["plan"]["sellable"] == {"amount": "400", "unit": "g"}
    assert api.handle("GET", f"/weighted/plans/{plan_id}").status == 404
    refreshed = api.handle("GET", "/household/state").body["household"]
    assert refreshed["balances"] == initial["balances"]
    assert refreshed["applied_event_ids"] == initial["applied_event_ids"]

    saved = api.handle("POST", "/weighted/plans/confirm", {"plan_id": plan_id})
    assert saved.status == 201, saved.body
    assert saved.body["persisted"] is True
    assert api.handle("GET", f"/weighted/plans/{plan_id}").status == 200
    refreshed = api.handle("GET", "/household/state").body["household"]
    assert refreshed["balances"] == initial["balances"]
    assert refreshed["applied_event_ids"] == initial["applied_event_ids"]

    purchase = {
        "event_id": "weight-paid-onion-1",
        "actual_mass": {"amount": "375", "unit": "g"},
        "actual_cost": {"amount": "19.50", "currency": "KGS"},
        "receipt_ref": "receipt:verified-test-1",
    }
    result = api.handle("POST", f"/weighted/plans/{plan_id}/purchases", purchase)
    assert result.status == 201, result.body
    assert result.body["purchase_confirmed"] is True
    assert result.body["receipt"]["actual_mass"] == {"amount": "375", "unit": "g"}
    assert api.handle("GET", "/household/state").body["household"]["balances"] == [
        {"item_id": "onion", "quantity": {"amount": "375", "unit": "g"}}
    ]
    assert api.handle("POST", f"/weighted/plans/{plan_id}/purchases", purchase).status == 201
    assert api.handle("POST", f"/weighted/plans/{plan_id}/purchases", {
        **purchase, "actual_cost": {"amount": "19.49", "currency": "KGS"},
    }).status == 409
    assert api.handle("POST", "/weighted/plans/preview", {
        "item_id": "onion", "quantity": {"amount": "350", "unit": "g"},
        "budget": {"amount": "25", "currency": "KGS"},
        "minimum": {"amount": "100", "unit": "g"},
    }).status == 422
    assert api.handle("GET", "/household/history").body["event_count"] == 1
    assert transport.calls == 1


def test_registered_weighted_item_can_be_stocktaken_without_fake_sku(tmp_path):
    api = build_demo_app(tmp_path).api
    assert api.handle("POST", "/household/stocktakes", {
        "event_id": "stock-onion-100",
        "item_id": "onion",
        "quantity": {"amount": "250", "unit": "g"},
        "reason": "observed by household",
    }).status == 201
    state = api.handle("GET", "/household/state").body["household"]
    assert state["balances"] == [
        {"item_id": "onion", "quantity": {"amount": "250", "unit": "g"}}
    ]
    assert api.handle("POST", "/household/stocktakes", {
        "event_id": "stock-onion-101",
        "item_id": "onion",
        "quantity": {"amount": "1", "unit": "piece"},
        "reason": "incompatible unit",
    }).status == 422
    assert api.handle("POST", "/household/stocktakes", {
        "event_id": "stock-unknown-101",
        "item_id": "unknown-fresh-product",
        "quantity": {"amount": "200", "unit": "g"},
        "reason": "unsupported source",
    }).status == 422
    assert api.handle("GET", "/household/history").body["event_count"] == 1


def test_weighted_receipt_idempotence_across_grams_and_kilograms(tmp_path):
    service, _, _ = _runtime(tmp_path)
    p = service.preview("onion", Quantity("350", "g"), Money("25", "KGS"))
    plan = service.save_plan(p.record.plan_id)
    event_id = HouseholdEventId("receipt-onion-unit-equivalent")
    first = service.confirm_actual(
        plan_id=plan.plan_id, event_id=event_id,
        actual_mass=Quantity("375", "g"), actual_cost=Money("19.50", "KGS"),
        receipt_ref="receipt:unit-compatible",
    )
    second = service.confirm_actual(
        plan_id=plan.plan_id, event_id=event_id,
        actual_mass=Quantity("0.375", "kg"), actual_cost=Money("19.50", "KGS"),
        receipt_ref="receipt:unit-compatible",
    )
    assert first == second
    assert len(service.household.history().events) == 1


def test_atomic_plan_completion_claim_blocks_second_event_across_instances(tmp_path):
    one, _, _ = _runtime(tmp_path)
    preview = one.preview("onion", Quantity("350", "g"), Money("25", "KGS"))
    plan = one.save_plan(preview.record.plan_id)
    two, _, _ = _runtime(tmp_path)

    # Mimic a different worker that reserved the plan but crashed before
    # appending the household event. There must be no second authorization.
    prior = WeightedCompletionClaim(
        plan.plan_id, HouseholdEventId("receipt-prior-worker"), "a" * 64
    )
    one.repository.claim_completion(prior)
    assert two.repository.get_completion(plan.plan_id) == prior
    one.repository.claim_completion(prior)
    with pytest.raises(WeightedProcurementConflict, match="already claimed"):
        two.confirm_actual(
            plan_id=plan.plan_id,
            event_id=HouseholdEventId("receipt-second-worker"),
            actual_mass=Quantity("375", "g"),
            actual_cost=Money("19.50", "KGS"),
            receipt_ref="receipt:second-worker",
        )
    assert one.household.history().events == ()
