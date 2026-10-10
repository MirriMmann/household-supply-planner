"""A small replaceable persistence boundary for user-declared usual-basket preferences."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import tempfile
from typing import Protocol

from household_supply.domain import Quantity

from .usual_basket import UsualBasket, UsualBasketItem


class UsualBasketRepositoryError(RuntimeError):
    """Saved preferences cannot be read or written safely."""


class UsualBasketRepository(Protocol):
    def load(self) -> UsualBasket: ...

    def save(self, basket: UsualBasket) -> None: ...

    def clear(self) -> None: ...


@dataclass(slots=True)
class InMemoryUsualBasketRepository:
    basket: UsualBasket = UsualBasket()

    def load(self) -> UsualBasket:
        return self.basket

    def save(self, basket: UsualBasket) -> None:
        if not isinstance(basket, UsualBasket):
            raise TypeError("expected UsualBasket")
        self.basket = basket

    def clear(self) -> None:
        self.basket = UsualBasket()


@dataclass(frozen=True, slots=True)
class FileUsualBasketRepository:
    """One local profile, atomic replacement, strict versioned JSON.

    This repository is not a multi-writer transaction system. The host owns
    serialization of edits and resetting a profile should also call clear().
    """

    path: Path

    def load(self) -> UsualBasket:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return UsualBasket()
        except OSError as exc:
            raise UsualBasketRepositoryError("cannot read usual basket") from exc
        try:
            data = json.loads(raw)
            if not isinstance(data, dict) or set(data) != {"schema_version", "items"}:
                raise ValueError("invalid usual basket document keys")
            if type(data["schema_version"]) is not int or data["schema_version"] != 1:
                raise ValueError("unknown usual basket schema version")
            if not isinstance(data["items"], list):
                raise ValueError("usual basket items must be an array")
            items = []
            for entry in data["items"]:
                if not isinstance(entry, dict) or set(entry) != {
                    "item_id", "fallback_quantity"
                }:
                    raise ValueError("invalid usual basket entry")
                if not isinstance(entry["item_id"], str):
                    raise ValueError("invalid usual basket item_id")
                quantity = None
                raw_quantity = entry["fallback_quantity"]
                if raw_quantity is not None:
                    if (
                        not isinstance(raw_quantity, dict)
                        or set(raw_quantity) != {"amount", "unit"}
                        or not isinstance(raw_quantity["amount"], str)
                        or not isinstance(raw_quantity["unit"], str)
                    ):
                        raise ValueError("invalid fallback quantity payload")
                    try:
                        amount = Decimal(raw_quantity["amount"])
                    except InvalidOperation as exc:
                        raise ValueError("invalid fallback decimal") from exc
                    if not amount.is_finite():
                        raise ValueError("non-finite fallback decimal")
                    quantity = Quantity(amount, raw_quantity["unit"])
                items.append(UsualBasketItem(entry["item_id"], quantity))
            result = UsualBasket(tuple(items))
            if tuple(item.item_id for item in result.items) != tuple(
                entry["item_id"] for entry in data["items"]
            ):
                raise ValueError("usual basket items must be canonical and unique")
            return result
        except (ValueError, TypeError, KeyError, RecursionError, json.JSONDecodeError) as exc:
            raise UsualBasketRepositoryError("usual basket file is corrupt") from exc

    def save(self, basket: UsualBasket) -> None:
        if not isinstance(basket, UsualBasket):
            raise TypeError("expected UsualBasket")
        data = {
            "schema_version": 1,
            "items": [
                {
                    "item_id": entry.item_id,
                    "fallback_quantity": (
                        None
                        if entry.fallback_quantity is None
                        else {
                            "amount": str(entry.fallback_quantity.amount),
                            "unit": entry.fallback_quantity.unit,
                        }
                    ),
                }
                for entry in basket.items
            ],
        }
        encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.path.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError as exc:
            raise UsualBasketRepositoryError("cannot save usual basket") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise UsualBasketRepositoryError("cannot clear usual basket") from exc
