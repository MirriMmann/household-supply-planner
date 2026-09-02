"""Offline M7 vertical slice: compute -> durable JSON record -> read history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from tempfile import TemporaryDirectory

from household_supply import (
    ApplicationPlanRequest,
    CatalogBinding,
    CatalogSnapshot,
    ExternalListingKey,
    FilePlanRepository,
    GlobusOnlineDemoProvider,
    GlobusOnlineListing,
    HttpTextResponse,
    Item,
    Money,
    PlanApplicationService,
    PlanId,
    PlanLifecycleService,
    Quantity,
    RequestedItem,
    SKU,
)

MILK_URL = "https://globus-online.kg/ru-kg/good/23df8084d37545f298d8b6dd01955ff2000200010000"
OIL_URL = "https://globus-online.kg/ru-kg/good/faec27b3ccfd4f96afd4bcd0d9acda03000200010001"

MILK_HTML = """
<html><body><main>
<div>Это демо-каталог. Укажите адрес доставки</div>
<h1>Молоко Хорошее дело ультрапаст 2,5% 1л</h1>
<div>1 шт.</div><div>121,49 сом вместо обычной цены 147 сом</div><button>В корзину</button><hr>
</main></body></html>
"""
OIL_HTML = """
<html><body><main>
<div>Это демо-каталог. Укажите адрес доставки</div>
<h1>Масло подсолнечное Олейна 1л</h1>
<div>1 шт.</div><div>193 сом</div><button>В корзину</button><hr>
</main></body></html>
"""


@dataclass
class FixtureTransport:
    pages: dict[str, str]

    def get(self, url: str, *, timeout_seconds: float) -> HttpTextResponse:
        del timeout_seconds
        return HttpTextResponse(200, url, self.pages[url])


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        value = self.now
        self.now += timedelta(seconds=1)
        return value


milk = Item("milk", "Milk")
oil = Item("oil", "Sunflower oil")
milk_sku = SKU("globus-milk-1l", milk, "Хорошее дело 2.5% 1L", Quantity(1, "l"))
oil_sku = SKU("globus-oil-1l", oil, "Олейна 1L", Quantity(1, "l"))
milk_listing = GlobusOnlineListing(MILK_URL)
oil_listing = GlobusOnlineListing(OIL_URL)

catalog = CatalogSnapshot(
    (milk_sku, oil_sku),
    tuple(
        CatalogBinding(
            ExternalListingKey(
                "globus-online-demo", "globus-online-demo", listing.external_product_id
            ),
            sku.id,
            "verified fixture binding",
        )
        for listing, sku in ((milk_listing, milk_sku), (oil_listing, oil_sku))
    ),
)
provider = GlobusOnlineDemoProvider(
    (milk_listing, oil_listing),
    transport=FixtureTransport({MILK_URL: MILK_HTML, OIL_URL: OIL_HTML}),
    clock=Clock(),
)
planner = PlanApplicationService(
    catalog,
    (provider,),
    clock=lambda: datetime(2026, 9, 2, 12, 0, 10, tzinfo=timezone.utc),
)
request = ApplicationPlanRequest(
    demands=(
        RequestedItem("milk", Quantity(1500, "ml")),
        RequestedItem("oil", Quantity(500, "ml")),
    ),
    budget=Money(5000, "KGS"),
)

with TemporaryDirectory() as directory:
    repository = FilePlanRepository(directory)
    lifecycle = PlanLifecycleService(
        planner,
        repository,
        clock=lambda: datetime(2026, 9, 2, 12, 0, 11, tzinfo=timezone.utc),
        id_factory=lambda: PlanId("m7-example-plan"),
    )
    created = lifecycle.create(request)
    restored = lifecycle.get(created.plan_id)
    assert restored == created

    print("plan id:", created.plan_id)
    print("created at:", created.created_at.isoformat())
    print("status:", created.result.to_mapping()["status"])
    print("total:", created.result.to_mapping()["total_cost"])
    print("market batches:", len(created.market_evidence.to_mapping()["batches"]))
    print("history count:", len(lifecycle.list_recent()))
