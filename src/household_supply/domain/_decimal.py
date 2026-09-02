from __future__ import annotations

from decimal import Decimal
from fractions import Fraction


def _coefficient(value: Decimal) -> tuple[int, int]:
    parts = value.as_tuple()
    digits = int("".join(str(digit) for digit in parts.digits) or "0")
    if parts.sign:
        digits = -digits
    return digits, parts.exponent


def decimal_from_coefficient(coefficient: int, exponent: int) -> Decimal:
    sign = 1 if coefficient < 0 else 0
    digits_text = str(abs(coefficient))
    digits = tuple(int(character) for character in digits_text) or (0,)
    return Decimal((sign, digits, exponent))


def shift_decimal_exact(value: Decimal, decimal_places: int) -> Decimal:
    coefficient, exponent = _coefficient(value)
    return decimal_from_coefficient(coefficient, exponent + decimal_places)


def add_decimals_exact(left: Decimal, right: Decimal) -> Decimal:
    """Add two finite Decimals without applying ambient context precision."""

    left_coefficient, left_exponent = _coefficient(left)
    right_coefficient, right_exponent = _coefficient(right)
    exponent = min(left_exponent, right_exponent)
    left_scaled = left_coefficient * (10 ** (left_exponent - exponent))
    right_scaled = right_coefficient * (10 ** (right_exponent - exponent))
    return decimal_from_coefficient(left_scaled + right_scaled, exponent)


def subtract_decimals_exact(left: Decimal, right: Decimal) -> Decimal:
    right_coefficient, right_exponent = _coefficient(right)
    negated = decimal_from_coefficient(-right_coefficient, right_exponent)
    return add_decimals_exact(left, negated)


def multiply_decimal_by_int_exact(value: Decimal, multiplier: int) -> Decimal:
    coefficient, exponent = _coefficient(value)
    return decimal_from_coefficient(coefficient * multiplier, exponent)


def scale_decimal_ratio_up(
    value: Decimal,
    numerator: Decimal,
    denominator: Decimal,
    *,
    decimal_places: int,
) -> Decimal:
    """Scale a non-negative Decimal by an exact Decimal ratio deterministically.

    The exact rational result is rounded upward to ``decimal_places`` digits
    after the decimal point. Integer arithmetic is used throughout, so the
    result does not depend on ``decimal.getcontext()``.
    """

    if value < 0 or numerator < 0:
        raise ValueError("scaled values must not be negative")
    if denominator <= 0:
        raise ValueError("scale denominator must be positive")
    if decimal_places < 0:
        raise ValueError("decimal_places must not be negative")

    exact = Fraction(value) * Fraction(numerator) / Fraction(denominator)
    scale = 10**decimal_places
    scaled_numerator = exact.numerator * scale
    rounded_up = (scaled_numerator + exact.denominator - 1) // exact.denominator
    return decimal_from_coefficient(rounded_up, -decimal_places)


def ceil_decimal_ratio_exact(numerator: Decimal, denominator: Decimal) -> int:
    """Return ceil(numerator / denominator) without Decimal context rounding."""

    if numerator < 0:
        raise ValueError("ratio numerator must not be negative")
    if denominator <= 0:
        raise ValueError("ratio denominator must be positive")
    ratio = Fraction(numerator) / Fraction(denominator)
    return (ratio.numerator + ratio.denominator - 1) // ratio.denominator
