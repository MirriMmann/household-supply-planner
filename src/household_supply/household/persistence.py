from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Protocol

from household_supply.domain import Item, Quantity

from .events import (
    ConsumptionObservation,
    HouseholdEvent,
    HouseholdEventId,
    InventoryCorrection,
    PurchaseEvent,
    event_kind,
)
from .history import HouseholdHistory


_EVENT_SCHEMA_VERSION = 1


class HouseholdEventRepositoryError(RuntimeError):
    """Durable household event history cannot be read or written safely."""


class HouseholdEventCorruptionError(HouseholdEventRepositoryError):
    """A stored household event is malformed or fails its integrity check."""


def _require_keys(
    value: Mapping[str, Any],
    *,
    label: str,
    required: set[str],
) -> None:
    if set(value) != required:
        missing = required - set(value)
        unknown = set(value) - required
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            details.append("unknown " + ", ".join(sorted(unknown)))
        raise ValueError(f"{label} has invalid schema: {'; '.join(details)}")


def _require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a JSON string")
    return value


def _quantity(value: Quantity) -> dict[str, str]:
    return {"amount": str(value.amount), "unit": value.unit}


def _parse_quantity(value: Any, *, label: str) -> Quantity:
    obj = _require_mapping(value, label=label)
    _require_keys(obj, label=label, required={"amount", "unit"})
    amount = _require_string(obj["amount"], label=f"{label}.amount")
    unit = _require_string(obj["unit"], label=f"{label}.unit")
    return Quantity(amount, unit)


def _item(value: Item) -> dict[str, Any]:
    return {
        "id": value.id,
        "canonical_name": value.canonical_name,
        "category": value.category,
        "aliases": list(value.aliases),
    }


def _parse_item(value: Any) -> Item:
    obj = _require_mapping(value, label="household event item")
    _require_keys(
        obj,
        label="household event item",
        required={"id", "canonical_name", "category", "aliases"},
    )
    aliases = obj["aliases"]
    if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
        raise ValueError("household event item aliases must be a JSON string array")
    return Item(
        id=_require_string(obj["id"], label="household event item.id"),
        canonical_name=_require_string(
            obj["canonical_name"], label="household event item.canonical_name"
        ),
        category=_require_string(obj["category"], label="household event item.category"),
        aliases=tuple(aliases),
    )


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON number is not allowed: {value}")


def _strict_json_loads(text: str) -> Any:
    return json.loads(
        text,
        object_pairs_hook=_strict_object_pairs,
        parse_constant=_reject_non_finite_json,
    )


def _event_payload_without_digest(event: HouseholdEvent) -> dict[str, Any]:
    common: dict[str, Any] = {
        "schema_version": _EVENT_SCHEMA_VERSION,
        "event_type": event_kind(event),
        "event_id": event.event_id.value,
        "recorded_at": event.recorded_at.isoformat(),
        "item": _item(event.item),
    }
    if isinstance(event, PurchaseEvent):
        common["body"] = {
            "occurred_at": event.occurred_at.isoformat(),
            "quantity": _quantity(event.quantity),
            "sku_id": event.sku_id,
            "source_ref": event.source_ref,
        }
    elif isinstance(event, InventoryCorrection):
        common["body"] = {
            "occurred_at": event.occurred_at.isoformat(),
            "quantity_on_hand": _quantity(event.quantity_on_hand),
            "reason": event.reason,
        }
    elif isinstance(event, ConsumptionObservation):
        common["body"] = {
            "period_start": event.period_start.isoformat(),
            "period_end": event.period_end.isoformat(),
            "quantity_consumed": _quantity(event.quantity_consumed),
            "source_ref": event.source_ref,
        }
    else:  # pragma: no cover - HouseholdEvent union
        raise TypeError(f"unsupported household event: {type(event)!r}")
    return common


def serialize_household_event(event: HouseholdEvent) -> dict[str, Any]:
    payload = _event_payload_without_digest(event)
    payload["digest"] = sha256(_canonical_bytes(payload)).hexdigest()
    return payload


def deserialize_household_event(value: Mapping[str, Any]) -> HouseholdEvent:
    try:
        obj = _require_mapping(value, label="stored household event")
        _require_keys(
            obj,
            label="stored household event",
            required={
                "schema_version",
                "event_type",
                "event_id",
                "recorded_at",
                "item",
                "body",
                "digest",
            },
        )
        if type(obj["schema_version"]) is not int or obj["schema_version"] != _EVENT_SCHEMA_VERSION:
            raise ValueError("unsupported household event schema version")
        digest = _require_string(obj["digest"], label="household event digest")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("household event digest must be lowercase SHA-256 hex")
        unsigned = dict(obj)
        del unsigned["digest"]
        expected = sha256(_canonical_bytes(unsigned)).hexdigest()
        if digest != expected:
            raise ValueError("household event integrity digest does not match")

        event_type = _require_string(obj["event_type"], label="household event type")
        event_id = HouseholdEventId(
            _require_string(obj["event_id"], label="household event id")
        )
        recorded_at = datetime.fromisoformat(
            _require_string(obj["recorded_at"], label="household event recorded_at")
        )
        item = _parse_item(obj["item"])
        body = _require_mapping(obj["body"], label="household event body")

        if event_type == "purchase":
            _require_keys(
                body,
                label="purchase event body",
                required={"occurred_at", "quantity", "sku_id", "source_ref"},
            )
            return PurchaseEvent(
                event_id=event_id,
                item=item,
                quantity=_parse_quantity(body["quantity"], label="purchase quantity"),
                occurred_at=datetime.fromisoformat(
                    _require_string(body["occurred_at"], label="purchase occurred_at")
                ),
                recorded_at=recorded_at,
                sku_id=_require_string(body["sku_id"], label="purchase sku_id"),
                source_ref=_require_string(body["source_ref"], label="purchase source_ref"),
            )

        if event_type == "inventory_correction":
            _require_keys(
                body,
                label="inventory correction body",
                required={"occurred_at", "quantity_on_hand", "reason"},
            )
            return InventoryCorrection(
                event_id=event_id,
                item=item,
                quantity_on_hand=_parse_quantity(
                    body["quantity_on_hand"], label="inventory correction quantity"
                ),
                occurred_at=datetime.fromisoformat(
                    _require_string(
                        body["occurred_at"], label="inventory correction occurred_at"
                    )
                ),
                recorded_at=recorded_at,
                reason=_require_string(body["reason"], label="inventory correction reason"),
            )

        if event_type == "consumption_observation":
            _require_keys(
                body,
                label="consumption observation body",
                required={
                    "period_start",
                    "period_end",
                    "quantity_consumed",
                    "source_ref",
                },
            )
            return ConsumptionObservation(
                event_id=event_id,
                item=item,
                quantity_consumed=_parse_quantity(
                    body["quantity_consumed"], label="consumption quantity"
                ),
                period_start=datetime.fromisoformat(
                    _require_string(
                        body["period_start"], label="consumption period_start"
                    )
                ),
                period_end=datetime.fromisoformat(
                    _require_string(body["period_end"], label="consumption period_end")
                ),
                recorded_at=recorded_at,
                source_ref=_require_string(
                    body["source_ref"], label="consumption source_ref"
                ),
            )
        raise ValueError(f"unsupported household event type: {event_type}")
    except HouseholdEventCorruptionError:
        raise
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise HouseholdEventCorruptionError(f"stored household event is invalid: {exc}") from exc


class HouseholdEventRepository(Protocol):
    def append(self, event: HouseholdEvent) -> None: ...

    def get(self, event_id: HouseholdEventId) -> HouseholdEvent | None: ...

    def history(self) -> HouseholdHistory: ...


class InMemoryHouseholdEventRepository:
    def __init__(self) -> None:
        self._events: dict[HouseholdEventId, HouseholdEvent] = {}

    def append(self, event: HouseholdEvent) -> None:
        if event.event_id in self._events:
            raise HouseholdEventRepositoryError(
                f"household event already exists: {event.event_id}"
            )
        self._events[event.event_id] = event

    def get(self, event_id: HouseholdEventId) -> HouseholdEvent | None:
        return self._events.get(event_id)

    def history(self) -> HouseholdHistory:
        events = sorted(
            self._events.values(),
            key=lambda event: (event.recorded_at, event.event_id.value),
        )
        return HouseholdHistory(tuple(events))


@dataclass(frozen=True, slots=True)
class FileHouseholdEventRepository:
    """Local-first immutable event repository: one integrity-checked JSON file per event."""

    root: Path
    max_event_bytes: int = 256 * 1024

    def __post_init__(self) -> None:
        root = Path(self.root)
        if type(self.max_event_bytes) is not int or self.max_event_bytes <= 0:
            raise ValueError("max_event_bytes must be a positive integer")
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise HouseholdEventRepositoryError(
                f"cannot create household event repository directory: {root}"
            ) from exc
        if not root.is_dir():
            raise HouseholdEventRepositoryError(
                f"household event repository root is not a directory: {root}"
            )
        object.__setattr__(self, "root", root.resolve())

    def _path(self, event_id: HouseholdEventId) -> Path:
        return self.root / f"{event_id.value}.json"

    def append(self, event: HouseholdEvent) -> None:
        target = self._path(event.event_id)
        encoded = (
            json.dumps(
                serialize_household_event(event),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > self.max_event_bytes:
            raise HouseholdEventRepositoryError(
                f"household event exceeds repository size limit: {event.event_id}"
            )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.root,
                prefix=f".{event.event_id.value}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary_path, target)
        except FileExistsError as exc:
            raise HouseholdEventRepositoryError(
                f"household event already exists: {event.event_id}"
            ) from exc
        except OSError as exc:
            raise HouseholdEventRepositoryError(
                f"cannot persist household event: {event.event_id}"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def get(self, event_id: HouseholdEventId) -> HouseholdEvent | None:
        target = self._path(event_id)
        try:
            if target.is_symlink():
                raise HouseholdEventCorruptionError(
                    f"stored household event must not be a symbolic link: {event_id}"
                )
            if not target.exists():
                return None
            if not target.is_file():
                raise HouseholdEventCorruptionError(
                    f"stored household event is not a regular file: {event_id}"
                )
            if target.stat().st_size > self.max_event_bytes:
                raise HouseholdEventCorruptionError(
                    f"stored household event exceeds repository size limit: {event_id}"
                )
            raw = target.read_bytes()
            if len(raw) > self.max_event_bytes:
                raise HouseholdEventCorruptionError(
                    f"stored household event exceeds repository size limit: {event_id}"
                )
        except HouseholdEventRepositoryError:
            raise
        except OSError as exc:
            raise HouseholdEventRepositoryError(
                f"cannot read household event: {event_id}"
            ) from exc
        try:
            value = _strict_json_loads(raw.decode("utf-8"))
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValueError,
            RecursionError,
        ) as exc:
            raise HouseholdEventCorruptionError(
                f"stored household event is not valid UTF-8 JSON: {event_id}"
            ) from exc
        if not isinstance(value, dict):
            raise HouseholdEventCorruptionError(
                f"stored household event root is not an object: {event_id}"
            )
        event = deserialize_household_event(value)
        if event.event_id != event_id:
            raise HouseholdEventCorruptionError(
                f"stored household event identity does not match filename: {event_id}"
            )
        return event

    def history(self) -> HouseholdHistory:
        try:
            paths = tuple(self.root.glob("*.json"))
        except OSError as exc:
            raise HouseholdEventRepositoryError(
                "cannot list household event repository"
            ) from exc
        events: list[HouseholdEvent] = []
        for path in paths:
            try:
                event_id = HouseholdEventId(path.stem)
            except ValueError as exc:
                raise HouseholdEventCorruptionError(
                    f"household repository contains invalid event filename: {path.name}"
                ) from exc
            event = self.get(event_id)
            if event is not None:
                events.append(event)
        events.sort(key=lambda event: (event.recorded_at, event.event_id.value))
        return HouseholdHistory(tuple(events))
