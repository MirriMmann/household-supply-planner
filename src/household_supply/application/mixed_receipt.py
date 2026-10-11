"""Recoverable actual-receipt reconciliation for a saved mixed PlanRecord.

A receipt is explicitly supplied and attested by the household operator; no
retailer checkout or receipt authenticity is inferred. The durable intent is
claimed atomically *before* individually appended household events. Failure
between events is exposed as pending_recovery, never as complete. Exact replay
repairs partial writes; different receipt evidence cannot steal the plan.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Callable

from household_supply.application.mixed_plan import (
    FileMixedPlanRepository, MixedPlanRecord, MixedPlanConflict,
    MixedPlanStorageError, _m, _parse_m, _parse_q, _q,
)
from household_supply.application.persistence import PlanId
from household_supply.domain import CatalogSnapshot, Item, Money, Quantity
from household_supply.domain._decimal import add_decimals_exact, multiply_decimal_by_int_exact
from household_supply.household import (
    HouseholdEventId, HouseholdEventRepositoryError, HouseholdHistory,
    HouseholdLearningService, PurchaseEvent, depletion_learning_reports,
    project_household_state,
)


class MixedReceiptError(ValueError):
    """Actual receipt is malformed or not authorized by the saved plan."""


class MixedReceiptConflict(MixedReceiptError):
    """Different receipt or household fact already holds the plan/event."""


class MixedReceiptStorageError(RuntimeError):
    """Cannot securely read or write a durable mixed receipt intent."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _time(value: datetime) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise MixedReceiptError("mixed receipt timestamps must be timezone-aware")


def _canon(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate mixed receipt JSON property")
        result[key] = value
    return result


def _strict_read(path: Path) -> dict | None:
    try:
        if path.is_symlink():
            raise MixedReceiptStorageError("receipt file cannot be a symlink")
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > 256_000:
            raise MixedReceiptStorageError("receipt storage file exceeds bounds")
        obj = json.loads(
            path.read_bytes().decode("utf-8"), object_pairs_hook=_strict_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
        if not isinstance(obj, dict) or set(obj) != {"schema_version", "body", "digest"}:
            raise ValueError("mixed receipt envelope mismatch")
        if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
            raise ValueError("unsupported mixed receipt version")
        if obj["digest"] != sha256(_canon({
            "schema_version": 1, "body": obj["body"],
        })).hexdigest():
            raise ValueError("mixed receipt integrity failure")
        return obj["body"]
    except MixedReceiptStorageError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError, OverflowError) as exc:
        raise MixedReceiptStorageError("mixed receipt cannot be safely read") from exc


def _create_once(target: Path, data: dict) -> None:
    unsigned = {"schema_version": 1, "body": data}
    encoded = _canon({
        **unsigned, "digest": sha256(_canon(unsigned)).hexdigest(),
    }) + b"\n"
    if len(encoded) > 256_000:
        raise MixedReceiptStorageError("receipt exceeds bounded size")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb", dir=target.parent, prefix=".mixed-", suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target)
    except FileExistsError as exc:
        raise MixedReceiptConflict("mixed receipt already claimed") from exc
    except OSError as exc:
        raise MixedReceiptStorageError("mixed receipt cannot be persisted") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _event_id(plan: PlanId, item_id: str) -> HouseholdEventId:
    digest = sha256((plan.value + ":" + item_id).encode("utf-8")).hexdigest()[:40]
    return HouseholdEventId("mr-" + digest)


@dataclass(frozen=True, slots=True)
class MixedActualLine:
    item_id: str
    kind: str  # fixed_package or weighted
    sku_id: str
    packs: int | None
    quantity: Quantity
    paid: Money

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise MixedReceiptError("actual receipt needs an Item identity")
        if self.paid.amount < 0 or self.quantity.amount <= 0:
            raise MixedReceiptError("actual receipt line needs positive quantity and nonnegative cost")
        if self.kind == "fixed_package":
            if not isinstance(self.sku_id, str) or not self.sku_id:
                raise MixedReceiptError("packaged receipt needs a SKU")
            if type(self.packs) is not int or self.packs < 1:
                raise MixedReceiptError("packaged receipt count must be positive integer")
        elif self.kind == "weighted":
            if self.sku_id != "" or self.packs is not None or self.quantity.dimension != "mass":
                raise MixedReceiptError("weighted receipt uses mass, not SKU/packs")
        else:
            raise MixedReceiptError("unknown actual receipt line kind")

    def to_mapping(self) -> dict:
        return {
            "item_id": self.item_id, "kind": self.kind,
            "sku_id": self.sku_id, "packs": self.packs,
            "quantity": _q(self.quantity), "paid": _m(self.paid),
        }

    @classmethod
    def from_mapping(cls, value: dict) -> MixedActualLine:
        if not isinstance(value, dict) or set(value) != {
            "item_id", "kind", "sku_id", "packs", "quantity", "paid",
        }:
            raise ValueError("mixed actual line schema mismatch")
        return cls(
            value["item_id"], value["kind"], value["sku_id"],
            value["packs"], _parse_q(value["quantity"]), _parse_m(value["paid"]),
        )


@dataclass(frozen=True, slots=True)
class MixedReceiptIntent:
    plan_id: PlanId
    receipt_ref: str
    occurred_at: datetime
    recorded_at: datetime
    lines: tuple[MixedActualLine, ...]
    delivery_and_fees: Money
    discount: Money
    total_paid: Money
    price_changes_approved: bool

    def __post_init__(self) -> None:
        _time(self.occurred_at)
        _time(self.recorded_at)
        if self.recorded_at < self.occurred_at:
            raise MixedReceiptError("receipt cannot be recorded before purchase")
        if not isinstance(self.receipt_ref, str) or not self.receipt_ref.strip():
            raise MixedReceiptError("external receipt reference required")
        if type(self.price_changes_approved) is not bool:
            raise MixedReceiptError("price approval must be explicit boolean")
        lines = tuple(self.lines)
        keys = [line.item_id for line in lines]
        if not lines or keys != sorted(set(keys)):
            raise MixedReceiptError("receipt must have unique sorted Item lines")
        currency = self.total_paid.currency
        if any(value.currency != currency or value.amount < 0 for value in (
            self.delivery_and_fees, self.discount, self.total_paid,
        )) or any(line.paid.currency != currency for line in lines):
            raise MixedReceiptError("actual receipt currency/amount mismatch")
        merchandise = Money.zero(currency)
        for line in lines:
            merchandise += line.paid
        if self.discount.amount > (merchandise + self.delivery_and_fees).amount:
            raise MixedReceiptError("discount exceeds charged amount")
        if merchandise + self.delivery_and_fees - self.discount != self.total_paid:
            raise MixedReceiptError("receipt total differs from line prices, fees and discount")
        object.__setattr__(self, "lines", lines)

    def to_mapping(self) -> dict:
        return {
            "plan_id": self.plan_id.value, "receipt_ref": self.receipt_ref,
            "occurred_at": self.occurred_at.isoformat(),
            "recorded_at": self.recorded_at.isoformat(),
            "lines": [line.to_mapping() for line in self.lines],
            "delivery_and_fees": _m(self.delivery_and_fees),
            "discount": _m(self.discount),
            "total_paid": _m(self.total_paid),
            "price_changes_approved": self.price_changes_approved,
        }

    def basis(self) -> dict:
        # recorded_at is assigned by this service only, and cannot change
        # the idempotence identity across repeated explicit requests.
        value = self.to_mapping()
        del value["recorded_at"]
        return value

    @classmethod
    def from_mapping(cls, value: dict) -> MixedReceiptIntent:
        if not isinstance(value, dict) or set(value) != {
            "plan_id", "receipt_ref", "occurred_at", "recorded_at", "lines",
            "delivery_and_fees", "discount", "total_paid", "price_changes_approved",
        } or not isinstance(value["lines"], list):
            raise ValueError("mixed receipt schema mismatch")
        return cls(
            PlanId(value["plan_id"]), value["receipt_ref"],
            datetime.fromisoformat(value["occurred_at"]),
            datetime.fromisoformat(value["recorded_at"]),
            tuple(MixedActualLine.from_mapping(line) for line in value["lines"]),
            _parse_m(value["delivery_and_fees"]), _parse_m(value["discount"]),
            _parse_m(value["total_paid"]), value["price_changes_approved"],
        )


class FileMixedReceiptRepository:
    """Plan-keyed create-once receipt intents and durable completion markers."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "intents").mkdir(exist_ok=True)
        (self.root / "completions").mkdir(exist_ok=True)

    def get(self, plan_id: PlanId) -> MixedReceiptIntent | None:
        body = _strict_read(self.root / "intents" / (plan_id.value + ".json"))
        if body is None:
            return None
        try:
            value = MixedReceiptIntent.from_mapping(body)
            if value.plan_id != plan_id:
                raise ValueError("receipt file PlanId mismatch")
            return value
        except (ValueError, TypeError, OverflowError) as exc:
            raise MixedReceiptStorageError("stored mixed receipt intent invalid") from exc

    def claim(self, intent: MixedReceiptIntent) -> MixedReceiptIntent:
        prior = self.get(intent.plan_id)
        if prior is not None:
            if prior.basis() != intent.basis():
                raise MixedReceiptConflict("different receipt already claimed this mixed plan")
            return prior
        try:
            _create_once(
                self.root / "intents" / (intent.plan_id.value + ".json"),
                intent.to_mapping(),
            )
            return intent
        except MixedReceiptConflict:
            prior = self.get(intent.plan_id)
            if prior is None or prior.basis() != intent.basis():
                raise MixedReceiptConflict("different receipt already claimed this mixed plan")
            return prior

    def is_completed(self, intent: MixedReceiptIntent) -> bool:
        marker = _strict_read(
            self.root / "completions" / (intent.plan_id.value + ".json")
        )
        if marker is None:
            return False
        if marker != {
            "plan_id": intent.plan_id.value,
            "basis_digest": sha256(_canon(intent.basis())).hexdigest(),
        }:
            raise MixedReceiptStorageError("mixed completion marker conflicts with receipt")
        return True

    def complete(self, intent: MixedReceiptIntent) -> None:
        if self.is_completed(intent):
            return
        target = self.root / "completions" / (intent.plan_id.value + ".json")
        try:
            _create_once(target, {
                "plan_id": intent.plan_id.value,
                "basis_digest": sha256(_canon(intent.basis())).hexdigest(),
            })
        except MixedReceiptConflict:
            if not self.is_completed(intent):
                raise MixedReceiptConflict("mixed completion marker conflict")


class MixedReceiptService:
    """Explicit receipt confirmation with atomic household batch publication.

    Receipt intent is claimed before the batch commits, and the completion
    marker follows it. If interrupted, retry either commits the whole batch
    or finds the same already committed batch; never publishes a subset.
    """

    def __init__(
        self, *,
        plans: FileMixedPlanRepository,
        receipts: FileMixedReceiptRepository,
        household: HouseholdLearningService,
        catalog: CatalogSnapshot,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self.plans = plans
        self.receipts = receipts
        self.household = household
        self.catalog = catalog
        self.clock = clock
        self._lock = RLock()

    def _plan(self, plan_id: PlanId) -> MixedPlanRecord:
        plan = self.plans.get(plan_id)
        if plan is None:
            raise MixedReceiptConflict("mixed plan has not been explicitly saved")
        return plan

    def _validate(self, plan: MixedPlanRecord, intent: MixedReceiptIntent) -> None:
        if intent.plan_id != plan.plan_id:
            raise MixedReceiptError("actual receipt PlanId mismatch")
        if intent.occurred_at < plan.created_at:
            raise MixedReceiptError("actual receipt must follow approved plan")
        if intent.total_paid.currency != plan.budget.currency:
            raise MixedReceiptError("actual receipt currency differs from plan")
        if intent.total_paid.amount > plan.budget.amount:
            raise MixedReceiptConflict("actual paid total exceeds approved budget")

        packages: dict[str, object] = {}
        for line in plan.packages:
            if line.item_id in packages:
                raise MixedReceiptError("duplicate packaged Item in mixed plan")
            packages[line.item_id] = line
        weights = {line.item_id: line for line in plan.weights}
        expected = set(packages) | set(weights)
        if set(line.item_id for line in intent.lines) != expected:
            raise MixedReceiptError("receipt must account for every planned Item exactly once")
        sku_map = {sku.id: sku for sku in self.catalog.skus}
        for line in intent.lines:
            if line.item_id in packages:
                package = packages[line.item_id]
                sku = sku_map.get(package.sku_id)
                if sku is None or sku.item.id != line.item_id:
                    raise MixedReceiptError("receipt packaged SKU no longer exists in catalog")
                if sku.package_quantity != package.package:
                    raise MixedReceiptError("receipt packaged SKU size changed")
                if line.kind != "fixed_package" or line.sku_id != package.sku_id:
                    raise MixedReceiptError("receipt packaged Item/SKU mismatch")
                if line.packs > package.packs:
                    raise MixedReceiptConflict("actual received packs exceed approved packs")
                target = Quantity(
                    multiply_decimal_by_int_exact(package.package.amount, line.packs),
                    package.package.unit,
                )
                if not line.quantity.compatible_with(target) or line.quantity.base_amount != target.base_amount:
                    raise MixedReceiptError("actual packaged mass does not match pack count")
                max_cost = package.cost
            else:
                weight = weights[line.item_id].record
                if line.kind != "weighted":
                    raise MixedReceiptError("weighted Item may not be represented by packs")
                if not line.quantity.compatible_with(weight.quote.sellable) or (
                    line.quantity.base_amount > weight.quote.sellable.base_amount
                ):
                    raise MixedReceiptConflict("actual received mass exceeds approved seller quantity")
                max_cost = weight.quote.estimated_upper_bound
            if line.paid.amount > max_cost.amount and not intent.price_changes_approved:
                raise MixedReceiptConflict("receipt price increased without explicit approval")

        if intent.delivery_and_fees.amount > 0 and not intent.price_changes_approved:
            raise MixedReceiptConflict("delivery/fees need explicit approval")
        if intent.total_paid.amount < 0:
            raise MixedReceiptError("negative paid receipt")

    def _events(self, plan: MixedPlanRecord, intent: MixedReceiptIntent) -> tuple[PurchaseEvent, ...]:
        package_items = {sku.id: sku.item for sku in self.catalog.skus}
        weighted_items = {line.item_id: line.record.item for line in plan.weights}
        fingerprint = sha256(_canon(intent.basis())).hexdigest()
        result = []
        for line in intent.lines:
            item: Item = (
                package_items[line.sku_id] if line.kind == "fixed_package"
                else weighted_items[line.item_id]
            )
            result.append(PurchaseEvent(
                event_id=_event_id(intent.plan_id, line.item_id),
                item=item,
                quantity=line.quantity.as_base(),
                occurred_at=intent.occurred_at,
                recorded_at=intent.recorded_at,
                sku_id=line.sku_id,
                source_ref="mixed-receipt:" + intent.plan_id.value + ":" + fingerprint,
            ))
        return tuple(result)

    def status(self, plan_id: PlanId) -> dict:
        plan = self._plan(plan_id)
        receipt = self.receipts.get(plan.plan_id)
        if receipt is None:
            return {
                "plan_id": plan_id.value, "state": "not_received",
                "receipt": None, "credited_items": [],
            }
        self._validate(plan, receipt)
        events = self._events(plan, receipt)
        credited = []
        for event in events:
            prior = self.household.repository.get(event.event_id)
            if prior is not None:
                if prior != event:
                    raise MixedReceiptConflict("household event contradicts mixed receipt")
                credited.append(event.item.id)
        completed = self.receipts.is_completed(receipt)
        if completed and len(credited) != len(events):
            raise MixedReceiptStorageError("completed receipt is missing household events")

        actual_by_item = {line.item_id: line for line in receipt.lines}
        fulfillment = []
        for demand in plan.demands:
            received = actual_by_item[demand.item_id].quantity
            required = demand.missing
            if not received.compatible_with(required):
                raise MixedReceiptStorageError("receipt demand units diverged")
            shortfall = max(
                required.base_amount - received.base_amount, 0
            )
            fulfillment.append({
                "item_id": demand.item_id,
                "required_to_buy": _q(required.as_base()),
                "actual_received": _q(received.as_base()),
                "still_missing_after_receipt": _q(
                    Quantity(shortfall, required.base_unit)
                ),
                "fulfilled": shortfall == 0,
                "posted_to_inventory": demand.item_id in credited,
            })
        return {
            "plan_id": plan_id.value,
            "state": "completed" if completed else "pending_recovery",
            "receipt": receipt.to_mapping(),
            "credited_items": credited,
            "requires_retry": not completed,
            "all_demands_fulfilled": all(x["fulfilled"] for x in fulfillment),
            "fulfillment": fulfillment,
            "budget_remaining_after_paid": _m(plan.budget - receipt.total_paid),
            "estimated_merchandise_total": _m(plan.estimated_total),
        }

    def confirm(
        self, *,
        plan_id: PlanId,
        receipt_ref: str,
        occurred_at: datetime,
        lines: tuple[MixedActualLine, ...],
        delivery_and_fees: Money,
        discount: Money,
        total_paid: Money,
        price_changes_approved: bool,
    ) -> dict:
        with self._lock:
            plan = self._plan(plan_id)
            at = self.clock()
            _time(at)
            _time(occurred_at)
            if occurred_at > at:
                raise MixedReceiptError("receipt purchase time cannot be in the future")
            # Exact equivalence: 0.29kg == 290g and 14.50 KGS ==
            # 14.5 KGS must replay the same immutable receipt identity.
            def normalized_money(value: Money) -> Money:
                return Money(format(value.amount.normalize(), "f"), value.currency)

            normalized_lines = tuple(
                MixedActualLine(
                    item_id=line.item_id, kind=line.kind, sku_id=line.sku_id,
                    packs=line.packs,
                    quantity=Quantity(
                        format(line.quantity.base_amount.normalize(), "f"),
                        line.quantity.base_unit,
                    ),
                    paid=normalized_money(line.paid),
                )
                for line in lines
            )
            new = MixedReceiptIntent(
                plan_id, receipt_ref.strip(), occurred_at, at,
                tuple(sorted(normalized_lines, key=lambda line: line.item_id)),
                normalized_money(delivery_and_fees), normalized_money(discount),
                normalized_money(total_paid), price_changes_approved,
            )
            self._validate(plan, new)
            receipt = self.receipts.claim(new)
            self._validate(plan, receipt)
            events = self._events(plan, receipt)

            # Preflight the entire batch *before* appending any event, using
            # the same projection/depletion checks as the normal household API.
            old = self.household.history()
            expected = {event.event_id: event for event in events}
            for recorded in old.events:
                matching = expected.get(recorded.event_id)
                if matching is not None and matching != recorded:
                    raise MixedReceiptConflict("household event ID already contains another fact")
            pending = tuple(event for event in events if self.household.repository.get(event.event_id) is None)
            if pending:
                candidate = HouseholdHistory(old.events + pending)
                project_household_state(candidate, as_of=at)
                depletion_learning_reports(candidate, as_of=at)

            # SQLite batch publishes *all* events under one commit. A crash
            # before commit exposes zero new Items; after commit exposes all.
            # The receipt intent/completion marker are separate and retriable.
            try:
                self.household.repository.append_batch(
                    "batch-" + plan_id.value,
                    tuple(sorted(events, key=lambda event: event.event_id.value)),
                )
            except HouseholdEventRepositoryError as exc:
                # A pre-M12.17 interrupted receipt might have already
                # published single-event JSON files. Keep the original
                # recoverable migration path; NEW receipts never use it.
                legacy = getattr(self.household.repository, "root", None)
                if legacy is None or not any(
                    (legacy / (event.event_id.value + ".json")).is_file()
                    for event in events
                ):
                    raise MixedReceiptStorageError(
                        "atomic household batch not committed; exact receipt retry required"
                    ) from exc
                for event in events:
                    existing = self.household.repository.get(event.event_id)
                    if existing is not None:
                        if existing != event:
                            raise MixedReceiptConflict(
                                "legacy event contradicts receipt"
                            )
                        continue
                    self.household.record(event)
            self.receipts.complete(receipt)
            return self.status(plan_id)
