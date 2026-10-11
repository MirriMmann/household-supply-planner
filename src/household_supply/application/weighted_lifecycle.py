"""Opt-in, locally persisted weighted procurement lifecycle.

Does not turn a per-kg price into a packaged SKU. Seller minimum/step MUST
come from an injected trusted source, never from an unauthenticated request.
All actual purchases are explicit events, with immutable receipt evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Callable
from uuid import uuid4

from household_supply.application.persistence import PlanId
from household_supply.domain import Item, Money, Quantity
from household_supply.domain.weighted import (
    VerifiedWeightQuote, VerifiedWeightSaleRule, WeightedPriceObservation,
    quote_verified_mass,
)
from household_supply.household import (
    HouseholdEventId, HouseholdEventRepositoryError, HouseholdHistory,
    HouseholdLearningService, PurchaseEvent, depletion_learning_reports,
    project_household_state,
)
from household_supply.market.catalogs.globus_weight_candidates import WEIGHT_CANDIDATES
from household_supply.market.providers.globus_weight import GlobusWeightPriceService


class WeightedProcurementError(ValueError):
    """Requested weighted action does not meet the verified contract."""


class WeightedProcurementConflict(WeightedProcurementError):
    """Stale preview, changed receipt, duplicated identity or budget conflict."""


class WeightedStorageError(RuntimeError):
    """Durable weighted plan/receipt could not be verified or stored."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise WeightedProcurementError("weighted lifecycle timestamps must be aware")


def _positive_mass(value: Quantity) -> None:
    if not isinstance(value, Quantity) or value.dimension != "mass" or value.amount <= 0:
        raise WeightedProcurementError("weighted purchase mass must be positive")


def _quantity(q: Quantity) -> dict[str, str]:
    return {"amount": str(q.amount), "unit": q.unit}


def _money(m: Money) -> dict[str, str]:
    return {"amount": str(m.amount), "currency": m.currency}


def _q(value: dict) -> Quantity:
    if not isinstance(value, dict) or set(value) != {"amount", "unit"}:
        raise ValueError("invalid stored mass fields")
    if not all(isinstance(x, str) for x in value.values()):
        raise ValueError("stored mass fields must be strings")
    return Quantity(value["amount"], value["unit"])


def _m(value: dict) -> Money:
    if not isinstance(value, dict) or set(value) != {"amount", "currency"}:
        raise ValueError("invalid stored money fields")
    if not all(isinstance(x, str) for x in value.values()):
        raise ValueError("stored money fields must be strings")
    return Money(value["amount"], value["currency"])


@dataclass(frozen=True, slots=True)
class WeightedPlanRecord:
    plan_id: PlanId
    item: Item
    quote: VerifiedWeightQuote
    budget: Money
    created_at: datetime

    def __post_init__(self) -> None:
        _time(self.created_at)
        if self.item.id != self.quote.observation.item_id:
            raise WeightedProcurementError("weighted plan Item mismatches price evidence")
        if self.quote.observation.observed_at > self.created_at:
            raise WeightedProcurementError("weighted plan created before price acquisition")
        if self.budget.currency != self.quote.estimated_upper_bound.currency:
            raise WeightedProcurementError("weighted plan budget currency mismatch")
        if self.budget.amount < self.quote.estimated_upper_bound.amount:
            raise WeightedProcurementError("weighted plan exceeds hard budget")

    def to_mapping(self) -> dict:
        q = self.quote
        return {
            "plan_id": self.plan_id.value,
            "item": {"id": self.item.id, "name": self.item.canonical_name,
                     "category": self.item.category},
            "budget": _money(self.budget),
            "created_at": self.created_at.isoformat(),
            "observation": {
                "item_id": q.observation.item_id,
                "source_ref": q.observation.source_ref,
                "observed_at": q.observation.observed_at.isoformat(),
                "price": _money(q.observation.price),
                "reference_mass": _quantity(q.observation.reference_mass),
                "available": q.observation.available,
            },
            "rule": {
                "item_id": q.rule.item_id,
                "source_ref": q.rule.source_ref,
                "verified_at": q.rule.verified_at.isoformat(),
                "minimum": _quantity(q.rule.minimum),
                "increment": _quantity(q.rule.increment),
            },
            "requested": _quantity(q.requested),
            "sellable": _quantity(q.sellable),
            "estimated_upper_bound": _money(q.estimated_upper_bound),
        }

    @classmethod
    def from_mapping(cls, data: dict) -> WeightedPlanRecord:
        if not isinstance(data, dict) or set(data) != {
            "plan_id", "item", "budget", "created_at", "observation", "rule",
            "requested", "sellable", "estimated_upper_bound",
        }:
            raise ValueError("weighted plan schema mismatch")
        item, o, rule = data["item"], data["observation"], data["rule"]
        if set(item) != {"id", "name", "category"} or set(o) != {
            "item_id", "source_ref", "observed_at", "price", "reference_mass", "available",
        } or set(rule) != {
            "item_id", "source_ref", "verified_at", "minimum", "increment",
        }:
            raise ValueError("weighted plan evidence schema mismatch")
        observation = WeightedPriceObservation(
            item_id=o["item_id"], source_ref=o["source_ref"],
            observed_at=datetime.fromisoformat(o["observed_at"]),
            price=_m(o["price"]), reference_mass=_q(o["reference_mass"]),
            available=o["available"],
        )
        trusted_rule = VerifiedWeightSaleRule(
            item_id=rule["item_id"], source_ref=rule["source_ref"],
            verified_at=datetime.fromisoformat(rule["verified_at"]),
            minimum=_q(rule["minimum"]), increment=_q(rule["increment"]),
        )
        quote = VerifiedWeightQuote(
            observation, trusted_rule, _q(data["requested"]),
            _q(data["sellable"]), _m(data["estimated_upper_bound"]),
        )
        return cls(
            PlanId(data["plan_id"]), Item(item["id"], item["name"], item["category"]),
            quote, _m(data["budget"]), datetime.fromisoformat(data["created_at"]),
        )


@dataclass(frozen=True, slots=True)
class WeightedReceipt:
    plan_id: PlanId
    event_id: HouseholdEventId
    actual_mass: Quantity
    actual_cost: Money
    receipt_ref: str
    occurred_at: datetime
    recorded_at: datetime

    def __post_init__(self) -> None:
        _positive_mass(self.actual_mass)
        if self.actual_cost.amount < 0 or not self.receipt_ref.strip():
            raise WeightedProcurementError("receipt amount and reference are required")
        _time(self.occurred_at)
        _time(self.recorded_at)
        if self.recorded_at < self.occurred_at:
            raise WeightedProcurementError("receipt recorded before purchase occurred")

    def to_mapping(self) -> dict:
        return {
            "plan_id": self.plan_id.value, "event_id": self.event_id.value,
            "actual_mass": _quantity(self.actual_mass),
            "actual_cost": _money(self.actual_cost),
            "receipt_ref": self.receipt_ref,
            "occurred_at": self.occurred_at.isoformat(),
            "recorded_at": self.recorded_at.isoformat(),
        }

    @classmethod
    def from_mapping(cls, data: dict) -> WeightedReceipt:
        if not isinstance(data, dict) or set(data) != {
            "plan_id", "event_id", "actual_mass", "actual_cost", "receipt_ref",
            "occurred_at", "recorded_at",
        }:
            raise ValueError("weighted receipt schema mismatch")
        return cls(
            PlanId(data["plan_id"]), HouseholdEventId(data["event_id"]),
            _q(data["actual_mass"]), _m(data["actual_cost"]), data["receipt_ref"],
            datetime.fromisoformat(data["occurred_at"]),
            datetime.fromisoformat(data["recorded_at"]),
        )


class FileWeightedPlanRepository:
    """Create-only snapshots; SHA integrity and typed re-validation on every read."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "plans").mkdir(exist_ok=True)
        (self.root / "receipts").mkdir(exist_ok=True)

    @staticmethod
    def _payload(record: WeightedPlanRecord | WeightedReceipt) -> bytes:
        body = {"schema_version": 1, "body": record.to_mapping()}
        source = json.dumps(body, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":"), allow_nan=False).encode("utf-8")
        body["digest"] = sha256(source).hexdigest()
        return (json.dumps(body, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")

    def _path(self, kind: str, key: str) -> Path:
        if kind not in {"plans", "receipts"}:
            raise ValueError("unsupported weighted record kind")
        return self.root / kind / f"{key}.json"

    def _get(self, kind: str, key: str, cls):
        path = self._path(kind, key)
        try:
            if path.is_symlink():
                raise WeightedStorageError("weighted record cannot be a symlink")
            if not path.exists():
                return None
            if not path.is_file() or path.stat().st_size > 256_000:
                raise WeightedStorageError("weighted record is missing regular-file bounds")
            raw = path.read_bytes()
            def strict_pairs(pairs):
                value = {}
                for name, entry in pairs:
                    if name in value:
                        raise ValueError("duplicate weighted JSON key")
                    value[name] = entry
                return value
            obj = json.loads(raw.decode("utf-8"), object_pairs_hook=strict_pairs,
                             parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
            if set(obj) != {"schema_version", "body", "digest"} or type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
                raise ValueError("invalid weighted storage envelope")
            unsigned = {"schema_version": obj["schema_version"], "body": obj["body"]}
            canonical = json.dumps(unsigned, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), allow_nan=False).encode("utf-8")
            if sha256(canonical).hexdigest() != obj["digest"]:
                raise ValueError("weighted record integrity mismatch")
            record = cls.from_mapping(obj["body"])
            identity = record.plan_id.value if kind == "plans" else record.event_id.value
            if identity != key:
                raise ValueError("weighted filename identity mismatch")
            return record
        except WeightedStorageError:
            raise
        except (OSError, TypeError, ValueError, UnicodeError, OverflowError) as exc:
            raise WeightedStorageError(f"weighted {kind} data invalid: {exc}") from exc

    def get_plan(self, plan_id: PlanId) -> WeightedPlanRecord | None:
        return self._get("plans", plan_id.value, WeightedPlanRecord)

    def get_receipt(self, event_id: HouseholdEventId) -> WeightedReceipt | None:
        return self._get("receipts", event_id.value, WeightedReceipt)

    def _save(self, kind: str, key: str, record) -> None:
        target = self._path(kind, key)
        payload = self._payload(record)
        if len(payload) > 256_000:
            raise WeightedStorageError("weighted record exceeds size bound")
        temp = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", dir=target.parent, prefix=f".{key}.", suffix=".tmp", delete=False
            ) as fp:
                temp = Path(fp.name)
                fp.write(payload)
                fp.flush()
                os.fsync(fp.fileno())
            os.link(temp, target)
        except FileExistsError as exc:
            raise WeightedProcurementConflict("weighted record already exists") from exc
        except OSError as exc:
            raise WeightedStorageError("cannot store weighted record") from exc
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)

    def save_plan(self, record: WeightedPlanRecord) -> None:
        self._save("plans", record.plan_id.value, record)

    def save_receipt(self, record: WeightedReceipt) -> None:
        self._save("receipts", record.event_id.value, record)


@dataclass(frozen=True, slots=True)
class WeightedPreview:
    record: WeightedPlanRecord
    expires_at: datetime

    def __post_init__(self) -> None:
        _time(self.expires_at)
        if self.expires_at <= self.record.created_at:
            raise ValueError("weighted preview expires before it begins")


class WeightedProcurementService:
    """Guarded full local lifecycle. Seller rules injected by trusted integration.

    Live Globus currently has no such seller source: do NOT wire this into the
    default public web routes until authoritative min/step evidence is available.
    """

    def __init__(
        self, *,
        prices: GlobusWeightPriceService,
        sale_rules: Callable[[str, str], VerifiedWeightSaleRule | None],
        repository: FileWeightedPlanRepository,
        household: HouseholdLearningService,
        clock: Callable[[], datetime] = _now,
        max_price_age: timedelta = timedelta(minutes=20),
        max_rule_age: timedelta = timedelta(days=1),
    ) -> None:
        self.prices = prices
        self.sale_rules = sale_rules
        self.repository = repository
        self.household = household
        self.clock = clock
        self.max_price_age = max_price_age
        self.max_rule_age = max_rule_age
        if max_price_age <= timedelta(0) or max_rule_age <= timedelta(0):
            raise ValueError("weighted evidence TTL must be positive")
        self._lock = RLock()
        self._previews: dict[PlanId, WeightedPreview] = {}

    def _now(self) -> datetime:
        value = self.clock()
        _time(value)
        return value

    def preview(self, item_id: str, requested: Quantity, budget: Money) -> WeightedPreview:
        if budget.amount < 0:
            raise WeightedProcurementError("weighted budget must be nonnegative")
        estimate = self.prices.estimate(item_id=item_id, requested=requested)
        observation = estimate.observation
        if not observation.available:
            raise WeightedProcurementError("weighted listing is unavailable")
        rule = self.sale_rules(item_id, observation.source_ref)
        if not isinstance(rule, VerifiedWeightSaleRule):
            raise WeightedProcurementError("no trusted seller minimum/step available")
        now = self._now()
        if not (
            now - self.max_price_age <= observation.observed_at <= now
            and now - self.max_rule_age <= rule.verified_at <= observation.observed_at
        ):
            raise WeightedProcurementConflict("weighted price/rule evidence is stale or future-dated")
        candidate = next((x for x in WEIGHT_CANDIDATES if x.item_id == item_id), None)
        if candidate is None or candidate.source_url != observation.source_ref:
            raise WeightedProcurementError("weighted Item not present in source-backed registry")
        quote = quote_verified_mass(observation, rule, requested)
        if quote.estimated_upper_bound.currency != budget.currency:
            raise WeightedProcurementError("weighted budget currency mismatch")
        if quote.estimated_upper_bound.amount > budget.amount:
            raise WeightedProcurementError("weighted plan exceeds budget")
        record = WeightedPlanRecord(
            PlanId("wp-" + uuid4().hex),
            Item(item_id, candidate.item_name, "weighted_grocery"),
            quote, budget, now,
        )
        preview = WeightedPreview(record, now + self.max_price_age)
        with self._lock:
            self._previews = {
                key: value for key, value in self._previews.items()
                if value.expires_at >= now
            }
            if len(self._previews) >= 16:
                raise WeightedProcurementConflict("too many unconfirmed weighted previews")
            self._previews[record.plan_id] = preview
        return preview

    def save_plan(self, plan_id: PlanId) -> WeightedPlanRecord:
        with self._lock:
            existing = self.repository.get_plan(plan_id)
            if existing is not None:
                return existing
            preview = self._previews.get(plan_id)
            if preview is None:
                raise WeightedProcurementConflict("no verified weighted preview for plan")
            now = self._now()
            if now > preview.expires_at:
                del self._previews[plan_id]
                raise WeightedProcurementConflict("weighted preview expired")
            self.repository.save_plan(preview.record)
            del self._previews[plan_id]
            return preview.record

    def confirm_actual(
        self, *, plan_id: PlanId, event_id: HouseholdEventId,
        actual_mass: Quantity, actual_cost: Money, receipt_ref: str,
        occurred_at: datetime | None = None,
    ) -> WeightedReceipt:
        _positive_mass(actual_mass)
        if not isinstance(receipt_ref, str) or not receipt_ref.strip():
            raise WeightedProcurementError("actual receipt reference required")
        with self._lock:
            plan = self.repository.get_plan(plan_id)
            if plan is None:
                raise WeightedProcurementConflict("weighted plan not saved")
            if not actual_mass.compatible_with(plan.quote.sellable):
                raise WeightedProcurementError("actual mass has incompatible dimension")
            if actual_mass.base_amount > plan.quote.sellable.base_amount:
                raise WeightedProcurementConflict("actual mass exceeds explicitly approved amount")
            if (
                actual_cost.currency != plan.budget.currency
                or actual_cost.amount < 0
                or actual_cost.amount > plan.quote.estimated_upper_bound.amount
                or actual_cost.amount > plan.budget.amount
            ):
                raise WeightedProcurementConflict("actual cost exceeds approved amount or currency")
            now = self._now()
            at = occurred_at or now
            _time(at)
            if at > now:
                raise WeightedProcurementConflict("purchase cannot occur in the future")
            # Fingerprint binds the completed receipt to exactly one household event.
            receipt_basis = {
                "plan_id": plan_id.value, "event_id": event_id.value,
                "actual_mass_g": str(actual_mass.base_amount),
                "actual_cost": _money(actual_cost), "receipt_ref": receipt_ref,
            }
            basis = json.dumps(receipt_basis, sort_keys=True, separators=(",", ":")).encode("utf-8")
            source = f"weighted-plan:{plan_id.value}:" + sha256(basis).hexdigest()
            existing = self.household.repository.get(event_id)
            if existing is not None:
                if not isinstance(existing, PurchaseEvent) or (
                    existing.item != plan.item or
                    existing.quantity.base_amount != actual_mass.base_amount or
                    existing.source_ref != source or
                    existing.sku_id != "" or
                    (occurred_at is not None and existing.occurred_at != occurred_at)
                ):
                    raise WeightedProcurementConflict("weighted purchase event ID conflicts")
                at = existing.occurred_at
                now = existing.recorded_at
            else:
                event = PurchaseEvent(
                    event_id=event_id, item=plan.item, quantity=actual_mass.as_base(),
                    occurred_at=at, recorded_at=now, sku_id="", source_ref=source,
                )
                history = self.household.history()
                candidate = HouseholdHistory(history.events + (event,))
                project_household_state(candidate, as_of=now)
                depletion_learning_reports(candidate, as_of=now)
                try:
                    self.household.record(event)
                except HouseholdEventRepositoryError as exc:
                    reread = self.household.repository.get(event_id)
                    if reread != event:
                        raise WeightedProcurementConflict("concurrent weighted event conflict") from exc
            receipt = WeightedReceipt(
                plan_id, event_id, actual_mass.as_base(), actual_cost, receipt_ref, at, now
            )
            prior = self.repository.get_receipt(event_id)
            if prior is None:
                try:
                    self.repository.save_receipt(receipt)
                except WeightedProcurementConflict:
                    prior = self.repository.get_receipt(event_id)
            if prior is not None and prior != receipt:
                raise WeightedProcurementConflict("weighted receipt ID conflicts")
            return receipt
