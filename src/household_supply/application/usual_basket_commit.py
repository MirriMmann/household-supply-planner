"""Explicit confirmation of the exact market result inspected by a household.

A preview is ephemeral (no plan or household event). Only confirmation saves a
PlanRecord with immutable market evidence. A confirm request never replans.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import uuid4

from household_supply.domain import Money, MultiObjectivePolicy, Quantity
from household_supply.domain.money import DecimalLike
from household_supply.household import ConsumptionEstimate, HouseholdHistory

from .lifecycle import PlanLifecycleService
from .models import ApplicationPlanResult, RequestedItem
from .persistence import PlanId, PlanRecord
from .usual_basket import (
    UsualBasket,
    UsualBasketPreparationService,
    UsualBasketPreparedSnapshot,
)


MAX_PENDING_USUAL_PREVIEWS = 16
PREVIEW_LIFETIME = timedelta(minutes=20)


class UsualBasketStalePreview(RuntimeError):
    """Preview expired, source evidence changed, or it was not retained."""


def _quantity(value: Quantity) -> dict[str, str]:
    return {"amount": str(value.amount), "unit": value.unit}


def _serialize_decision_basis(
    snapshot: UsualBasketPreparedSnapshot, *,
    preview_id: str, horizon_days: DecimalLike,
) -> dict:
    proposal = snapshot.proposal
    if not proposal.ready or proposal.compilation is None:
        raise ValueError("incomplete routine candidate cannot be committed")

    contributions = tuple(proposal.compilation.contributions)
    recurring = {
        contribution.item.id: contribution
        for contribution in contributions
        if contribution.source_id == "household:recurring"
    }
    estimates: list[dict] = []
    for estimate in snapshot.estimates:
        contribution = recurring.get(estimate.item.id)
        if contribution is None:
            continue
        estimates.append({
            "item_id": estimate.item.id,
            "daily_quantity": _quantity(estimate.daily_quantity),
            "sample_count": estimate.sample_count,
            "observed_days": str(estimate.observed_days),
            "total_depleted": _quantity(estimate.total_depleted),
            "observed_microseconds": estimate.observed_microseconds,
            "daily_min": _quantity(estimate.daily_min),
            "daily_max": _quantity(estimate.daily_max),
            "uncertainty": _quantity(estimate.uncertainty),
            "contribution_quantity": _quantity(contribution.quantity),
        })
    return {
        "kind": "usual_basket",
        "as_of": snapshot.as_of.isoformat(),
        "preview_id": preview_id,
        "horizon_days": str(horizon_days),
        "preferences": [
            {
                "item_id": item.item_id,
                "fallback_quantity": (
                    None if item.fallback_quantity is None
                    else _quantity(item.fallback_quantity)
                ),
            }
            for item in snapshot.basket.items
        ],
        "choices": [
            {"item_id": choice.item_id, "basis": choice.basis}
            for choice in proposal.choices
        ],
        "household_event_ids": [
            event.event_id.value for event in snapshot.history.events
        ],
        "explicit_needs": [
            {"item_id": c.item.id, "quantity": _quantity(c.quantity)}
            for c in contributions
            if c.source_id in {"routine:fallback", "request:override"}
        ],
        "recurring_estimates": estimates,
        "contributions": [
            {
                "source_id": c.source_id,
                "contribution_id": c.contribution_id,
                "item_id": c.item.id,
                "quantity": _quantity(c.quantity),
            }
            for c in contributions
        ],
    }


@dataclass(frozen=True, slots=True)
class UsualBasketStagedPreview:
    preview_id: str | None
    snapshot: UsualBasketPreparedSnapshot
    planning_result: ApplicationPlanResult | None


@dataclass(frozen=True, slots=True)
class _Pending:
    staged_at: datetime
    snapshot: UsualBasketPreparedSnapshot
    result: ApplicationPlanResult
    basis: dict


@dataclass(slots=True)
class UsualBasketPlanCommitService:
    """Small local-stand cache, bounded and protected against duplicate commits.

    File-backed PlanRepository remains the durable source of confirmed plans.
    Unconfirmed previews intentionally disappear when this process restarts.
    """

    preparation: UsualBasketPreparationService
    plans: PlanLifecycleService
    pending: dict[str, _Pending] = field(default_factory=dict, init=False, repr=False)
    lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def preview(
        self,
        *,
        budget: Money,
        horizon_days: DecimalLike,
        overrides: tuple[RequestedItem, ...] = (),
        exclusions: tuple[str, ...] = (),
        objective_policy: MultiObjectivePolicy | None = None,
    ) -> UsualBasketStagedPreview:
        snapshot = self.preparation.prepare_snapshot(
            budget=budget, horizon_days=horizon_days, overrides=overrides,
            exclusions=exclusions, objective_policy=objective_policy,
        )
        if not snapshot.proposal.ready:
            return UsualBasketStagedPreview(None, snapshot, None)
        result = self.plans.planner.plan(snapshot.proposal.application_request)
        # An infeasible plan is explainable, but not a confirmable purchase list.
        if result.plan.status.value != "feasible":
            return UsualBasketStagedPreview(None, snapshot, result)
        now = self.plans.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("plan clock must be timezone-aware")
        with self.lock:
            token = "ub-" + uuid4().hex
            while token in self.pending or self.plans.get(PlanId(token)) is not None:
                token = "ub-" + uuid4().hex
            basis = _serialize_decision_basis(
                snapshot, preview_id=token, horizon_days=horizon_days,
            )
            # Limit the number of market snapshots retained per local process.
            while len(self.pending) >= MAX_PENDING_USUAL_PREVIEWS:
                self.pending.pop(next(iter(self.pending)))
            self.pending[token] = _Pending(now, snapshot, result, basis)
        return UsualBasketStagedPreview(token, snapshot, result)

    def confirm(self, preview_id: str) -> PlanRecord:
        # The PlanId doubles as an idempotency key across process restarts.
        if not isinstance(preview_id, str) or not preview_id.startswith("ub-"):
            raise ValueError("invalid routine preview_id")
        plan_id = PlanId(preview_id)
        with self.lock:
            existing = self.plans.get(plan_id)
            if existing is not None:
                basis = existing.request.to_mapping().get("decision_basis", {})
                if (
                    basis.get("kind") != "usual_basket"
                    or basis.get("preview_id") != preview_id
                ):
                    raise UsualBasketStalePreview("preview identity conflicts with plan")
                return existing
            pending = self.pending.get(preview_id)
            if pending is None:
                raise UsualBasketStalePreview("preview is no longer available")
            now = self.plans.clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("plan clock must be timezone-aware")
            if now < pending.staged_at or now - pending.staged_at > PREVIEW_LIFETIME:
                self.pending.pop(preview_id, None)
                raise UsualBasketStalePreview("preview expired; calculate it again")
            if (
                self.preparation.basket_repository.load() != pending.snapshot.basket
                or self.preparation.household.history() != pending.snapshot.history
            ):
                self.pending.pop(preview_id, None)
                raise UsualBasketStalePreview(
                    "household data or preferences changed; calculate again"
                )
            # Persist EXACTLY the result the user saw; no second market query.
            record = self.plans.commit_result(
                pending.result, plan_id=plan_id,
                decision_basis=pending.basis,
            )
            self.pending.pop(preview_id, None)
            return record

    def discard_previews(self) -> None:
        with self.lock:
            self.pending.clear()
