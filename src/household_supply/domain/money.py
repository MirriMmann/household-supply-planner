from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TypeAlias


DecimalLike: TypeAlias = Decimal | int | str


class CurrencyMismatchError(ValueError):
    """Raised when arithmetic mixes different currencies."""


def as_decimal(value: DecimalLike) -> Decimal:
    if isinstance(value, bool):
        raise TypeError("boolean is not a valid decimal value")
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc


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
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        if not isinstance(other, Money):
            return NotImplemented
        self._check_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, multiplier: int) -> Money:
        if not isinstance(multiplier, int) or isinstance(multiplier, bool):
            return NotImplemented
        return Money(self.amount * multiplier, self.currency)

    __rmul__ = __mul__
