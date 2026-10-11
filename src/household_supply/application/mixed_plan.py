"""One atomic *plan* for package counts and separately verified sellable mass.

Opt-in mixed planning is deliberately not a Globus checkout. A weight-listed
reference price alone is insufficient authority to create this plan.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Callable
from uuid import uuid4

from household_supply.application.models import ApplicationPlanRequest, RequestedItem
from household_supply.application.persistence import PlanId
from household_supply.application.service import ApplicationPlanner
from household_supply.application.shopping_readiness import (
    ShoppingDemand, ShoppingReadinessService,
)
from household_supply.application.weighted_lifecycle import WeightedPlanRecord
from household_supply.domain import Item, Money, Quantity
from household_supply.domain._decimal import (
    add_decimals_exact, multiply_decimal_by_int_exact,
)
from household_supply.domain.weighted import (
    VerifiedWeightSaleRule, quote_verified_mass,
)
from household_supply.market.catalogs.globus_weight_candidates import WEIGHT_CANDIDATES
from household_supply.market.providers.globus_weight import GlobusWeightPriceService


class MixedPlanError(ValueError):
    """One or more requirements cannot be fully and safely planned."""


class MixedPlanConflict(MixedPlanError):
    """Authorization expired or household facts changed after preview."""


class MixedPlanStorageError(RuntimeError):
    """Immutable plan integrity, filesystem or identity failure."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(when: datetime) -> None:
    if not isinstance(when, datetime) or when.tzinfo is None or when.utcoffset() is None:
        raise MixedPlanError("mixed plan timestamp must be timezone aware")


def _q(value: Quantity) -> dict[str, str]:
    return {"amount": str(value.amount), "unit": value.unit}


def _parse_q(value: dict) -> Quantity:
    if not isinstance(value, dict) or set(value) != {"amount", "unit"}:
        raise ValueError("invalid mixed plan Quantity schema")
    if not all(isinstance(x, str) for x in value.values()):
        raise ValueError("mixed Quantity fields must be strings")
    return Quantity(value["amount"], value["unit"])


def _m(value: Money) -> dict[str, str]:
    return {"amount": str(value.amount), "currency": value.currency}


def _parse_m(value: dict) -> Money:
    if not isinstance(value, dict) or set(value) != {"amount", "currency"}:
        raise ValueError("invalid mixed plan Money schema")
    if not all(isinstance(x, str) for x in value.values()):
        raise ValueError("mixed Money fields must be strings")
    return Money(value["amount"], value["currency"])


@dataclass(frozen=True, slots=True)
class MixedPackLine:
    item_id: str
    sku_id: str
    offer_id: str
    seller_id: str
    source: str
    observed_at: datetime
    package: Quantity
    packs: int
    unit_price: Money
    cost: Money
    acquired: Quantity

    def __post_init__(self) -> None:
        if any(not isinstance(x, str) or not x.strip() for x in (
            self.item_id, self.sku_id, self.offer_id, self.seller_id, self.source
        )):
            raise MixedPlanError("packaged purchase missing source/identity")
        _aware(self.observed_at)
        if type(self.packs) is not int or not 1 <= self.packs <= 100_000:
            raise MixedPlanError("mixed packaged packs must be a positive integer")
        if self.package.amount <= 0 or self.unit_price.amount < 0:
            raise MixedPlanError("invalid package quantity or price")
        if self.cost != self.unit_price * self.packs:
            raise MixedPlanError("packaged cost mismatch")
        expected = Quantity(
            multiply_decimal_by_int_exact(self.package.amount, self.packs),
            self.package.unit,
        )
        # The existing M3 planner normalizes acquired quantity to a base
        # unit (e.g. a 1kg SKU yields 1000g). Preserve that semantics.
        if not self.acquired.compatible_with(expected) or (
            self.acquired.base_amount != expected.base_amount
        ):
            raise MixedPlanError("packaged purchased quantity mismatch")

    def to_mapping(self) -> dict:
        return {
            "item_id": self.item_id, "sku_id": self.sku_id,
            "offer_id": self.offer_id, "seller_id": self.seller_id,
            "source": self.source, "observed_at": self.observed_at.isoformat(),
            "package": _q(self.package), "packs": self.packs,
            "unit_price": _m(self.unit_price), "cost": _m(self.cost),
            "acquired": _q(self.acquired),
        }

    @classmethod
    def from_mapping(cls, value: dict) -> MixedPackLine:
        if not isinstance(value, dict) or set(value) != {
            "item_id", "sku_id", "offer_id", "seller_id", "source",
            "observed_at", "package", "packs", "unit_price", "cost", "acquired",
        }:
            raise ValueError("mixed packaged line schema mismatch")
        return cls(
            item_id=value["item_id"], sku_id=value["sku_id"],
            offer_id=value["offer_id"], seller_id=value["seller_id"],
            source=value["source"], observed_at=datetime.fromisoformat(value["observed_at"]),
            package=_parse_q(value["package"]), packs=value["packs"],
            unit_price=_parse_m(value["unit_price"]), cost=_parse_m(value["cost"]),
            acquired=_parse_q(value["acquired"]),
        )


@dataclass(frozen=True, slots=True)
class MixedWeightLine:
    """Reuses typed M12.13 snapshot validation including min/step source evidence."""
    record: WeightedPlanRecord

    @property
    def item_id(self) -> str:
        return self.record.item.id

    @property
    def cost(self) -> Money:
        return self.record.quote.estimated_upper_bound

    def to_mapping(self) -> dict:
        return self.record.to_mapping()

    @classmethod
    def from_mapping(cls, value: dict) -> MixedWeightLine:
        return cls(WeightedPlanRecord.from_mapping(value))


@dataclass(frozen=True, slots=True)
class MixedDemandLine:
    item_id: str
    required: Quantity
    on_hand: Quantity
    missing: Quantity

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise MixedPlanError("mixed demand needs a canonical Item identity")
        if self.required.amount <= 0 or self.on_hand.amount < 0 or self.missing.amount <= 0:
            raise MixedPlanError("mixed line requires a positive proven shortage")
        if not (
            self.required.compatible_with(self.on_hand)
            and self.required.compatible_with(self.missing)
        ):
            raise MixedPlanError("mixed demand uses incompatible stock units")
        if self.missing.base_amount != (
            self.required.base_amount - min(self.on_hand.base_amount, self.required.base_amount)
        ):
            raise MixedPlanError("mixed deficit conflicts with inventory observation")

    def to_mapping(self) -> dict:
        return {
            "item_id": self.item_id, "required": _q(self.required),
            "on_hand": _q(self.on_hand), "missing": _q(self.missing),
        }

    @classmethod
    def from_mapping(cls, value: dict) -> MixedDemandLine:
        if not isinstance(value, dict) or set(value) != {
            "item_id", "required", "on_hand", "missing",
        }:
            raise ValueError("mixed demand line schema mismatch")
        return cls(
            value["item_id"], _parse_q(value["required"]),
            _parse_q(value["on_hand"]), _parse_q(value["missing"]),
        )


@dataclass(frozen=True, slots=True)
class MixedPlanRecord:
    """One complete, budget-checked package+weight authorization snapshot.

    This is distinct from the legacy PlanRecord (packs-only) and is NOT a
    seller's confirmed checkout or an inventory purchase event.
    """
    plan_id: PlanId
    created_at: datetime
    budget: Money
    source: str
    household_event_ids: tuple[str, ...]
    demands: tuple[MixedDemandLine, ...]
    packages: tuple[MixedPackLine, ...]
    weights: tuple[MixedWeightLine, ...]
    estimated_total: Money

    def __post_init__(self) -> None:
        _aware(self.created_at)
        if self.source not in {"recipe", "manual"}:
            raise MixedPlanError("unsupported mixed plan demand source")
        if self.budget.amount < 0 or self.estimated_total.amount < 0:
            raise MixedPlanError("invalid mixed plan budget or estimated cost")
        if self.budget.currency != self.estimated_total.currency:
            raise MixedPlanError("mixed plan currency mismatch")
        if len(self.household_event_ids) != len(set(self.household_event_ids)):
            raise MixedPlanError("duplicate household event IDs")
        demand_ids = [d.item_id for d in self.demands]
        if not self.demands or demand_ids != sorted(set(demand_ids)):
            raise MixedPlanError("mixed demand lines must be nonempty, unique and sorted")
        if not self.packages and not self.weights:
            raise MixedPlanError("cannot save an empty mixed purchase plan")
        if any(line.observed_at > self.created_at for line in self.packages):
            raise MixedPlanError("future-dated packaged offer")
        if any(line.record.created_at != self.created_at for line in self.weights):
            raise MixedPlanError("weighted sub-line snapshot time conflict")
        selected_ids = [p.item_id for p in self.packages] + [w.item_id for w in self.weights]
        if set(selected_ids) != set(demand_ids):
            raise MixedPlanError("mixed plan drops or adds a demanded Item")
        if len(selected_ids) != len(set(selected_ids)) and any(
            selected_ids.count(item_id) != sum(p.item_id == item_id for p in self.packages)
            for item_id in set(selected_ids)
        ):
            raise MixedPlanError("mixed plan Item overlaps fixed and weighted channels")
        amounts: dict[str, Decimal] = {}
        for p in self.packages:
            amounts[p.item_id] = add_decimals_exact(
                amounts.get(p.item_id, Decimal(0)), p.acquired.as_base().base_amount
            )
        for w in self.weights:
            amounts[w.item_id] = add_decimals_exact(
                amounts.get(w.item_id, Decimal(0)), w.record.quote.sellable.base_amount
            )
        for demand in self.demands:
            if amounts.get(demand.item_id, Decimal(0)) < demand.missing.base_amount:
                raise MixedPlanError(f"mixed plan under-covers Item: {demand.item_id}")
        total = Money.zero(self.budget.currency)
        for package in self.packages:
            total += package.cost
        for weighted in self.weights:
            total += weighted.cost
        if total != self.estimated_total or total.amount > self.budget.amount:
            raise MixedPlanError("mixed plan estimated total mismatches budget/lines")

    def to_mapping(self) -> dict:
        return {
            "plan_id": self.plan_id.value,
            "created_at": self.created_at.isoformat(),
            "budget": _m(self.budget),
            "source": self.source,
            "household_event_ids": list(self.household_event_ids),
            "demands": [d.to_mapping() for d in self.demands],
            "packages": [p.to_mapping() for p in self.packages],
            "weights": [w.to_mapping() for w in self.weights],
            "estimated_total": _m(self.estimated_total),
            "status": "saved_plan_not_checkout",
        }

    @classmethod
    def from_mapping(cls, value: dict) -> MixedPlanRecord:
        if not isinstance(value, dict) or set(value) != {
            "plan_id", "created_at", "budget", "source",
            "household_event_ids", "demands", "packages", "weights",
            "estimated_total", "status",
        } or value["status"] != "saved_plan_not_checkout":
            raise ValueError("mixed plan storage schema mismatch")
        if not isinstance(value["household_event_ids"], list) or not all(
            isinstance(x, str) for x in value["household_event_ids"]
        ):
            raise ValueError("mixed household event IDs invalid")
        if any(not isinstance(value[k], list) for k in ("demands", "packages", "weights")):
            raise ValueError("mixed plan line arrays invalid")
        return cls(
            plan_id=PlanId(value["plan_id"]),
            created_at=datetime.fromisoformat(value["created_at"]),
            budget=_parse_m(value["budget"]), source=value["source"],
            household_event_ids=tuple(value["household_event_ids"]),
            demands=tuple(MixedDemandLine.from_mapping(x) for x in value["demands"]),
            packages=tuple(MixedPackLine.from_mapping(x) for x in value["packages"]),
            weights=tuple(MixedWeightLine.from_mapping(x) for x in value["weights"]),
            estimated_total=_parse_m(value["estimated_total"]),
        )


class FileMixedPlanRepository:
    """Atomically link immutable, integrity-checked typed plan snapshots."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, record: MixedPlanRecord) -> None:
        unsigned = {"schema_version": 1, "plan": record.to_mapping()}
        source = json.dumps(unsigned, sort_keys=True, separators=(",", ":"),
                            ensure_ascii=False, allow_nan=False).encode("utf-8")
        full = dict(unsigned, digest=sha256(source).hexdigest())
        encoded = (json.dumps(full, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > 256_000:
            raise MixedPlanStorageError("mixed plan exceeds file bound")
        target = self.root / f"{record.plan_id.value}.json"
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb", prefix=f".{record.plan_id.value}.",
                suffix=".tmp", dir=self.root, delete=False
            ) as handle:
                temporary = Path(handle.name)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.link(temporary, target)
        except FileExistsError as exc:
            raise MixedPlanConflict("mixed plan already saved") from exc
        except OSError as exc:
            raise MixedPlanStorageError("mixed plan storage failed") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def get(self, plan_id: PlanId) -> MixedPlanRecord | None:
        path = self.root / f"{plan_id.value}.json"
        try:
            if path.is_symlink():
                raise MixedPlanStorageError("mixed plan file must not be symlink")
            if not path.exists():
                return None
            if not path.is_file() or path.stat().st_size > 256_000:
                raise MixedPlanStorageError("mixed plan exceeds file size / type boundary")
            def strict_pairs(pairs):
                out = {}
                for key, val in pairs:
                    if key in out:
                        raise ValueError("duplicate JSON property")
                    out[key] = val
                return out
            payload = json.loads(
                path.read_text(encoding="utf-8"), object_pairs_hook=strict_pairs,
                parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)),
            )
            if not isinstance(payload, dict) or set(payload) != {
                "schema_version", "plan", "digest",
            } or type(payload["schema_version"]) is not int or payload["schema_version"] != 1:
                raise ValueError("invalid mixed plan envelope")
            signed = {"schema_version": payload["schema_version"], "plan": payload["plan"]}
            canonical = json.dumps(signed, sort_keys=True, separators=(",", ":"),
                                   ensure_ascii=False, allow_nan=False).encode("utf-8")
            if sha256(canonical).hexdigest() != payload["digest"]:
                raise ValueError("mixed plan integrity check failed")
            record = MixedPlanRecord.from_mapping(payload["plan"])
            if record.plan_id != plan_id:
                raise ValueError("mixed plan ID differs from path")
            return record
        except MixedPlanStorageError:
            raise
        except (OSError, TypeError, ValueError, OverflowError, UnicodeError) as exc:
            raise MixedPlanStorageError(f"mixed plan data invalid: {exc}") from exc


@dataclass(frozen=True, slots=True)
class MixedPlanPreview:
    record: MixedPlanRecord
    expires_at: datetime


class MixedPlanService:
    """Fail-closed mixed plan authority, disabled absent trusted seller rules."""

    def __init__(
        self, *,
        shopping: ShoppingReadinessService,
        package_planner: ApplicationPlanner,
        prices: GlobusWeightPriceService,
        sale_rules: Callable[[str, str], VerifiedWeightSaleRule | None],
        repository: FileMixedPlanRepository,
        clock: Callable[[], datetime] = _now,
        max_age: timedelta = timedelta(minutes=20),
        max_rule_age: timedelta = timedelta(days=1),
    ) -> None:
        if shopping.catalog != package_planner.catalog:
            raise MixedPlanError("mixed plan catalog differs from package planner")
        if max_age <= timedelta(0) or max_rule_age <= timedelta(0):
            raise MixedPlanError("mixed plan evidence TTL must be positive")
        self.shopping = shopping
        self.package_planner = package_planner
        self.prices = prices
        self.sale_rules = sale_rules
        self.repository = repository
        self.clock = clock
        self.max_age = max_age
        self.max_rule_age = max_rule_age
        self._lock = RLock()
        self._previews: dict[PlanId, MixedPlanPreview] = {}

    def _now(self) -> datetime:
        at = self.clock()
        _aware(at)
        return at

    def preview(
        self, *,
        budget: Money,
        demands: tuple[ShoppingDemand, ...] | None = None,
        recipe_id: str | None = None,
    ) -> MixedPlanPreview:
        if budget.amount < 0:
            raise MixedPlanError("negative budget")
        start = self.shopping.household.history()
        basis = tuple(event.event_id.value for event in start.events)
        report = self.shopping.preview(demands=demands, recipe_id=recipe_id)
        if any(line["stock_status"] in {"unverified", "unit_conflict"} for line in report["lines"]):
            raise MixedPlanError("inventory_unknown_or_incompatible")
        if any(line["kind"] not in {"fixed_package", "weighted_reference"} for line in report["lines"]):
            raise MixedPlanError("unknown_item_or_quantity_unit")
        short = [line for line in report["lines"] if line["stock_status"] == "short"]
        if not short:
            raise MixedPlanError("all requested Items are already on hand")

        created_at = self._now()
        weighted = []
        for line in short:
            if line["kind"] != "weighted_reference":
                continue
            missing = _parse_q(line["missing"])
            estimate = self.prices.estimate(item_id=line["item_id"], requested=missing)
            observed = estimate.observation
            rule = self.sale_rules(line["item_id"], observed.source_ref)
            if rule is None:
                raise MixedPlanError("no seller-verified weight minimum/step")
            if not isinstance(rule, VerifiedWeightSaleRule):
                raise MixedPlanError("invalid weighted sale rule authority")
            # Acquisition just happened: the observation timestamp can be
            # milliseconds later than the initial preview clock.
            created_at = self._now()
            if not (
                created_at - self.max_age <= observed.observed_at <= created_at
                and created_at - self.max_rule_age <= rule.verified_at <= observed.observed_at
            ):
                raise MixedPlanConflict("seller rule or price evidence stale")
            candidate = next(
                (row for row in WEIGHT_CANDIDATES if row.item_id == line["item_id"]), None
            )
            if candidate is None or candidate.source_url != observed.source_ref:
                raise MixedPlanError("weighted source identity is not approved")
            quote = quote_verified_mass(observed, rule, missing)
            if quote.estimated_upper_bound.currency != budget.currency:
                raise MixedPlanError("weighted quote budget currency mismatch")
            if quote.estimated_upper_bound.amount > budget.amount:
                raise MixedPlanError("weighted purchases alone exceed hard budget")
            weighted.append(MixedWeightLine(WeightedPlanRecord(
                plan_id=PlanId("mw-" + uuid4().hex),
                item=Item(line["item_id"], candidate.item_name, "weighted_grocery"),
                quote=quote, budget=budget, created_at=created_at,
            )))
        # One normalized capture time after all source observations.
        created_at = self._now()
        weighted = [
            MixedWeightLine(replace(x.record, created_at=created_at))
            for x in weighted
        ]
        for entry in weighted:
            if not created_at - self.max_age <= entry.record.quote.observation.observed_at <= created_at:
                raise MixedPlanConflict("weighted price stale after mixed acquisition")
        weighted_cost = Money.zero(budget.currency)
        for entry in weighted:
            weighted_cost += entry.cost
        if weighted_cost.amount > budget.amount:
            raise MixedPlanError("weighted purchases alone exceed hard budget")

        packaged_needs = tuple(
            RequestedItem(line["item_id"], _parse_q(line["missing"]))
            for line in short if line["kind"] == "fixed_package"
        )
        packages = []
        if packaged_needs:
            remainder = budget - weighted_cost
            planned = self.package_planner.plan(
                ApplicationPlanRequest(demands=packaged_needs, budget=remainder)
            )
            if planned.plan.status.value != "feasible":
                raise MixedPlanError("packaged part cannot cover every demand within remaining budget")
            # Package market acquisition may have occurred after the weight
            # request; capture the final atomic snapshot once both are known.
            created_at = self._now()
            weighted = [
                MixedWeightLine(replace(x.record, created_at=created_at))
                for x in weighted
            ]
            for entry in weighted:
                if not created_at - self.max_age <= entry.record.quote.observation.observed_at <= created_at:
                    raise MixedPlanConflict("weighted price became stale while acquiring packaged offers")
            for purchase in planned.plan.purchases:
                offer = purchase.offer
                if not created_at - self.max_age <= offer.observed_at <= created_at:
                    raise MixedPlanConflict("packaged offer evidence stale")
                packages.append(MixedPackLine(
                    item_id=offer.sku.item.id, sku_id=offer.sku.id,
                    offer_id=offer.id, seller_id=offer.seller_id,
                    source=(
                        offer.provenance.source_ref if offer.provenance is not None
                        else offer.source
                    ),
                    observed_at=offer.observed_at, package=offer.sku.package_quantity,
                    packs=purchase.packs, unit_price=offer.price, cost=purchase.cost,
                    acquired=purchase.acquired_quantity,
                ))

        if tuple(event.event_id.value for event in self.shopping.household.history().events) != basis:
            raise MixedPlanConflict("household facts changed during mixed market acquisition")
        demands_snapshot = tuple(
            MixedDemandLine(
                item_id=line["item_id"],
                required=_parse_q(line["required"]),
                on_hand=_parse_q(line["recorded_on_hand"]),
                missing=_parse_q(line["missing"]),
            )
            for line in short
        )
        total = weighted_cost
        for package in packages:
            total += package.cost
        record = MixedPlanRecord(
            plan_id=PlanId("mp-" + uuid4().hex), created_at=created_at,
            budget=budget, source=report["source"],
            household_event_ids=basis, demands=demands_snapshot,
            packages=tuple(packages), weights=tuple(weighted),
            estimated_total=total,
        )
        preview = MixedPlanPreview(record, created_at + self.max_age)
        with self._lock:
            now = self._now()
            self._previews = {
                key: value for key, value in self._previews.items()
                if value.expires_at > now
            }
            if len(self._previews) >= 16:
                raise MixedPlanConflict("too many pending mixed plans")
            self._previews[record.plan_id] = preview
        return preview

    def confirm(self, plan_id: PlanId) -> MixedPlanRecord:
        with self._lock:
            existing = self.repository.get(plan_id)
            if existing is not None:
                return existing
            preview = self._previews.get(plan_id)
            if preview is None:
                raise MixedPlanConflict("no pending mixed plan preview")
            now = self._now()
            if now > preview.expires_at:
                del self._previews[plan_id]
                raise MixedPlanConflict("mixed plan preview expired")
            # Preview validity is not sufficient on its own: a seller rule
            # could expire before the preview TTL. Source authority remains
            # time-bound until the explicit saved-plan decision.
            if any(not now - self.max_age <= p.observed_at <= now
                   for p in preview.record.packages):
                raise MixedPlanConflict("packaged price expired before mixed save")
            if any(
                not (
                    now - self.max_age <= w.record.quote.observation.observed_at <= now
                    and now - self.max_rule_age <= w.record.quote.rule.verified_at <= now
                )
                for w in preview.record.weights
            ):
                raise MixedPlanConflict("weighted evidence expired before mixed save")
            current = tuple(
                event.event_id.value for event in self.shopping.household.history().events
            )
            if current != preview.record.household_event_ids:
                raise MixedPlanConflict("household state changed since mixed preview")
            self.repository.save(preview.record)
            del self._previews[plan_id]
            return preview.record
