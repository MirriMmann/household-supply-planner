from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .money import DecimalLike, as_decimal


# dimension, decimal power needed to reach the base unit, base unit
_UNIT_TABLE: dict[str, tuple[str, int, str]] = {
    "g": ("mass", 0, "g"),
    "kg": ("mass", 3, "g"),
    "ml": ("volume", 0, "ml"),
    "l": ("volume", 3, "ml"),
    "piece": ("count", 0, "piece"),
    "pieces": ("count", 0, "piece"),
    "pcs": ("count", 0, "piece"),
}


def _shift_decimal_exact(value: Decimal, decimal_places: int) -> Decimal:
    parts = value.as_tuple()
    return Decimal((parts.sign, parts.digits, parts.exponent + decimal_places))


def _add_decimals_exact(left: Decimal, right: Decimal) -> Decimal:
    """Add two finite Decimals without applying the active context precision."""

    left_tuple = left.as_tuple()
    right_tuple = right.as_tuple()
    exponent = min(left_tuple.exponent, right_tuple.exponent)

    def coefficient(value: Decimal, target_exponent: int) -> int:
        parts = value.as_tuple()
        digits = int("".join(str(digit) for digit in parts.digits) or "0")
        if parts.sign:
            digits = -digits
        return digits * (10 ** (parts.exponent - target_exponent))

    total = coefficient(left, exponent) + coefficient(right, exponent)
    sign = 1 if total < 0 else 0
    digits = tuple(int(character) for character in str(abs(total))) or (0,)
    return Decimal((sign, digits, exponent))


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
        return _shift_decimal_exact(self.amount, _UNIT_TABLE[self.unit][1])

    def compatible_with(self, other: Quantity) -> bool:
        return self.dimension == other.dimension

    def __add__(self, other: Quantity) -> Quantity:
        if not isinstance(other, Quantity):
            return NotImplemented
        if not self.compatible_with(other):
            raise ValueError(
                f"incompatible quantity units: {self.unit} + {other.unit}"
            )
        return Quantity(
            _add_decimals_exact(self.base_amount, other.base_amount),
            self.base_unit,
        )

    def to(self, target_unit: str) -> Quantity:
        normalized_target = target_unit.strip().lower()
        if normalized_target not in _UNIT_TABLE:
            raise ValueError(f"unsupported unit: {target_unit!r}")
        target_dimension, target_shift, _ = _UNIT_TABLE[normalized_target]
        if target_dimension != self.dimension:
            raise ValueError(
                f"incompatible quantity units: {self.unit} -> {normalized_target}"
            )
        return Quantity(
            _shift_decimal_exact(self.base_amount, -target_shift),
            normalized_target,
        )

    def as_base(self) -> Quantity:
        return Quantity(self.base_amount, self.base_unit)
