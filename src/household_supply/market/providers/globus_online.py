from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html.parser import HTMLParser
from fractions import Fraction
from math import isfinite
import re
from typing import Callable, Protocol, runtime_checkable
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from household_supply.domain import MarketAcquisitionBatch, MarketObservation, Money

_PROVIDER_ID = "globus-online-demo"
_DEFAULT_SELLER_ID = "globus-online-demo"
_DEMO_MARKER = "это демо-каталог"
_NO_ADDRESS_MARKER = "укажите адрес доставки"
_CART_MARKER = "корзина"
_UNAVAILABLE_MARKERS = ("раскупили", "разобрали", "нет в наличии")
_ADD_TO_CART_MARKER = "в корзину"
_PRICE_RE = re.compile(
    r"(?P<amount>\d(?:[\d\s\u00a0\u202f]*\d)?(?:[,.]\d+)?)\s*сом(?P<perkg>\s*/\s*кг)?",
    re.IGNORECASE,
)
_DISCOUNT_CURRENT_PRICE_RE = re.compile(
    r"(?P<amount>\d(?:[\d\s\u00a0\u202f]*\d)?(?:[,.]\d+)?)"
    r"\s*сом\s+вместо\s+обычной\s+цены",
    re.IGNORECASE,
)
_DISCOUNT_PERCENT_RE = re.compile(
    r"(?<![\d,.])(?P<percent>\d{1,2}(?:[,.]\d+)?)\s*%",
    re.IGNORECASE,
)
_GOOD_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,}$")


class GlobusOnlineError(RuntimeError):
    """Base error for the bounded Globus Online adapter."""


class GlobusOnlineFetchError(GlobusOnlineError):
    """The configured page could not be fetched safely."""


class GlobusOnlineParseError(GlobusOnlineError):
    """The fetched page does not satisfy the adapter's expected contract."""


class GlobusOnlineUnsupportedListingError(GlobusOnlineError):
    """The listing uses semantics not yet representable by the procurement core."""


@dataclass(frozen=True, slots=True)
class HttpTextResponse:
    status: int
    final_url: str
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, int) or isinstance(self.status, bool):
            raise TypeError("HTTP status must be int")
        if not 100 <= self.status <= 599:
            raise ValueError("HTTP status must be between 100 and 599")
        final_url = self.final_url.strip()
        if not final_url:
            raise ValueError("HTTP final_url must not be empty")
        if not isinstance(self.text, str):
            raise TypeError("HTTP text body must be str")
        object.__setattr__(self, "final_url", final_url)


@runtime_checkable
class HttpTextTransport(Protocol):
    def get(self, url: str, *, timeout_seconds: float) -> HttpTextResponse: ...




class _SafeGlobusRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self, req, fp, code, msg, headers, newurl  # type: ignore[no-untyped-def]
    ):
        try:
            _validate_globus_good_url(newurl)
        except ValueError as exc:
            raise GlobusOnlineFetchError(
                "Globus redirect leaves the allowed product-page boundary"
            ) from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass(frozen=True, slots=True)
class UrllibHttpTextTransport:
    user_agent: str = "household-supply-planner/0.5 (+bounded Globus demo acquisition)"
    max_response_bytes: int = 2_000_000

    def __post_init__(self) -> None:
        if (
            not isinstance(self.max_response_bytes, int)
            or isinstance(self.max_response_bytes, bool)
        ):
            raise TypeError("max_response_bytes must be int")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        user_agent = self.user_agent.strip()
        if not user_agent:
            raise ValueError("user_agent must not be empty")
        object.__setattr__(self, "user_agent", user_agent)

    def get(self, url: str, *, timeout_seconds: float) -> HttpTextResponse:
        try:
            _validate_globus_good_url(url)
        except ValueError as exc:
            raise GlobusOnlineFetchError("invalid Globus request URL") from exc
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not isfinite(float(timeout_seconds))
        ):
            raise TypeError("timeout_seconds must be a finite real number")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru-KG,ru;q=0.9",
            },
        )
        try:
            opener = build_opener(_SafeGlobusRedirectHandler())
            with opener.open(request, timeout=timeout_seconds) as response:
                status = int(getattr(response, "status", response.getcode()))
                final_url = response.geturl()
                content_type = response.headers.get_content_type()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    raise GlobusOnlineFetchError(
                        f"unexpected Globus content type: {content_type}"
                    )
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise GlobusOnlineFetchError("Globus response exceeds configured size limit")
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    text = raw.decode(charset)
                except (LookupError, UnicodeDecodeError) as exc:
                    raise GlobusOnlineFetchError("cannot decode Globus HTML response") from exc
        except GlobusOnlineError:
            raise
        except Exception as exc:  # urllib exposes several transport-specific exception classes.
            raise GlobusOnlineFetchError(f"failed to fetch Globus listing: {url}") from exc
        return HttpTextResponse(status=status, final_url=final_url, text=text)


@dataclass(frozen=True, slots=True)
class GlobusOnlineListing:
    """One explicitly configured packaged product page in the public demo catalog.

    M5 deliberately does not crawl or infer product identity from names. The good ID
    is taken from the canonical product URL and is expected to be bound explicitly in
    the M4 CatalogSnapshot.
    """

    url: str

    def __post_init__(self) -> None:
        canonical = self.url.strip()
        _validate_globus_good_url(canonical)
        object.__setattr__(self, "url", canonical)

    @property
    def seller_id(self) -> str:
        return _DEFAULT_SELLER_ID

    @property
    def external_product_id(self) -> str:
        return _external_product_id(self.url)


class _ProductPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._h1_depth = 0
        self._ignored_depth = 0
        self._seen_h1 = False
        self._product_surface_closed = False
        self._title_parts: list[str] = []
        self._after_h1_parts: list[str] = []
        self._visible_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "template", "noscript"}:
            self._ignored_depth += 1
            return
        if lowered == "h1":
            if not self._seen_h1:
                self._h1_depth = 1
                self._seen_h1 = True
            elif not self._h1_depth:
                self._product_surface_closed = True
            return
        if self._seen_h1 and not self._h1_depth and lowered in {
            "hr",
            "h2",
            "h3",
            "aside",
            "footer",
        }:
            self._product_surface_closed = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "template", "noscript"}:
            if self._ignored_depth:
                self._ignored_depth -= 1
            return
        if lowered == "h1" and self._h1_depth:
            self._h1_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        self._visible_parts.append(data)
        if self._h1_depth:
            self._title_parts.append(data)
        elif self._seen_h1 and not self._product_surface_closed:
            self._after_h1_parts.append(data)

    @property
    def title(self) -> str:
        return _normalize_text(" ".join(self._title_parts))

    @property
    def after_h1_text(self) -> str:
        return _normalize_text(" ".join(self._after_h1_parts))

    @property
    def visible_text(self) -> str:
        return _normalize_text(" ".join(self._visible_parts))


@dataclass(frozen=True, slots=True)
class GlobusOnlineParsedProduct:
    name: str
    price: Money | None
    available: bool

    def __post_init__(self) -> None:
        name = self.name.strip()
        if not name:
            raise ValueError("parsed Globus product name must not be empty")
        if not isinstance(self.available, bool):
            raise TypeError("parsed Globus availability must be bool")
        if self.price is None:
            if self.available:
                raise ValueError("available parsed Globus product requires price")
        else:
            if self.price.currency != "KGS":
                raise ValueError("parsed Globus product price must use KGS")
            if self.price.amount < 0:
                raise ValueError("parsed Globus product price must not be negative")
        object.__setattr__(self, "name", name)


def _normalize_text(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").replace("\u202f", " ").split())


def _validate_globus_good_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "globus-online.kg":
        raise ValueError("Globus listing URL must use https://globus-online.kg")
    if parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
        raise ValueError("Globus listing URL must not contain credentials or a custom port")
    if parsed.query or parsed.fragment:
        raise ValueError("Globus listing URL must not contain query or fragment")
    _external_product_id(url)


def _external_product_id(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    parts = path.split("/")
    if (
        len(parts) != 4
        or parts[0] != ""
        or parts[2] != "good"
        or parts[1] not in {"ru-kg", "ky-kg"}
    ):
        raise ValueError("Globus listing URL must point exactly to /<locale>/good/<product-id>")
    product_id = parts[3]
    if not _GOOD_ID_RE.fullmatch(product_id):
        raise ValueError("Globus product id has an unsupported format")
    return product_id


def _parse_decimal_amount(raw: str) -> Decimal:
    normalized = raw.replace("\xa0", "").replace("\u202f", "").replace(" ", "").replace(",", ".")
    try:
        amount = Decimal(normalized)
    except InvalidOperation as exc:
        raise GlobusOnlineParseError(f"invalid Globus price: {raw!r}") from exc
    if not amount.is_finite() or amount < 0:
        raise GlobusOnlineParseError("Globus price must be a finite non-negative number")
    return amount


def parse_globus_online_demo_product(html: str) -> GlobusOnlineParsedProduct:
    """Parse one packaged-product page from the public Globus demo catalog.

    The parser intentionally rejects per-kilogram listings because M1-M3 currently
    model integer package counts rather than arbitrary weighed quantities.
    """

    parser = _ProductPageParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:
        raise GlobusOnlineParseError("invalid Globus HTML") from exc

    name = parser.title
    if not name:
        raise GlobusOnlineParseError("Globus product page does not contain a product h1")
    visible = parser.after_h1_text
    lowered = visible.casefold()
    page_lowered = parser.visible_text.casefold()
    explicit_demo_marker = _DEMO_MARKER in page_lowered
    addressless_demo_evidence = (
        _NO_ADDRESS_MARKER in page_lowered and _CART_MARKER in page_lowered
    )
    if not (explicit_demo_marker or addressless_demo_evidence):
        raise GlobusOnlineParseError(
            "Globus page exposes no explicit public-demo/addressless catalog evidence"
        )

    available = not any(marker in lowered for marker in _UNAVAILABLE_MARKERS)
    price_matches = list(_PRICE_RE.finditer(visible))
    if any(match.group("perkg") for match in price_matches):
        raise GlobusOnlineUnsupportedListingError(
            "per-kilogram Globus listings are not supported by the package-count planner"
        )

    price = None
    discounted = _DISCOUNT_CURRENT_PRICE_RE.search(visible)
    if discounted is not None:
        price = Money(_parse_decimal_amount(discounted.group("amount")), "KGS")
    elif price_matches:
        distinct_amounts = tuple(
            dict.fromkeys(_parse_decimal_amount(match.group("amount")) for match in price_matches)
        )
        if len(distinct_amounts) == 1:
            price = Money(distinct_amounts[0], "KGS")
        elif len(distinct_amounts) == 2:
            discount_percent_matches = tuple(
                dict.fromkeys(
                    _parse_decimal_amount(match.group("percent"))
                    for match in _DISCOUNT_PERCENT_RE.finditer(visible)
                )
            )
            current_amount, regular_amount = sorted(distinct_amounts)
            if len(discount_percent_matches) == 1 and regular_amount > 0:
                observed_percent = discount_percent_matches[0]
                implied_percent = (
                    Fraction(regular_amount - current_amount)
                    * 100
                    / Fraction(regular_amount)
                )
                # Retailer UI rounds the displayed discount percentage to a whole
                # number. Allow one percentage point of display-rounding slack, but
                # compare exact rationals so ambient Decimal precision cannot alter
                # acquisition semantics.
                if abs(implied_percent - Fraction(observed_percent)) <= 1:
                    price = Money(current_amount, "KGS")
        if price is None:
            raise GlobusOnlineParseError(
                "Globus product surface exposes multiple ambiguous KGS prices"
            )
    if available and _ADD_TO_CART_MARKER not in lowered:
        raise GlobusOnlineParseError(
            "Globus page does not expose an add-to-cart action for an available listing"
        )
    if available and price is None:
        raise GlobusOnlineParseError("available Globus product page exposes no KGS price")
    return GlobusOnlineParsedProduct(name=name, price=price, available=available)


def _stable_observation_id(
    *, seller_id: str, external_product_id: str, observed_at: datetime
) -> str:
    canonical = "\x00".join(
        (_PROVIDER_ID, seller_id, external_product_id, observed_at.isoformat())
    ).encode("utf-8")
    return "globus-" + sha256(canonical).hexdigest()[:24]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class GlobusOnlineDemoProvider:
    """Bounded live provider for explicitly configured Globus demo product pages.

    This is intentionally not a crawler. Each configured product URL is fetched once,
    parsed under a narrow public-demo contract, and emitted as attributable M4 market
    evidence. Catalog identity remains an explicit M4 responsibility.
    """

    listings: tuple[GlobusOnlineListing, ...]
    transport: HttpTextTransport = field(default_factory=UrllibHttpTextTransport)
    timeout_seconds: float = 10.0
    clock: Callable[[], datetime] = _utc_now

    def __post_init__(self) -> None:
        listings = tuple(
            sorted(
                self.listings,
                key=lambda listing: (listing.seller_id, listing.external_product_id),
            )
        )
        if not listings:
            raise ValueError("Globus provider requires at least one listing")
        if (
            not isinstance(self.timeout_seconds, (int, float))
            or isinstance(self.timeout_seconds, bool)
            or not isfinite(float(self.timeout_seconds))
        ):
            raise TypeError("Globus timeout_seconds must be a finite real number")
        if self.timeout_seconds <= 0:
            raise ValueError("Globus timeout_seconds must be positive")
        keys = [(listing.seller_id, listing.external_product_id) for listing in listings]
        if len(keys) != len(set(keys)):
            raise ValueError("Globus provider contains duplicate listing identity")
        object.__setattr__(self, "listings", listings)

    @property
    def provider_id(self) -> str:
        return _PROVIDER_ID

    def acquire(self) -> MarketAcquisitionBatch:
        observations: list[MarketObservation] = []
        for listing in self.listings:
            response = self.transport.get(
                listing.url, timeout_seconds=self.timeout_seconds
            )
            if response.status != 200:
                raise GlobusOnlineFetchError(
                    f"Globus listing returned HTTP {response.status}: {listing.url}"
                )
            try:
                _validate_globus_good_url(response.final_url)
            except ValueError as exc:
                raise GlobusOnlineFetchError(
                    "Globus response redirected outside the allowed product-page boundary"
                ) from exc
            if _external_product_id(response.final_url) != listing.external_product_id:
                raise GlobusOnlineFetchError(
                    "Globus response redirected to a different product identity"
                )

            parsed = parse_globus_online_demo_product(response.text)
            observed_at = self.clock()
            if observed_at.tzinfo is None or observed_at.utcoffset() is None:
                raise ValueError("Globus provider clock must return timezone-aware datetime")
            observations.append(
                MarketObservation(
                    id=_stable_observation_id(
                        seller_id=listing.seller_id,
                        external_product_id=listing.external_product_id,
                        observed_at=observed_at,
                    ),
                    provider_id=self.provider_id,
                    seller_id=listing.seller_id,
                    external_product_id=listing.external_product_id,
                    price=parsed.price,
                    observed_at=observed_at,
                    available=parsed.available,
                    name=parsed.name,
                    source_ref=response.final_url,
                )
            )

        acquired_at = self.clock()
        if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
            raise ValueError("Globus provider clock must return timezone-aware datetime")
        if observations and acquired_at < max(obs.observed_at for obs in observations):
            raise ValueError("Globus provider clock moved backwards during acquisition")
        return MarketAcquisitionBatch(
            provider_id=self.provider_id,
            acquired_at=acquired_at,
            observations=tuple(observations),
        )
