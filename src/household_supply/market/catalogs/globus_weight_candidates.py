"""Evidence-only Globus weight listings (NOT fixed-size purchasable SKUs).

The current per-kg/per-100g retailer model cannot be projected into integer
package counts. Keep these separately until a validated variable-weight
planning and purchasing contract exists. URLs and product names are manually
checked against the public addressless Globus Online catalog, 2026-10-11.
Prices and in-stock status MUST be reacquired, never stored here.
"""

from __future__ import annotations

from dataclasses import dataclass

from household_supply.market.providers.globus_online import GlobusOnlineListing


@dataclass(frozen=True, slots=True)
class GlobusWeightCandidate:
    item_id: str
    item_name: str
    product_name: str
    source_url: str
    listed_price_basis_amount: str
    listed_price_basis_unit: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (
            self.item_id, self.item_name, self.product_name,
            self.listed_price_basis_amount, self.listed_price_basis_unit,
        )):
            raise ValueError("weight candidate requires explicit source-backed identity")
        if self.listed_price_basis_unit not in {"kg", "g"}:
            raise ValueError("unsupported weight listing basis")
        if self.listed_price_basis_amount not in {"1", "100"}:
            raise ValueError("weight listing basis not explicitly reviewed")
        if self.listed_price_basis_unit == "kg" and self.listed_price_basis_amount != "1":
            raise ValueError("unexpected per-kg price basis")
        if self.listed_price_basis_unit == "g" and self.listed_price_basis_amount != "100":
            raise ValueError("unexpected per-100g price basis")
        GlobusOnlineListing(self.source_url)


# Product records below are candidates for a future weighted-purchase model.
# Even when a listing displays "per 1 kg", that is a PRICE REFERENCE UNIT,
# not evidence that a one-kilogram prepacked SKU exists.
WEIGHT_CANDIDATES: tuple[GlobusWeightCandidate, ...] = (
    GlobusWeightCandidate(
        "onion", "Лук репчатый", "Лук репчатый фас вес",
        "https://globus-online.kg/ru-kg/good/9ca7b9b184f14fe3af97d319a64dd364000200010001",
        "1", "kg",
    ),
    GlobusWeightCandidate(
        "potato", "Картофель", "Картофель фасованный вес",
        "https://globus-online.kg/ru-kg/good/268f8c2e69184dc5834205503a1c53f1000100010000",
        "1", "kg",
    ),
    GlobusWeightCandidate(
        "beef", "Говядина", "Мясо говядина стейк",
        "https://globus-online.kg/ru-kg/good/623c4eaaea2f45eeb256209980309780000300010001",
        "1", "kg",
    ),
    GlobusWeightCandidate(
        "beef_mince", "Фарш говяжий", "Фарш говяжий диетический",
        "https://globus-online.kg/ru-kg/good/f700f604344843ddab854e4208ca841f000100010000",
        "1", "kg",
    ),
    GlobusWeightCandidate(
        "carrot", "Морковь", "Морковь мытая вес",
        "https://globus-online.kg/ru-kg/good/f4c437e4e6c74f048cc5f77a3ea64cbe000300010001",
        "1", "kg",
    ),
    GlobusWeightCandidate(
        "tomato", "Помидоры", "Помидоры вес",
        "https://globus-online.kg/ru-kg/good/f1eb82e519d94ff981bd71140f690d52000200010000",
        "1", "kg",
    ),
    GlobusWeightCandidate(
        "bell_pepper", "Перец болгарский", "Перец Болгарский зеленый вес",
        "https://globus-online.kg/ru-kg/good/8e23e7215445494f833ad77c002f3dc1000300010001",
        "1", "kg",
    ),
    GlobusWeightCandidate(
        "garlic", "Чеснок", "Чеснок вес",
        "https://globus-online.kg/ru-kg/good/6828d37edf754f6a8a4ac7740428f4af000200010000",
        "1", "kg",
    ),
    GlobusWeightCandidate(
        "chicken_fillet", "Филе куриное", "Филе куриное охл вес GL",
        "https://globus-online.kg/ru-kg/good/96003deeeb1546019404766f87bf9f4d000100010000",
        "1", "kg",
    ),
    GlobusWeightCandidate(
        "black_pepper", "Перец чёрный", "Перец черный 2сорт вес",
        "https://globus-online.kg/ru-kg/good/3853044f04ec4d3d9733d68d5167aac0000200010000",
        "100", "g",
    ),
)


def serialize_weight_candidates() -> tuple[dict[str, str | bool], ...]:
    """A non-purchasable source index: explicitly NOT a substitute for /catalog."""
    return tuple(
        {
            "item_id": candidate.item_id,
            "item_name": candidate.item_name,
            "product_name": candidate.product_name,
            "source_url": candidate.source_url,
            "listed_price_basis_amount": candidate.listed_price_basis_amount,
            "listed_price_basis_unit": candidate.listed_price_basis_unit,
            "plannable": False,
            "blocked_reason": "variable_weight_not_supported",
        }
        for candidate in WEIGHT_CANDIDATES
    )
