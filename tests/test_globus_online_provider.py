from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from household_supply import (
    CatalogBinding,
    CatalogSnapshot,
    Demand,
    ExternalListingKey,
    InventorySnapshot,
    Item,
    MarketAcquisitionBatch,
    MarketCompilationPolicy,
    MarketObservation,
    MarketObservationDispositionStatus,
    Money,
    PlanningPolicy,
    PlanningProblem,
    Quantity,
    SKU,
    acquire_market,
    build_plan,
    compile_market_snapshot,
)
from household_supply.market.providers import (
    GlobusOnlineDemoProvider,
    GlobusOnlineFetchError,
    GlobusOnlineListing,
    GlobusOnlineParseError,
    GlobusOnlineUnsupportedListingError,
    HttpTextResponse,
    parse_globus_online_demo_product,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)
MILK_URL = "https://globus-online.kg/ru-kg/good/23df8084d37545f298d8b6dd01955ff2000200010000"
OIL_URL = "https://globus-online.kg/ru-kg/good/faec27b3ccfd4f96afd4bcd0d9acda03000200010001"
SOLD_OUT_URL = "https://globus-online.kg/ru-kg/good/f0a3f9beabc04a40be112dd09671f7a8000100010000"
WEIGHT_URL = "https://globus-online.kg/ru-kg/good/96003deeeb1546019404766f87bf9f4d000100010000"


def _page(
    name: str,
    body: str,
    *,
    demo: bool = True,
    marker_before_h1: bool = False,
    addressless_header: bool = False,
) -> str:
    marker = (
        "<div>Это демо-каталог. Укажите адрес, чтобы посмотреть настоящий</div>"
        if demo
        else ""
    )
    header = "Каталог"
    if addressless_header:
        header += " Укажите адрес доставки Корзина"
    before = marker if marker_before_h1 else ""
    after = "" if marker_before_h1 else marker
    return (
        f"<!doctype html><html><body><header>{header}</header>"
        f"<main>{before}<h1>{name}</h1>{body}{after}</main></body></html>"
    )


MILK_HTML = _page(
    "Молоко Хорошее дело ультрапаст 2,5% 1л",
    "<div>1 шт.</div><div>13%</div><div>147\u202fсом</div>"
    "<div>121,49\u202fсом вместо обычной цены 147\u202fсом</div>"
    "<button>В корзину</button>",
)
OIL_HTML = _page(
    "Масло подсолнечное Олейна 1л",
    "<div>1 шт.</div><div>193\u202fсом</div><button>В корзину</button>",
)
SOLD_OUT_HTML = _page(
    "Готовый обед Филе куриное под шапкой вес СП GL",
    "<div>Раскупили</div>",
)
WEIGHT_HTML = _page(
    "Филе куриное охл вес GL",
    "<div>554\u202fсом/кг</div><button>В корзину</button>",
)


@dataclass
class FakeTransport:
    pages: dict[str, HttpTextResponse]
    calls: list[tuple[str, float]]

    def get(self, url: str, *, timeout_seconds: float) -> HttpTextResponse:
        self.calls.append((url, timeout_seconds))
        return self.pages[url]


class StepClock:
    def __init__(self, start: datetime = NOW) -> None:
        self.value = start

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


def test_parser_reads_current_discounted_piece_price_without_using_original_price() -> None:
    parsed = parse_globus_online_demo_product(MILK_HTML)

    assert parsed.name == "Молоко Хорошее дело ультрапаст 2,5% 1л"
    assert parsed.price == Money(Decimal("121.49"), "KGS")
    assert parsed.available is True


def test_parser_allows_unavailable_listing_without_inventing_a_price() -> None:
    parsed = parse_globus_online_demo_product(SOLD_OUT_HTML)

    assert parsed.available is False
    assert parsed.price is None


def test_parser_rejects_weight_goods_until_planner_supports_variable_quantities() -> None:
    with pytest.raises(GlobusOnlineUnsupportedListingError, match="per-kilogram"):
        parse_globus_online_demo_product(WEIGHT_HTML)


def test_parser_requires_explicit_demo_scope_and_product_heading() -> None:
    with pytest.raises(GlobusOnlineParseError, match="public-demo/addressless"):
        parse_globus_online_demo_product(_page("Milk", "<div>100 сом</div>", demo=False))

    with pytest.raises(GlobusOnlineParseError, match="product h1"):
        parse_globus_online_demo_product("<html><body>100 сом Это демо-каталог</body></html>")


def test_parser_demo_scope_does_not_depend_on_dom_position_after_h1() -> None:
    parsed = parse_globus_online_demo_product(
        _page(
            "Milk",
            "<div>125 сом</div><button>В корзину</button>",
            marker_before_h1=True,
        )
    )

    assert parsed.available is True
    assert parsed.price == Money(125, "KGS")


def test_parser_accepts_official_addressless_scope_evidence_without_footer_marker() -> None:
    parsed = parse_globus_online_demo_product(
        _page(
            "Milk",
            "<div>125 сом</div><button>В корзину</button>",
            demo=False,
            addressless_header=True,
        )
    )

    assert parsed.available is True
    assert parsed.price == Money(125, "KGS")


def test_parser_requires_price_for_available_listing() -> None:
    with pytest.raises(GlobusOnlineParseError, match="exposes no KGS price"):
        parse_globus_online_demo_product(_page("Milk", "<button>В корзину</button>"))



def test_parser_ignores_script_text_that_looks_like_stock_or_price() -> None:
    html = _page(
        "Milk",
        '<script>const translations = "Раскупили 1 сом";</script><div>125 сом</div><button>В корзину</button>',
    )
    parsed = parse_globus_online_demo_product(html)

    assert parsed.available is True
    assert parsed.price == Money(125, "KGS")


def test_parser_handles_thousands_separator_and_requires_buy_action_for_available_page() -> None:
    parsed = parse_globus_online_demo_product(
        _page("Large pack", "<div>1\u202f018 сом</div><button>В корзину</button>")
    )
    assert parsed.price == Money(1018, "KGS")

    with pytest.raises(GlobusOnlineParseError, match="add-to-cart"):
        parse_globus_online_demo_product(_page("Milk", "<div>125 сом</div>"))


def test_parser_rejects_ambiguous_multiple_product_prices_without_current_price_context() -> None:
    html = _page(
        "Milk",
        "<div>147 сом</div><div>121 сом</div><button>В корзину</button>",
    )
    with pytest.raises(GlobusOnlineParseError, match="multiple ambiguous"):
        parse_globus_online_demo_product(html)


def test_parser_resolves_discount_pair_when_raw_dom_omits_discount_phrase() -> None:
    html = _page(
        "Milk",
        "<div>17%</div>"
        "<div>121,49 сом</div><div>147 сом</div>"
        "<div>121,49 сом</div><div>147 сом</div>"
        "<button>В корзину</button>",
    )

    parsed = parse_globus_online_demo_product(html)

    assert parsed.price == Money(Decimal("121.49"), "KGS")
    assert parsed.available is True


def test_parser_rejects_two_prices_when_discount_percent_is_inconsistent() -> None:
    html = _page(
        "Milk",
        "<div>5%</div><div>121,49 сом</div><div>147 сом</div>"
        "<button>В корзину</button>",
    )

    with pytest.raises(GlobusOnlineParseError, match="multiple ambiguous"):
        parse_globus_online_demo_product(html)


def test_discount_pair_resolution_is_independent_of_decimal_context_precision() -> None:
    from decimal import localcontext

    html = _page(
        "Milk",
        "<div>17%</div><div>121,49 сом</div><div>147 сом</div>"
        "<button>В корзину</button>",
    )
    results = []
    for precision in (6, 12, 28, 50):
        with localcontext() as context:
            context.prec = precision
            results.append(parse_globus_online_demo_product(html).price)

    assert results == [Money(Decimal("121.49"), "KGS")] * 4


def test_parser_does_not_leak_market_facts_from_content_below_product_surface() -> None:
    available_html = (
        "<html><body><header>Укажите адрес доставки Корзина</header>"
        "<h1>Milk</h1><div>125 сом</div><button>В корзину</button>"
        "<hr><section><h2>Recommendations</h2>"
        "<div>Other item Раскупили 99 сом/кг</div></section></body></html>"
    )
    parsed = parse_globus_online_demo_product(available_html)
    assert parsed.available is True
    assert parsed.price == Money(125, "KGS")

    missing_target_price_html = (
        "<html><body><header>Укажите адрес доставки Корзина</header>"
        "<h1>Milk</h1><div>1 шт.</div><hr>"
        "<section><h2>Recommendations</h2>"
        "<div>Other item 99 сом</div><button>В корзину</button></section>"
        "</body></html>"
    )
    with pytest.raises(GlobusOnlineParseError):
        parse_globus_online_demo_product(missing_target_price_html)

def test_listing_accepts_only_canonical_official_good_urls() -> None:
    assert GlobusOnlineListing(MILK_URL).external_product_id.startswith("23df")

    for bad in (
        "http://globus-online.kg/ru-kg/good/abcdefgh",
        "https://example.com/ru-kg/good/abcdefgh",
        "https://globus-online.kg/ru-kg/catalog/grocery",
        "https://globus-online.kg/not-a-locale/good/abcdefgh",
        "https://globus-online.kg/extra/ru-kg/good/abcdefgh",
        "https://globus-online.kg/ru-kg/good/abcdefgh?x=1",
        "https://user@globus-online.kg/ru-kg/good/abcdefgh",
    ):
        with pytest.raises(ValueError):
            GlobusOnlineListing(bad)


def test_transport_and_provider_reject_non_finite_or_boolean_bounds() -> None:
    from household_supply.market.providers import UrllibHttpTextTransport

    with pytest.raises(TypeError, match="max_response_bytes"):
        UrllibHttpTextTransport(max_response_bytes=True)
    with pytest.raises(ValueError, match="user_agent"):
        UrllibHttpTextTransport(user_agent="   ")

    transport = UrllibHttpTextTransport()
    for timeout in (float("nan"), float("inf"), True):
        with pytest.raises(TypeError, match="timeout_seconds"):
            transport.get(MILK_URL, timeout_seconds=timeout)
        with pytest.raises(TypeError, match="timeout_seconds"):
            GlobusOnlineDemoProvider(
                (GlobusOnlineListing(MILK_URL),), timeout_seconds=timeout
            )


def test_provider_emits_attributable_observations_from_explicit_pages() -> None:
    transport = FakeTransport(
        {
            MILK_URL: HttpTextResponse(200, MILK_URL, MILK_HTML),
            OIL_URL: HttpTextResponse(200, OIL_URL, OIL_HTML),
        },
        [],
    )
    provider = GlobusOnlineDemoProvider(
        # Reverse input order deliberately: provider canonicalizes configured listing identity.
        listings=(GlobusOnlineListing(OIL_URL), GlobusOnlineListing(MILK_URL)),
        transport=transport,
        timeout_seconds=3,
        clock=StepClock(),
    )

    batch = acquire_market(provider)

    assert batch.provider_id == "globus-online-demo"
    assert [obs.external_product_id for obs in batch.observations] == sorted(
        obs.external_product_id for obs in batch.observations
    )
    assert {obs.price for obs in batch.observations} == {
        Money("121.49", "KGS"),
        Money(193, "KGS"),
    }
    assert all(obs.seller_id == "globus-online-demo" for obs in batch.observations)
    assert all(obs.source_ref.startswith("https://globus-online.kg/") for obs in batch.observations)
    assert transport.calls == [(MILK_URL, 3), (OIL_URL, 3)]


def test_provider_rejects_redirect_to_different_product_or_non_200_status() -> None:
    different = OIL_URL
    transport = FakeTransport(
        {MILK_URL: HttpTextResponse(200, different, MILK_HTML)}, []
    )
    provider = GlobusOnlineDemoProvider(
        (GlobusOnlineListing(MILK_URL),), transport=transport, clock=StepClock()
    )
    with pytest.raises(GlobusOnlineFetchError, match="different product identity"):
        provider.acquire()

    transport = FakeTransport({MILK_URL: HttpTextResponse(503, MILK_URL, "oops")}, [])
    provider = GlobusOnlineDemoProvider(
        (GlobusOnlineListing(MILK_URL),), transport=transport, clock=StepClock()
    )
    with pytest.raises(GlobusOnlineFetchError, match="HTTP 503"):
        provider.acquire()


def test_provider_rejects_duplicate_product_identity_and_backwards_clock() -> None:
    with pytest.raises(ValueError, match="duplicate listing identity"):
        GlobusOnlineDemoProvider(
            (GlobusOnlineListing(MILK_URL), GlobusOnlineListing(MILK_URL))
        )

    class BackwardsClock:
        values = iter((NOW, NOW - timedelta(seconds=1)))

        def __call__(self) -> datetime:
            return next(self.values)

    transport = FakeTransport({MILK_URL: HttpTextResponse(200, MILK_URL, MILK_HTML)}, [])
    provider = GlobusOnlineDemoProvider(
        (GlobusOnlineListing(MILK_URL),),
        transport=transport,
        clock=BackwardsClock(),
    )
    with pytest.raises(ValueError, match="moved backwards"):
        provider.acquire()


def test_unpriced_unavailable_observation_blocks_old_offer_without_creating_fake_offer() -> None:
    milk = Item("milk", "Milk")
    sku = SKU("milk-1l", milk, "Milk 1L", Quantity(1, "l"))
    product_id = GlobusOnlineListing(SOLD_OUT_URL).external_product_id
    key = ExternalListingKey("globus-online-demo", "globus-online-demo", product_id)
    catalog = CatalogSnapshot((sku,), (CatalogBinding(key, sku.id, "manual M5 binding"),))
    old = MarketObservation(
        "old",
        "globus-online-demo",
        "globus-online-demo",
        product_id,
        Money(100, "KGS"),
        NOW - timedelta(hours=1),
        available=True,
        source_ref=SOLD_OUT_URL,
    )
    latest = MarketObservation(
        "latest",
        "globus-online-demo",
        "globus-online-demo",
        product_id,
        None,
        NOW,
        available=False,
        source_ref=SOLD_OUT_URL,
    )

    compilation = compile_market_snapshot(
        catalog,
        (
            # Same provider and listing, two successive observations.
            MarketAcquisitionBatch("globus-online-demo", NOW, (old, latest)),
        ),
        captured_at=NOW,
    )

    assert compilation.snapshot.offers == ()
    statuses = {d.observation.id: d.status for d in compilation.dispositions}
    assert statuses == {
        "old": MarketObservationDispositionStatus.SUPERSEDED,
        "latest": MarketObservationDispositionStatus.UNAVAILABLE,
    }


def test_real_provider_fixture_flows_through_m4_into_unchanged_m1_planner() -> None:
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
                ExternalListingKey("globus-online-demo", "globus-online-demo", milk_listing.external_product_id),
                milk_sku.id,
                "verified Globus product URL",
            ),
            CatalogBinding(
                ExternalListingKey("globus-online-demo", "globus-online-demo", oil_listing.external_product_id),
                oil_sku.id,
                "verified Globus product URL",
            ),
        ),
    )
    transport = FakeTransport(
        {
            MILK_URL: HttpTextResponse(200, MILK_URL, MILK_HTML),
            OIL_URL: HttpTextResponse(200, OIL_URL, OIL_HTML),
        },
        [],
    )
    provider = GlobusOnlineDemoProvider(
        (milk_listing, oil_listing), transport=transport, clock=StepClock()
    )
    batch = acquire_market(provider)
    compilation = compile_market_snapshot(
        catalog,
        (batch,),
        captured_at=batch.acquired_at,
        policy=MarketCompilationPolicy(),
    )
    problem = PlanningProblem(
        demands=(
            Demand(milk, Quantity(1500, "ml"), "M5 fixture"),
            Demand(oil, Quantity(500, "ml"), "M5 fixture"),
        ),
        inventory=InventorySnapshot(()),
        market=compilation.snapshot,
        policy=PlanningPolicy(Money(1000, "KGS")),
    )

    plan = build_plan(problem)

    assert plan.status.value == "feasible"
    assert plan.total_cost == Money(Decimal("435.98"), "KGS")
    assert {(p.offer.sku.id, p.packs) for p in plan.purchases} == {
        (milk_sku.id, 2),
        (oil_sku.id, 1),
    }
    assert all(p.offer.provenance is not None for p in plan.purchases)


def test_safe_redirect_handler_rejects_external_target_before_following_it() -> None:
    from household_supply.market.providers.globus_online import _SafeGlobusRedirectHandler

    handler = _SafeGlobusRedirectHandler()
    with pytest.raises(GlobusOnlineFetchError, match="redirect leaves"):
        handler.redirect_request(
            None,
            None,
            302,
            "Found",
            {},
            "https://example.com/ru-kg/good/abcdefgh",
        )


def test_unavailable_observation_may_omit_price_but_available_one_may_not() -> None:
    unavailable = MarketObservation(
        "obs-unavailable",
        "provider",
        "seller",
        "listing",
        None,
        NOW,
        available=False,
    )
    assert unavailable.price is None

    with pytest.raises(ValueError, match="requires price"):
        MarketObservation(
            "obs-available",
            "provider",
            "seller",
            "listing",
            None,
            NOW,
            available=True,
        )


def test_accepted_disposition_cannot_claim_unpriced_observation() -> None:
    milk = Item("milk", "Milk")
    sku = SKU("milk", milk, "Milk", Quantity(1, "l"))
    observation = MarketObservation(
        "obs",
        "provider",
        "seller",
        "listing",
        None,
        NOW,
        available=False,
    )
    from household_supply import (
        CatalogResolution,
        CatalogResolutionMethod,
        CatalogResolutionStatus,
        MarketObservationDisposition,
    )

    resolution = CatalogResolution(
        observation_id=observation.id,
        status=CatalogResolutionStatus.RESOLVED,
        sku=sku,
        method=CatalogResolutionMethod.EXPLICIT_BINDING,
        candidate_sku_ids=(sku.id,),
    )
    with pytest.raises(ValueError, match="requires priced observation"):
        MarketObservationDisposition(
            observation=observation,
            status=MarketObservationDispositionStatus.ACCEPTED,
            resolution=resolution,
        )
