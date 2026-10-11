"""M12.15: one mixed plan, no anonymous per-kg pack or partial authorization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json

import pytest

from examples.m11_local_web import (
    CurrentDemoMarketProvider, build_demo_app, build_demo_catalog,
)
from household_supply.application.mixed_receipt import (
    FileMixedReceiptRepository, MixedActualLine, MixedReceiptConflict,
    MixedReceiptError, MixedReceiptService, MixedReceiptStorageError,
)
from household_supply.application.mixed_plan import (
    FileMixedPlanRepository, MixedPlanConflict, MixedPlanError,
    MixedPlanRecord, MixedPlanService, MixedPlanStorageError,
)
from household_supply.application.service import PlanApplicationService
from household_supply.application.shopping_readiness import (
    ShoppingDemand, ShoppingReadinessService,
)
from household_supply.domain import Money, Quantity
from household_supply.domain.weighted import VerifiedWeightSaleRule
from household_supply.market.catalogs.globus_weight_candidates import WEIGHT_CANDIDATES
from household_supply.market.providers.globus_online import HttpTextResponse
from household_supply.market.providers.globus_weight import GlobusWeightPriceService
from household_supply.web.mixed_plan_api import MixedPlanWebApi
from household_supply.web.mixed_receipt_api import MixedReceiptWebApi


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


def test_opt_in_mixed_http_never_appears_in_standard_offline_or_live_app(tmp_path):
    app, svc, _, _ = runtime(tmp_path)
    assert app.api.handle("POST", "/mixed/plans/preview", {}).status == 404
    from dataclasses import replace

    api = replace(app.api, mixed_plan_api=MixedPlanWebApi(svc))
    with_known_stock(app)
    body = {
        "budget": {"amount": "110", "currency": "KGS"},
        "demands": [
            {"item_id": "rice", "quantity": {"amount": "200", "unit": "g"}},
            {"item_id": "onion", "quantity": {"amount": "350", "unit": "g"}},
        ],
    }
    preview = api.handle("POST", "/mixed/plans/preview", body)
    assert preview.status == 200, preview.body
    assert preview.body["checkout_confirmed"] is False
    assert preview.body["persisted"] is False
    plan_id = preview.body["plan"]["plan_id"]
    assert len(preview.body["plan"]["packages"]) == 1
    assert len(preview.body["plan"]["weights"]) == 1
    assert api.handle("GET", f"/mixed/plans/{plan_id}").status == 404
    assert api.handle("GET", "/household/history").body["event_count"] == 2

    saved = api.handle("POST", "/mixed/plans/confirm", {"plan_id": plan_id})
    assert saved.status == 201, saved.body
    assert saved.body["persisted"]
    assert saved.body["checkout_confirmed"] is False
    fetched = api.handle("GET", f"/mixed/plans/{plan_id}")
    assert fetched.status == 200
    assert fetched.body["plan"] == saved.body["plan"]
    assert api.handle("GET", "/household/history").body["event_count"] == 2
    assert api.handle("GET", "/plans?limit=10").body["plans"] == []

    invalid = dict(body, min_weight={"amount": "1", "unit": "g"})
    assert api.handle("POST", "/mixed/plans/preview", invalid).status == 422
    assert api.handle("GET", "/mixed/plans/preview").status == 405
    assert api.handle("POST", "/mixed/plans/confirm", {
        "plan_id": plan_id, "checkout": True,
    }).status == 422


def test_source_less_globus_weighted_cart_disallows_live_mixed_plan(tmp_path):
    from examples.m11_local_web import build_live_globus_app
    live = build_live_globus_app(tmp_path)
    metadata = live.api.handle("GET", "/catalog/weighted-candidates")
    assert metadata.status == 200
    assert metadata.body["seller_weight_rules_verified"] is False
    assert metadata.body["mixed_plan_enabled"] is False
    assert live.api.handle("POST", "/mixed/plans/preview", {}).status == 404
    assert live.api.handle("POST", "/mixed/plans/confirm", {}).status == 404


def test_seller_rule_can_expire_during_active_preview_before_explicit_save(tmp_path):
    app, service, _, _ = runtime(tmp_path)
    with_known_stock(app)
    now = datetime.now(timezone.utc) + timedelta(seconds=15)
    service.clock = lambda: now
    service.prices = replace(service.prices, clock=lambda: now)
    service.shopping = replace(service.shopping, clock=lambda: now)

    # A controlled rule source, within the 1-hour TTL at preview time,
    # but older than that TTL when the user returns 10 minutes later.
    service.max_rule_age = timedelta(hours=1)
    service.sale_rules = lambda item_id, source_ref: VerifiedWeightSaleRule(
        item_id, source_ref, now - timedelta(minutes=55),
        Quantity("300", "g"), Quantity("100", "g"),
    )
    preview = service.preview(demands=mixed_needs(), budget=Money("110", "KGS"))
    service.clock = lambda: now + timedelta(minutes=10)
    with pytest.raises(MixedPlanConflict, match="weighted evidence expired"):
        service.confirm(preview.record.plan_id)
    assert service.repository.get(preview.record.plan_id) is None


def _receipt_runtime(tmp_path):
    app, mixed, _, _ = runtime(tmp_path)
    with_known_stock(app)
    plan = mixed.confirm(
        mixed.preview(demands=mixed_needs(), budget=Money("110", "KGS")).record.plan_id
    )
    receipt_service = MixedReceiptService(
        plans=mixed.repository,
        receipts=FileMixedReceiptRepository(tmp_path / "mixed-receipts"),
        household=mixed.shopping.household,
        catalog=mixed.package_planner.catalog,
    )
    return app, plan, receipt_service


def _actual(plan, *, mass="290", rice_packs=1, rice_paid="95",
            onion_paid="14.5", fees="0.5", discount="0", total="110",
            approved=True, receipt_ref="invoice:household-test-001"):
    return {
        "plan_id": plan.plan_id,
        "receipt_ref": receipt_ref,
        "occurred_at": datetime.now(timezone.utc),
        "lines": (
            MixedActualLine(
                "onion", "weighted", "", None, Quantity(mass, "g"),
                Money(onion_paid, "KGS"),
            ),
            MixedActualLine(
                "rice", "fixed_package", "rice-1kg", rice_packs,
                Quantity(1000 * rice_packs, "g"), Money(rice_paid, "KGS"),
            ),
        ),
        "delivery_and_fees": Money(fees, "KGS"),
        "discount": Money(discount, "KGS"),
        "total_paid": Money(total, "KGS"),
        "price_changes_approved": approved,
    }


def test_mixed_receipt_explicit_actual_grams_paid_fee_and_once_only_events(tmp_path):
    app, plan, service = _receipt_runtime(tmp_path)
    before = app.api.handle("GET", "/household/history").body["event_count"]
    assert before == 2
    assert service.status(plan.plan_id)["state"] == "not_received"
    receipt = _actual(plan)
    status = service.confirm(**receipt)
    assert status["state"] == "completed"
    assert status["receipt"]["total_paid"] == {
        "amount": "110", "currency": "KGS"
    }
    assert sorted(status["credited_items"]) == ["onion", "rice"]
    assert status["all_demands_fulfilled"] is False
    assert status["budget_remaining_after_paid"] == {"amount": "0", "currency": "KGS"}
    coverage = {row["item_id"]: row for row in status["fulfillment"]}
    assert coverage["onion"]["required_to_buy"] == {"amount": "300", "unit": "g"}
    assert coverage["onion"]["actual_received"] == {"amount": "290", "unit": "g"}
    assert coverage["onion"]["still_missing_after_receipt"] == {"amount": "10", "unit": "g"}
    assert coverage["rice"]["still_missing_after_receipt"] == {"amount": "0", "unit": "g"}
    assert all(x["posted_to_inventory"] for x in status["fulfillment"])
    assert len(service.household.history().events) == before + 2
    state = service.household.state(as_of=datetime.now(timezone.utc))
    assert state.quantity_for("onion") == Quantity("340", "g")
    assert state.quantity_for("rice") == Quantity("1050", "g")
    assert app.api.handle("GET", "/plans?limit=10").body["plans"] == []
    assert service.confirm(**receipt)["state"] == "completed"
    assert len(service.household.history().events) == before + 2

    # A restarted process compares the exact receipt identity, preserving
    # 290g == 0.29kg and 14.50 == 14.5 in canonical values.
    recovered = MixedReceiptService(
        plans=FileMixedPlanRepository(tmp_path / "mixed-plans"),
        receipts=FileMixedReceiptRepository(tmp_path / "mixed-receipts"),
        household=service.household,
        catalog=service.catalog,
    )
    equivalent = dict(receipt)
    equivalent["lines"] = (
        MixedActualLine(
            "onion", "weighted", "", None, Quantity("0.29", "kg"),
            Money("14.50", "KGS"),
        ),
        receipt["lines"][1],
    )
    assert recovered.confirm(**equivalent)["state"] == "completed"
    assert len(recovered.household.history().events) == before + 2


def test_mixed_receipt_rejects_uncounted_or_unapproved_overpriced_delivery(tmp_path):
    app, plan, service = _receipt_runtime(tmp_path)
    prior = len(service.household.history().events)
    cases = [
        ({"lines": _actual(plan)["lines"][:1],
          "total_paid": Money("15", "KGS")}, "every planned Item"),
        ({"lines": _actual(plan)["lines"] + _actual(plan)["lines"][:1]}, "unique"),
        ({"lines": (
            MixedActualLine("onion", "weighted", "", None, Quantity("301", "g"),
                            Money("14.5", "KGS")),
            _actual(plan)["lines"][1],
        )}, "received mass exceeds"),
        ({"price_changes_approved": False}, "delivery/fees need explicit approval"),
        ({"total_paid": Money("111", "KGS")}, "total differs"),
        ({"total_paid": Money("111", "KGS"),
          "delivery_and_fees": Money("1.5", "KGS")}, "approved budget"),
        ({"lines": (
            MixedActualLine("onion", "weighted", "", None, Quantity("290", "g"),
                            Money("20", "KGS")),
            _actual(plan)["lines"][1],
        ), "delivery_and_fees": Money("0", "KGS"),
          "total_paid": Money("115", "KGS")}, "approved budget"),
    ]
    for replacement, message in cases:
        payload = dict(_actual(plan), **replacement)
        with pytest.raises((MixedReceiptError, MixedReceiptConflict), match=message):
            service.confirm(**payload)
    assert len(service.household.history().events) == prior
    assert service.receipts.get(plan.plan_id) is None


def test_receipt_price_change_must_be_explicit_even_with_budget_headroom(tmp_path):
    app, plan, service = _receipt_runtime(tmp_path)
    # Change with equal-budget total: actual onion 16 > quoted 15, rice 93 < 95.
    payload = _actual(
        plan, onion_paid="16", rice_paid="93",
        fees="0", discount="0", total="109", approved=False,
    )
    with pytest.raises(MixedReceiptConflict, match="price increased"):
        service.confirm(**payload)
    payload["price_changes_approved"] = True
    assert service.confirm(**payload)["state"] == "completed"


def test_receipt_one_plan_claim_rejects_changed_payment_reference(tmp_path):
    app, plan, service = _receipt_runtime(tmp_path)
    receipt = _actual(plan)
    service.confirm(**receipt)
    with pytest.raises(MixedReceiptConflict, match="different receipt"):
        service.confirm(**dict(receipt, receipt_ref="invoice:replacement"))
    alternate = dict(receipt)
    alternate["lines"] = (
        MixedActualLine("onion", "weighted", "", None,
                        Quantity("280", "g"), Money("14.5", "KGS")),
        receipt["lines"][1],
    )
    with pytest.raises(MixedReceiptConflict, match="different receipt"):
        service.confirm(**alternate)
    assert len(service.household.history().events) == 4


def test_atomic_receipt_failure_before_batch_commit_is_invisible_and_retriable(tmp_path):
    app, plan, service = _receipt_runtime(tmp_path)
    actual = _actual(plan)
    original = service.household
    from household_supply.household import HouseholdEventRepositoryError

    class FailingRepo:
        def __init__(self, base):
            self.base = base
        def get(self, event_id):
            return self.base.get(event_id)
        def append_batch(self, batch_id, events):
            raise HouseholdEventRepositoryError("simulated failure before commit")

    class FailingHousehold:
        def __init__(self, original_household):
            self.repository = FailingRepo(original_household.repository)
        def history(self):
            return original.history()

    service.household = FailingHousehold(original)
    with pytest.raises(MixedReceiptStorageError, match="atomic household batch not committed"):
        service.confirm(**actual)
    service.household = original
    halfway = service.status(plan.plan_id)
    assert halfway["state"] == "pending_recovery"
    assert halfway["requires_retry"] is True
    assert halfway["credited_items"] == []
    assert len(original.history().events) == 2
    assert original.state(as_of=datetime.now(timezone.utc)).quantity_for("onion") == Quantity("50", "g")
    with pytest.raises(MixedReceiptConflict, match="different receipt"):
        service.confirm(**dict(actual, receipt_ref="invoice:changed-during-recovery"))
    assert service.confirm(**actual)["state"] == "completed"
    assert len(original.history().events) == 4


def test_atomic_receipt_failure_after_commit_is_wholly_visible_and_retriable(tmp_path):
    app, plan, service = _receipt_runtime(tmp_path)
    actual = _actual(plan)
    original = service.household
    from household_supply.household import HouseholdEventRepositoryError

    class FailingRepo:
        def __init__(self, base):
            self.base = base
        def get(self, event_id):
            return self.base.get(event_id)
        def append_batch(self, batch_id, events):
            self.base.append_batch(batch_id, events)
            raise HouseholdEventRepositoryError("simulated crash after DB commit")

    class FailingHousehold:
        def __init__(self, original_household):
            self.repository = FailingRepo(original_household.repository)
        def history(self):
            return original.history()

    service.household = FailingHousehold(original)
    with pytest.raises(MixedReceiptStorageError, match="exact receipt retry required"):
        service.confirm(**actual)
    service.household = original
    pending = service.status(plan.plan_id)
    assert pending["state"] == "pending_recovery"
    assert sorted(pending["credited_items"]) == ["onion", "rice"]
    assert len(original.history().events) == 4
    assert service.confirm(**actual)["state"] == "completed"
    assert len(original.history().events) == 4


def test_corrupted_stored_mixed_receipt_fails_closed(tmp_path):
    _, plan, service = _receipt_runtime(tmp_path)
    service.confirm(**_actual(plan))
    file = service.receipts.root / "intents" / f"{plan.plan_id.value}.json"
    payload = json.loads(file.read_text(encoding="utf-8"))
    payload["body"]["total_paid"]["amount"] = "0"
    file.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(MixedReceiptStorageError, match="safely read"):
        service.status(plan.plan_id)


def test_mixed_receipt_api_guarded_and_has_no_live_globus_access(tmp_path):
    app, plan, service = _receipt_runtime(tmp_path)
    from dataclasses import replace

    api = replace(
        app.api, mixed_receipt_api=MixedReceiptWebApi(service),
    )
    route = f"/mixed/plans/{plan.plan_id.value}/receipt"
    assert app.api.handle("POST", route, {}).status == 404
    assert api.handle("GET", route).body["state"] == "not_received"
    payload = {
        "receipt_ref": "invoice:api-test-001",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "lines": [
            {"kind": "weighted", "item_id": "onion",
             "actual_mass": {"amount": "290", "unit": "g"},
             "paid": {"amount": "14.5", "currency": "KGS"}},
            {"kind": "fixed_package", "item_id": "rice",
             "sku_id": "rice-1kg", "packs": 1,
             "paid": {"amount": "95", "currency": "KGS"}},
        ],
        "delivery_and_fees": {"amount": "0.5", "currency": "KGS"},
        "discount": {"amount": "0", "currency": "KGS"},
        "total_paid": {"amount": "110", "currency": "KGS"},
        "price_changes_approved": True,
    }
    recorded = api.handle("POST", route, payload)
    assert recorded.status == 201, recorded.body
    assert recorded.body["state"] == "completed"
    assert api.handle("GET", route).body["state"] == "completed"
    assert api.handle("POST", route, payload).status == 201
    assert api.handle("POST", route, {**payload, "approval_from_store": True}).status == 422
    assert api.handle("POST", route, {**payload, "lines": payload["lines"][:1]}).status == 422
    assert api.handle("POST", route + "?mutation=1", payload).status == 400
    assert app.api.handle("GET", "/plans?limit=10").body["plans"] == []
    assert app.api.handle("GET", "/household/history").body["event_count"] == 4


def test_mixed_receipt_can_fully_cover_need_with_exact_received_mass(tmp_path):
    app, plan, service = _receipt_runtime(tmp_path)
    receipt = _actual(
        plan, mass="300", onion_paid="15",
        fees="0", discount="0", total="110", approved=False,
    )
    status = service.confirm(**receipt)
    assert status["state"] == "completed"
    assert status["all_demands_fulfilled"] is True
    assert all(x["fulfilled"] for x in status["fulfillment"])
    assert all(
        x["still_missing_after_receipt"]["amount"] == "0"
        for x in status["fulfillment"]
    )


def test_receipt_discount_and_delivery_are_accounted_to_paid_total(tmp_path):
    app, plan, service = _receipt_runtime(tmp_path)
    receipt = _actual(
        plan, fees="1", discount="0.5", total="110", approved=True,
    )
    status = service.confirm(**receipt)
    assert status["state"] == "completed"
    assert status["receipt"]["delivery_and_fees"] == {
        "amount": "1", "currency": "KGS"
    }
    assert status["receipt"]["discount"] == {
        "amount": "0.5", "currency": "KGS"
    }
    assert status["receipt"]["total_paid"] == {
        "amount": "110", "currency": "KGS"
    }
    assert len(service.household.history().events) == 4


def test_receipt_review_preview_validates_without_claim_or_inventory_writes(tmp_path):
    app, plan, service = _receipt_runtime(tmp_path)
    actual = _actual(plan)
    before = len(service.household.history().events)
    for _ in range(2):
        preview = service.preview_actual(**actual)
        assert preview["validated"] is True
        assert preview["persisted"] is False
        assert preview["inventory_updated"] is False
        assert preview["checkout_confirmed"] is False
        assert preview["all_demands_fulfilled"] is False
        assert preview["budget_remaining_after_paid"] == {
            "amount": "0", "currency": "KGS"
        }
        assert {entry["item_id"] for entry in preview["lines"]} == {
            "onion", "rice"
        }
        assert len(service.household.history().events) == before
        assert service.receipts.get(plan.plan_id) is None
        assert service.status(plan.plan_id)["state"] == "not_received"

    with pytest.raises(MixedReceiptConflict, match="received mass exceeds"):
        service.preview_actual(**dict(actual, lines=(
            MixedActualLine(
                "onion", "weighted", "", None, Quantity("350", "g"),
                Money("14.5", "KGS"),
            ),
            actual["lines"][1],
        )))
    assert service.receipts.get(plan.plan_id) is None
    assert len(service.household.history().events) == before


def test_receipt_review_http_preview_and_explicit_immutable_retry(tmp_path):
    app, plan, service = _receipt_runtime(tmp_path)
    api = replace(app.api, mixed_receipt_api=MixedReceiptWebApi(service))
    root = f"/mixed/plans/{plan.plan_id.value}/receipt"
    payload = {
        "receipt_ref": "invoice:review-ui-001",
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "lines": [
            {"kind": "weighted", "item_id": "onion",
             "actual_mass": {"amount": "0.29", "unit": "kg"},
             "paid": {"amount": "14.5", "currency": "KGS"}},
            {"kind": "fixed_package", "item_id": "rice",
             "sku_id": "rice-1kg", "packs": 1,
             "paid": {"amount": "95", "currency": "KGS"}},
        ],
        "delivery_and_fees": {"amount": "0.5", "currency": "KGS"},
        "discount": {"amount": "0", "currency": "KGS"},
        "total_paid": {"amount": "110", "currency": "KGS"},
        "price_changes_approved": True,
    }
    assert app.api.handle("POST", root + "/preview", payload).status == 404
    assert app.api.handle("POST", root + "/retry", {"confirmation": "RETRY"}).status == 404
    before = len(service.household.history().events)
    preview = api.handle("POST", root + "/preview", payload)
    assert preview.status == 200, preview.body
    assert preview.body["validated"] is True
    assert preview.body["requires_explicit_confirmation"] is True
    assert preview.body["inventory_updated"] is False
    assert len(service.household.history().events) == before
    assert api.handle("GET", root).body["state"] == "not_received"
    assert api.handle("POST", root + "/retry", {"confirmation": "RETRY"}).status == 409
    assert api.handle("POST", root + "/preview", {
        **payload, "lines": payload["lines"][:1]
    }).status == 422
    assert api.handle("POST", root + "/preview", {
        **payload, "total_paid": {"amount": "111", "currency": "KGS"}
    }).status == 422
    assert api.handle("POST", root + "/preview?unsafe=1", payload).status == 400
    assert api.handle("GET", root + "/preview").status == 405

    confirmed = api.handle("POST", root, payload)
    assert confirmed.status == 201, confirmed.body
    assert confirmed.body["state"] == "completed"
    assert len(service.household.history().events) == before + 2
    assert api.handle("POST", root + "/retry", {"confirmation": "wrong"}).status == 422
    retried = api.handle("POST", root + "/retry", {"confirmation": "RETRY"})
    assert retried.status == 201
    assert retried.body["state"] == "completed"
    assert len(service.household.history().events) == before + 2
    assert api.handle("POST", root + "/preview", {
        **payload, "receipt_ref": "invoice:new"
    }).status == 409
    assert api.handle("POST", root + "/retry", {
        "confirmation": "RETRY", "override_cost": "1"
    }).status == 422
