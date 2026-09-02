from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .money import DecimalLike, as_decimal


_UNIT_TABLE: dict[str, tuple[str, Decimal, str]] = {
    "g": ("mass", Decimal("1"), "g"),
    "kg": ("mass", Decimal("1000"), "g"),
    "ml": ("volume", Decimal("1"), "ml"),
    "l": ("volume", Decimal("1000"), "ml"),
    "piece": ("count", Decimal("1"), "piece"),
    "pieces": ("count", Decimal("1"), "piece"),
    "pcs": ("count", Decimal("1"), "piece"),
}


@dataclass(frozen=True, slots=True)
class Quantity:
    amount: Decimal
    unit: str

    def __init__(self, amount: DecimalLike, unit: str) -> None:
        normalized_unit = unit.strip().lower()
        if normalized_unit not in _UNIT_TABLE:
            raise ValueError(f"unsupported unit: {unit!r}")
        normalized_amount = as_decimal(amount)
        if normalized_amount < 0:
            raise ValueError("quantity must not be negative")
        object.__setattr__(self, "amount", normalized_amount)
        object.__setattr__(self, "unit", normalized_unit)

    @property
    def dimension(self) -> str:
        return _UNIT_TABLE[self.unit][0]

    @property
    def base_unit(self) -> str:
        return _UNIT_TABLE[self.unit][2]

    @property
    def base_amount(self) -> Decimal:
        return self.amount * _UNIT_TABLE[self.unit][1]

    def compatible_with(self, other: Quantity) -> bool:
        return self.dimension == other.dimension

    def to(self, target_unit: str) -> Quantity:
        normalized_target = target_unit.strip().lower()
        if normalized_target not in _UNIT_TABLE:
            raise ValueError(f"unsupported unit: {target_unit!r}")
        target_dimension, target_factor, _ = _UNIT_TABLE[normalized_target]
        if target_dimension != self.dimension:
            raise ValueError(
                f"incompatible quantity units: {self.unit} -> {normalized_target}"
            )
        return Quantity(self.base_amount / target_factor, normalized_target)

    def as_base(self) -> Quantity:
        return Quantity(self.base_amount, self.base_unit)
