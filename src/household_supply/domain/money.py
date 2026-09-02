from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TypeAlias

from ._decimal import (
    add_decimals_exact,
    multiply_decimal_by_int_exact,
    subtract_decimals_exact,
)


DecimalLike: TypeAlias = Decimal | int | str


class CurrencyMismatchError(ValueError):
    """Raised when arithmetic mixes different currencies."""


def as_decimal(value: DecimalLike) -> Decimal:
    if isinstance(value, bool):
        raise TypeError("boolean is not a valid decimal value")
    if isinstance(value, float):
        raise TypeError("float is not accepted; use Decimal, int, or str")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"decimal value must be finite: {value!r}")
    return result


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __init__(self, amount: DecimalLike, currency: str) -> None:
        normalized_currency = currency.strip().upper()
        if not normalized_currency:
            raise ValueError("currency must not be empty")
        object.__setattr__(self, "amount", as_decimal(amount))
        object.__setattr__(self, "currency", normalized_currency)

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(Decimal("0"), currency)

    def _check_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"currency mismatch: {self.currency} != {other.currency}"
            )

    def __add__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._check_currency(other)
        return Money(add_decimals_exact(self.amount, other.amount), self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._check_currency(other)
        return Money(subtract_decimals_exact(self.amount, other.amount), self.currency)

    def __mul__(self, multiplier: int) -> Money:
        if not isinstance(multiplier, int) or isinstance(multiplier, bool):
            return NotImplemented
        return Money(multiply_decimal_by_int_exact(self.amount, multiplier), self.currency)

    __rmul__ = __mul__
