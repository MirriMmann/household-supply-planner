from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping, Protocol


_PLAN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_RECORD_SCHEMA_VERSION = 1

_REQUEST_SNAPSHOT_REQUIRED_KEYS = {"budget", "demands", "inventory", "objective"}
_REQUEST_SNAPSHOT_OPTIONAL_KEYS = {"decision_basis"}
_RESULT_REQUIRED_KEYS = {
    "status",
    "market",
    "total_cost",
    "budget_remaining",
    "purchases",
    "coverage",
    "projected_leftovers",
    "infeasibility_reasons",
    "warnings",
    "explanation",
}
_RESULT_OPTIONAL_KEYS = {"minimum_required_cost", "objective"}
_MARKET_EVIDENCE_KEYS = {
    "captured_at",
    "policy",
    "catalog",
    "batches",
    "dispositions",
    "offers",
}


def _require_snapshot_schema(
    value: Mapping[str, Any],
    *,
    label: str,
    required: set[str],
    optional: set[str] | frozenset[str] = frozenset(),
) -> None:
    keys = set(value)
    missing = required - keys
    unknown = keys - required - optional
    if missing or unknown:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if unknown:
            details.append("unknown " + ", ".join(sorted(unknown)))
        raise ValueError(f"{label} has invalid schema: {'; '.join(details)}")


def _require_nonempty_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _require_decimal_string(
    value: Any,
    *,
    label: str,
    positive: bool = False,
) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{label} must be a finite decimal string") from exc
    if not parsed.is_finite():
        raise ValueError(f"{label} must be a finite decimal string")
    if positive and parsed <= 0:
        raise ValueError(f"{label} must be positive")
    return parsed


def _require_quantity_snapshot(value: Any, *, label: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    _require_snapshot_schema(
        value,
        label=label,
        required={"amount", "unit"},
    )
    _require_decimal_string(value["amount"], label=f"{label}.amount")
    _require_nonempty_string(value["unit"], label=f"{label}.unit")


def _require_decision_basis(value: Any) -> None:
    label = "plan request decision_basis"
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    _require_snapshot_schema(
        value,
        label=label,
        required={
            "kind",
            "as_of",
            "horizon_days",
            "explicit_needs",
            "recurring_estimates",
            "contributions",
        },
    )
    if value["kind"] != "household_replenishment":
        raise ValueError(
            "plan request decision_basis kind must be 'household_replenishment'"
        )

    as_of = _require_nonempty_string(value["as_of"], label=f"{label}.as_of")
    try:
        parsed_as_of = datetime.fromisoformat(as_of)
    except ValueError as exc:
        raise ValueError(f"{label}.as_of must be an ISO datetime") from exc
    _require_aware(parsed_as_of, label=f"{label}.as_of")
    _require_decimal_string(
        value["horizon_days"],
        label=f"{label}.horizon_days",
        positive=True,
    )

    explicit_needs = value["explicit_needs"]
    recurring_estimates = value["recurring_estimates"]
    contributions = value["contributions"]
    if not isinstance(explicit_needs, list):
        raise ValueError(f"{label}.explicit_needs must be an array")
    if not isinstance(recurring_estimates, list):
        raise ValueError(f"{label}.recurring_estimates must be an array")
    if not isinstance(contributions, list):
        raise ValueError(f"{label}.contributions must be an array")
    if not contributions:
        raise ValueError(f"{label}.contributions must not be empty")

    explicit_ids: set[str] = set()
    for index, entry in enumerate(explicit_needs):
        entry_label = f"{label}.explicit_needs[{index}]"
        if not isinstance(entry, Mapping):
            raise ValueError(f"{entry_label} must be an object")
        _require_snapshot_schema(
            entry,
            label=entry_label,
            required={"item_id", "quantity"},
        )
        item_id = _require_nonempty_string(
            entry["item_id"], label=f"{entry_label}.item_id"
        )
        if item_id in explicit_ids:
            raise ValueError(f"{label}.explicit_needs contains duplicate item_id")
        explicit_ids.add(item_id)
        _require_quantity_snapshot(
            entry["quantity"], label=f"{entry_label}.quantity"
        )

    recurring_ids: set[str] = set()
    recurring_required = {
        "item_id",
        "daily_quantity",
        "sample_count",
        "observed_days",
        "total_depleted",
        "observed_microseconds",
        "daily_min",
        "daily_max",
        "uncertainty",
        "contribution_quantity",
    }
    for index, entry in enumerate(recurring_estimates):
        entry_label = f"{label}.recurring_estimates[{index}]"
        if not isinstance(entry, Mapping):
            raise ValueError(f"{entry_label} must be an object")
        _require_snapshot_schema(
            entry,
            label=entry_label,
            required=recurring_required,
        )
        item_id = _require_nonempty_string(
            entry["item_id"], label=f"{entry_label}.item_id"
        )
        if item_id in recurring_ids:
            raise ValueError(f"{label}.recurring_estimates contains duplicate item_id")
        recurring_ids.add(item_id)
        if type(entry["sample_count"]) is not int or entry["sample_count"] < 1:
            raise ValueError(f"{entry_label}.sample_count must be a positive integer")
        if (
            type(entry["observed_microseconds"]) is not int
            or entry["observed_microseconds"] <= 0
        ):
            raise ValueError(
                f"{entry_label}.observed_microseconds must be a positive integer"
            )
        _require_decimal_string(
            entry["observed_days"],
            label=f"{entry_label}.observed_days",
            positive=True,
        )
        for quantity_key in (
            "daily_quantity",
            "total_depleted",
            "daily_min",
            "daily_max",
            "uncertainty",
            "contribution_quantity",
        ):
            _require_quantity_snapshot(
                entry[quantity_key],
                label=f"{entry_label}.{quantity_key}",
            )

    contribution_keys: set[tuple[str, str]] = set()
    recurring_contribution_ids: set[str] = set()
    explicit_contribution_ids: set[str] = set()
    for index, entry in enumerate(contributions):
        entry_label = f"{label}.contributions[{index}]"
        if not isinstance(entry, Mapping):
            raise ValueError(f"{entry_label} must be an object")
        _require_snapshot_schema(
            entry,
            label=entry_label,
            required={"source_id", "contribution_id", "item_id", "quantity"},
        )
        source_id = _require_nonempty_string(
            entry["source_id"], label=f"{entry_label}.source_id"
        )
        contribution_id = _require_nonempty_string(
            entry["contribution_id"], label=f"{entry_label}.contribution_id"
        )
        item_id = _require_nonempty_string(
            entry["item_id"], label=f"{entry_label}.item_id"
        )
        key = (source_id, contribution_id)
        if key in contribution_keys:
            raise ValueError(f"{label}.contributions contains duplicate identity")
        contribution_keys.add(key)
        _require_quantity_snapshot(
            entry["quantity"], label=f"{entry_label}.quantity"
        )
        if source_id == "household:recurring":
            recurring_contribution_ids.add(item_id)
        elif source_id == "request:explicit":
            explicit_contribution_ids.add(item_id)

    if recurring_ids != recurring_contribution_ids:
        raise ValueError(
            f"{label} recurring estimates do not match recurring contributions"
        )
    if explicit_ids != explicit_contribution_ids:
        raise ValueError(
            f"{label} explicit needs do not match explicit contributions"
        )



class PlanRepositoryError(RuntimeError):
    """Persistent plan history cannot be read or written safely."""


class PlanRecordCorruptionError(PlanRepositoryError):
    """A stored plan record is malformed or fails its integrity check."""


def _require_aware(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _canonical_json_object(value: Mapping[str, Any], *, label: str) -> str:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be a mapping")
    try:
        encoded = json.dumps(
            dict(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError(f"{label} must contain only finite JSON values") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{label} must encode a JSON object")
    return encoded


@dataclass(frozen=True, slots=True, order=True)
class PlanId:
    value: str

    def __post_init__(self) -> None:
        value = self.value.strip()
        if not _PLAN_ID_PATTERN.fullmatch(value):
            raise ValueError(
                "plan id must contain 1-64 lowercase ASCII letters, digits, '_' or '-', "
                "and must start with a letter or digit"
            )
        object.__setattr__(self, "value", value)

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CanonicalJsonObject:
    """Immutable canonical JSON object used inside a durable plan snapshot."""

    text: str

    @classmethod
    def from_mapping(
        cls, value: Mapping[str, Any], *, label: str = "JSON object"
    ) -> CanonicalJsonObject:
        return cls(_canonical_json_object(value, label=label))

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("canonical JSON text must be str")
        try:
            value = json.loads(self.text)
        except (json.JSONDecodeError, RecursionError) as exc:
            raise ValueError("canonical JSON text is invalid") from exc
        if not isinstance(value, dict):
            raise ValueError("canonical JSON text must encode an object")
        canonical = _canonical_json_object(value, label="canonical JSON object")
        object.__setattr__(self, "text", canonical)

    def to_mapping(self) -> dict[str, Any]:
        value = json.loads(self.text)
        if not isinstance(value, dict):  # pragma: no cover - guarded by construction
            raise AssertionError("canonical JSON object invariant violated")
        return value


def _record_digest(
    plan_id: PlanId,
    created_at: datetime,
    request: CanonicalJsonObject,
    result: CanonicalJsonObject,
    market_evidence: CanonicalJsonObject,
) -> str:
    canonical = "\x00".join(
        (
            str(_RECORD_SCHEMA_VERSION),
            plan_id.value,
            created_at.isoformat(),
            request.text,
            result.text,
            market_evidence.text,
        )
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class PlanRecord:
    """Immutable historical snapshot of one completed planning computation.

    The record stores canonical request, result, and market-evidence JSON. It is
    deliberately a historical snapshot rather than a recipe for recomputation.
    """

    plan_id: PlanId
    created_at: datetime
    request: CanonicalJsonObject
    result: CanonicalJsonObject
    market_evidence: CanonicalJsonObject
    digest: str

    def __post_init__(self) -> None:
        _require_aware(self.created_at, label="plan record created_at")
        digest = self.digest.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("plan record digest must be a lowercase SHA-256 hex digest")
        expected = _record_digest(
            self.plan_id,
            self.created_at,
            self.request,
            self.result,
            self.market_evidence,
        )
        if digest != expected:
            raise ValueError("plan record digest does not match record contents")
        object.__setattr__(self, "digest", digest)

    @classmethod
    def create(
        cls,
        *,
        plan_id: PlanId,
        created_at: datetime,
        request: Mapping[str, Any],
        result: Mapping[str, Any],
        market_evidence: Mapping[str, Any],
    ) -> PlanRecord:
        _require_aware(created_at, label="plan record created_at")
        _require_snapshot_schema(
            request,
            label="plan request snapshot",
            required=_REQUEST_SNAPSHOT_REQUIRED_KEYS,
            optional=_REQUEST_SNAPSHOT_OPTIONAL_KEYS,
        )
        if "decision_basis" in request:
            _require_decision_basis(request["decision_basis"])
        _require_snapshot_schema(
            result,
            label="plan result snapshot",
            required=_RESULT_REQUIRED_KEYS,
            optional=_RESULT_OPTIONAL_KEYS,
        )
        _require_snapshot_schema(
            market_evidence,
            label="market evidence snapshot",
            required=_MARKET_EVIDENCE_KEYS,
        )
        request_json = CanonicalJsonObject.from_mapping(request, label="plan request snapshot")
        result_json = CanonicalJsonObject.from_mapping(result, label="plan result snapshot")
        evidence_json = CanonicalJsonObject.from_mapping(
            market_evidence, label="market evidence snapshot"
        )
        digest = _record_digest(
            plan_id, created_at, request_json, result_json, evidence_json
        )
        return cls(
            plan_id=plan_id,
            created_at=created_at,
            request=request_json,
            result=result_json,
            market_evidence=evidence_json,
            digest=digest,
        )

    def to_storage_payload(self) -> dict[str, Any]:
        return {
            "schema_version": _RECORD_SCHEMA_VERSION,
            "plan_id": self.plan_id.value,
            "created_at": self.created_at.isoformat(),
            "request": self.request.to_mapping(),
            "result": self.result.to_mapping(),
            "market_evidence": self.market_evidence.to_mapping(),
            "digest": self.digest,
        }

    @classmethod
    def from_storage_payload(cls, value: Mapping[str, Any]) -> PlanRecord:
        required = {
            "schema_version",
            "plan_id",
            "created_at",
            "request",
            "result",
            "market_evidence",
            "digest",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise PlanRecordCorruptionError("stored plan record has invalid top-level schema")
        if type(value["schema_version"]) is not int or value["schema_version"] != _RECORD_SCHEMA_VERSION:
            raise PlanRecordCorruptionError("stored plan record uses unsupported schema version")
        for key in ("plan_id", "created_at", "digest"):
            if not isinstance(value[key], str):
                raise PlanRecordCorruptionError(f"stored plan record field {key!r} must be string")
        for key in ("request", "result", "market_evidence"):
            if not isinstance(value[key], Mapping):
                raise PlanRecordCorruptionError(f"stored plan record field {key!r} must be object")
        try:
            created_at = datetime.fromisoformat(value["created_at"])
            record = cls.create(
                plan_id=PlanId(value["plan_id"]),
                created_at=created_at,
                request=value["request"],
                result=value["result"],
                market_evidence=value["market_evidence"],
            )
        except (TypeError, ValueError) as exc:
            raise PlanRecordCorruptionError(f"stored plan record is invalid: {exc}") from exc
        if record.digest != value["digest"]:
            raise PlanRecordCorruptionError("stored plan record integrity digest does not match")
        return record


class PlanRepository(Protocol):
    def save(self, record: PlanRecord) -> None: ...

    def get(self, plan_id: PlanId) -> PlanRecord | None: ...

    def list_recent(self, limit: int) -> tuple[PlanRecord, ...]: ...

    def clear(self) -> int: ...


class InMemoryPlanRepository:
    """Process-local repository useful for embedding and deterministic tests."""

    def __init__(self) -> None:
        self._records: dict[PlanId, PlanRecord] = {}

    def save(self, record: PlanRecord) -> None:
        if record.plan_id in self._records:
            raise PlanRepositoryError(f"plan record already exists: {record.plan_id}")
        self._records[record.plan_id] = record

    def get(self, plan_id: PlanId) -> PlanRecord | None:
        return self._records.get(plan_id)

    def list_recent(self, limit: int) -> tuple[PlanRecord, ...]:
        _validate_limit(limit)
        records = sorted(
            self._records.values(),
            key=lambda record: (record.created_at, record.plan_id.value),
            reverse=True,
        )
        return tuple(records[:limit])

    def clear(self) -> int:
        count = len(self._records)
        self._records.clear()
        return count


def _validate_limit(limit: int) -> None:
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("plan history limit must be an integer from 1 to 1000")


@dataclass(frozen=True, slots=True)
class FilePlanRepository:
    """Small local-first JSON repository with strict records and no database dependency."""

    root: Path
    max_record_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        root = Path(self.root)
        if type(self.max_record_bytes) is not int or self.max_record_bytes <= 0:
            raise ValueError("max_record_bytes must be a positive integer")
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PlanRepositoryError(f"cannot create plan repository directory: {root}") from exc
        if not root.is_dir():
            raise PlanRepositoryError(f"plan repository root is not a directory: {root}")
        object.__setattr__(self, "root", root.resolve())

    def _path(self, plan_id: PlanId) -> Path:
        return self.root / f"{plan_id.value}.json"

    def save(self, record: PlanRecord) -> None:
        target = self._path(record.plan_id)
        encoded = (
            json.dumps(
                record.to_storage_payload(),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > self.max_record_bytes:
            raise PlanRepositoryError(
                f"plan record exceeds repository size limit: {record.plan_id}"
            )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.root,
                prefix=f".{record.plan_id.value}.",
                suffix=".tmp",
                delete=False,
            ) as stream:
                temporary_path = Path(stream.name)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())

            # Hard-link publication is atomic and refuses to replace an existing
            # identity. Readers therefore never observe a partially written JSON
            # target while another process is saving the same record.
            os.link(temporary_path, target)
        except FileExistsError as exc:
            raise PlanRepositoryError(
                f"plan record already exists: {record.plan_id}"
            ) from exc
        except OSError as exc:
            raise PlanRepositoryError(
                f"cannot persist plan record: {record.plan_id}"
            ) from exc
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def get(self, plan_id: PlanId) -> PlanRecord | None:
        target = self._path(plan_id)
        try:
            if target.is_symlink():
                raise PlanRecordCorruptionError(
                    f"stored plan record must not be a symbolic link: {plan_id}"
                )
            if not target.exists():
                return None
            if not target.is_file():
                raise PlanRecordCorruptionError(
                    f"stored plan record is not a regular file: {plan_id}"
                )
            size = target.stat().st_size
            if size > self.max_record_bytes:
                raise PlanRecordCorruptionError(
                    f"stored plan record exceeds repository size limit: {plan_id}"
                )
            raw = target.read_bytes()
            if len(raw) > self.max_record_bytes:
                raise PlanRecordCorruptionError(
                    f"stored plan record exceeds repository size limit: {plan_id}"
                )
        except PlanRepositoryError:
            raise
        except OSError as exc:
            raise PlanRepositoryError(f"cannot read plan record: {plan_id}") from exc
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise PlanRecordCorruptionError(
                f"stored plan record is not valid UTF-8 JSON: {plan_id}"
            ) from exc
        if not isinstance(value, dict):
            raise PlanRecordCorruptionError(
                f"stored plan record root is not an object: {plan_id}"
            )
        record = PlanRecord.from_storage_payload(value)
        if record.plan_id != plan_id:
            raise PlanRecordCorruptionError(
                f"stored plan record identity does not match filename: {plan_id}"
            )
        return record

    def list_recent(self, limit: int) -> tuple[PlanRecord, ...]:
        _validate_limit(limit)
        records: list[PlanRecord] = []
        try:
            paths = tuple(self.root.glob("*.json"))
        except OSError as exc:
            raise PlanRepositoryError("cannot list plan repository") from exc
        for path in paths:
            try:
                plan_id = PlanId(path.stem)
            except ValueError as exc:
                raise PlanRecordCorruptionError(
                    f"plan repository contains invalid record filename: {path.name}"
                ) from exc
            record = self.get(plan_id)
            if record is None:  # pragma: no cover - race with external deletion
                continue
            records.append(record)
        records.sort(
            key=lambda record: (record.created_at, record.plan_id.value),
            reverse=True,
        )
        return tuple(records[:limit])

    def clear(self) -> int:
        """Delete published plan records without touching unrelated files."""

        try:
            paths = tuple(self.root.glob("*.json"))
        except OSError as exc:
            raise PlanRepositoryError("cannot list plan repository for reset") from exc

        deleted = 0
        for path in paths:
            try:
                if not path.is_file() and not path.is_symlink():
                    raise PlanRepositoryError(
                        f"cannot reset non-file plan record: {path.name}"
                    )
                path.unlink()
                deleted += 1
            except PlanRepositoryError:
                raise
            except OSError as exc:
                raise PlanRepositoryError(
                    f"cannot delete plan record during reset: {path.name}"
                ) from exc
        return deleted
