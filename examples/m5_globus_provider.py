from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from household_supply import (
    CatalogBinding,
    CatalogSnapshot,
    Demand,
    ExternalListingKey,
    GlobusOnlineDemoProvider,
    GlobusOnlineListing,
    HttpTextResponse,
    InventorySnapshot,
    Item,
    Money,
    PlanningPolicy,
    PlanningProblem,
    Quantity,
    SKU,
    acquire_market,
    build_plan,
    compile_market_snapshot,
)

MILK_URL = "https://globus-online.kg/ru-kg/good/23df8084d37545f298d8b6dd01955ff2000200010000"
OIL_URL = "https://globus-online.kg/ru-kg/good/faec27b3ccfd4f96afd4bcd0d9acda03000200010001"

MILK_HTML = """
<html><body><main>
<h1>Молоко Хорошее дело ультрапаст 2,5% 1л</h1>
<div>1 шт.</div><div>121,49 сом вместо обычной цены 147 сом</div><button>В корзину</button>
<div>Это демо-каталог. Укажите адрес, чтобы посмотреть настоящий</div>
</main></body></html>
"""
OIL_HTML = """
<html><body><main>
<h1>Масло подсолнечное Олейна 1л</h1>
<div>1 шт.</div><div>193 сом</div><button>В корзину</button>
<div>Это демо-каталог. Укажите адрес, чтобы посмотреть настоящий</div>
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
    (
        CatalogBinding(
            ExternalListingKey(
                "globus-online-demo",
                "globus-online-demo",
                milk_listing.external_product_id,
            ),
            milk_sku.id,
            "verified Globus product URL",
        ),
        CatalogBinding(
            ExternalListingKey(
                "globus-online-demo",
                "globus-online-demo",
                oil_listing.external_product_id,
            ),
            oil_sku.id,
            "verified Globus product URL",
        ),
    ),
)

provider = GlobusOnlineDemoProvider(
    (milk_listing, oil_listing),
    transport=FixtureTransport({MILK_URL: MILK_HTML, OIL_URL: OIL_HTML}),
    clock=Clock(),
)
batch = acquire_market(provider)
compilation = compile_market_snapshot(catalog, (batch,), captured_at=batch.acquired_at)
problem = PlanningProblem(
    demands=(
        Demand(milk, Quantity(1500, "ml"), "M5 demo"),
        Demand(oil, Quantity(500, "ml"), "M5 demo"),
    ),
    inventory=InventorySnapshot(()),
    market=compilation.snapshot,
    policy=PlanningPolicy(Money(1000, "KGS")),
)
plan = build_plan(problem)

print("provider:", provider.provider_id)
print("observations:")
for observation in batch.observations:
    print(
        f"  - {observation.name}: "
        f"{observation.price if observation.price is not None else 'no price'} "
        f"available={observation.available}"
    )
print("accepted offers:", len(compilation.snapshot.offers))
print("plan status:", plan.status.value)
print("total:", plan.total_cost)
print("purchases:")
for purchase in plan.purchases:
    print(f"  - {purchase.offer.sku.name} x {purchase.packs} = {purchase.cost}")
