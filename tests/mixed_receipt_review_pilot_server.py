"""Private synthetic seller-rule fixture for the M12.18 Chromium E2E gate.

Never enable as a production Globus authority. All prices and rules here
are controlled test data. Available only on loopback in the test process.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from examples.m11_local_web import (
    CurrentDemoMarketProvider, build_demo_app, build_demo_catalog,
)
from household_supply.application.mixed_plan import (
    FileMixedPlanRepository, MixedPlanService,
)
from household_supply.application.mixed_receipt import (
    FileMixedReceiptRepository, MixedReceiptService,
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
from household_supply.web import HouseholdLocalWebApp, serve_local_web
from household_supply.web.mixed_plan_api import MixedPlanWebApi
from household_supply.web.mixed_receipt_api import MixedReceiptWebApi


ONION = next(c for c in WEIGHT_CANDIDATES if c.item_id == "onion")


class SyntheticGlobusTransport:
    def get(self, url, *, timeout_seconds):
        assert url == ONION.source_url
        return HttpTextResponse(
            200, url,
            "<html><body><h1>Лук репчатый</h1>"
            "<div>50 сом/кг</div><button>В корзину</button></body></html>",
        )


def build_fixture(data_dir: Path) -> tuple[HouseholdLocalWebApp, str]:
    app = build_demo_app(data_dir)
    for item, amount in (("rice", "50"), ("onion", "50")):
        result = app.api.handle("POST", "/household/stocktakes", {
            "event_id": f"receipt-ui-initial-{item}", "item_id": item,
            "quantity": {"amount": amount, "unit": "g"},
            "reason": "measured in test fixture",
        })
        assert result.status == 201, result.body

    catalog, entries = build_demo_catalog()
    clock = lambda: datetime.now(timezone.utc)
    household = app.api.api.operations.household
    plan_service = PlanApplicationService(
        catalog, (CurrentDemoMarketProvider(entries),), clock=clock,
    )

    def controlled_rule(item_id: str, source_ref: str):
        assert item_id == "onion" and source_ref == ONION.source_url
        return VerifiedWeightSaleRule(
            item_id, source_ref, clock() - timedelta(hours=1),
            Quantity("300", "g"), Quantity("100", "g"),
        )

    mixed = MixedPlanService(
        shopping=ShoppingReadinessService(household, catalog, clock=clock),
        package_planner=plan_service,
        prices=GlobusWeightPriceService(
            transport=SyntheticGlobusTransport(), clock=clock,
        ),
        sale_rules=controlled_rule,
        repository=FileMixedPlanRepository(data_dir / "mixed-plans"),
        clock=clock,
    )
    pending = mixed.preview(
        demands=(
            ShoppingDemand("onion", Quantity("350", "g")),
            ShoppingDemand("rice", Quantity("200", "g")),
        ),
        budget=Money("110", "KGS"),
    )
    saved = mixed.confirm(pending.record.plan_id)
    receipts = MixedReceiptService(
        plans=mixed.repository,
        receipts=FileMixedReceiptRepository(data_dir / "mixed-receipts"),
        household=household,
        catalog=catalog,
    )
    private_api = replace(
        app.api,
        mixed_plan_api=MixedPlanWebApi(mixed),
        mixed_receipt_api=MixedReceiptWebApi(receipts),
    )
    return replace(app, api=private_api), saved.plan_id.value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    app, plan_id = build_fixture(args.data_dir)
    (args.data_dir / "pilot-plan-id.txt").write_text(
        plan_id, encoding="ascii"
    )
    serve_local_web(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
