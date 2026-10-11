"""Explicitly save the exact recipe shortage plan shown to the user.

Recipe discovery and quotation are read-only. A bounded server-side preview
holds the planner result until a separate confirmation authorizes PlanRecord
persistence; neither operation creates a household purchase event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from uuid import uuid4

from household_supply.domain import Money, Quantity
from household_supply.household import HouseholdHistory, HouseholdLearningService

from .lifecycle import PlanLifecycleService
from .models import ApplicationPlanResult
from .persistence import PlanId, PlanRecord
from .recipe_assessment import RecipeAssessment, assess_recipe, recipe_missing_request
from .recipe_catalog import CuratedRecipe


RECIPE_PREVIEW_LIFETIME = timedelta(minutes=20)
MAX_PENDING_RECIPE_PREVIEWS = 16


class RecipeStalePreview(RuntimeError):
    """The previously inspected recipe plan can no longer be confirmed."""


def _quantity(value: Quantity) -> dict[str, str]:
    return {"amount": format(value.amount.normalize(), "f"), "unit": value.unit}


def _decision_basis(
    assessment: RecipeAssessment, history: HouseholdHistory, *,
    preview_id: str, as_of: datetime,
) -> dict:
    recipe = assessment.recipe.recipe
    return {
        "kind": "recipe",
        "as_of": as_of.isoformat(),
        "preview_id": preview_id,
        "recipe_id": recipe.id,
        "recipe_name": recipe.name,
        "servings": str(recipe.servings),
        "household_event_ids": [event.event_id.value for event in history.events],
        "ingredients": [
            {
                "item_id": ingredient.item_id,
                "required": _quantity(ingredient.required),
                "recorded_on_hand": _quantity(ingredient.recorded_on_hand),
                "missing": _quantity(ingredient.missing),
            }
            for ingredient in assessment.ingredients
        ],
        "explicit_needs": [
            {"item_id": item.item_id, "quantity": _quantity(item.quantity)}
            for item in assessment.missing_demands
        ],
        "contributions": [
            {
                "source_id": f"recipe:{recipe.id}",
                "contribution_id": f"ingredient:{item.item_id}",
                "item_id": item.item_id,
                "quantity": _quantity(item.quantity),
            }
            for item in assessment.missing_demands
        ],
    }


@dataclass(frozen=True, slots=True)
class RecipeStagedPreview:
    assessment: RecipeAssessment
    result: ApplicationPlanResult | None
    preview_id: str | None


@dataclass(frozen=True, slots=True)
class _PendingRecipe:
    staged_at: datetime
    recipe_id: str
    history: HouseholdHistory
    result: ApplicationPlanResult
    basis: dict


@dataclass(slots=True)
class RecipePlanCommitService:
    household: HouseholdLearningService
    plans: PlanLifecycleService
    pending: dict[str, _PendingRecipe] = field(default_factory=dict, init=False, repr=False)
    lock: RLock = field(default_factory=RLock, init=False, repr=False)

    def preview(self, recipe: CuratedRecipe, *, budget: Money) -> RecipeStagedPreview:
        as_of = self.plans.clock()
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("recipe preview clock must be timezone-aware")
        history = self.household.history()
        assessment = assess_recipe(recipe, household=self.household.state(as_of=as_of))
        if history != self.household.history():
            raise RecipeStalePreview("household inventory changed during recipe assessment")
        request = recipe_missing_request(assessment, budget=budget)
        if request is None:
            return RecipeStagedPreview(assessment, None, None)
        result = self.plans.planner.plan(request)
        if history != self.household.history():
            raise RecipeStalePreview("household inventory changed during market quotation")
        if result.plan.status.value != "feasible":
            return RecipeStagedPreview(assessment, result, None)
        with self.lock:
            token = "rc-" + uuid4().hex
            while token in self.pending or self.plans.get(PlanId(token)) is not None:
                token = "rc-" + uuid4().hex
            while len(self.pending) >= MAX_PENDING_RECIPE_PREVIEWS:
                self.pending.pop(next(iter(self.pending)))
            self.pending[token] = _PendingRecipe(
                as_of, recipe.recipe.id, history, result,
                _decision_basis(assessment, history, preview_id=token, as_of=as_of),
            )
        return RecipeStagedPreview(assessment, result, token)

    def confirm(self, *, recipe_id: str, preview_id: str) -> PlanRecord:
        if not isinstance(preview_id, str) or not preview_id.startswith("rc-"):
            raise ValueError("invalid recipe preview_id")
        plan_id = PlanId(preview_id)
        with self.lock:
            existing = self.plans.get(plan_id)
            if existing is not None:
                basis = existing.request.to_mapping().get("decision_basis", {})
                if (
                    basis.get("kind") != "recipe"
                    or basis.get("preview_id") != preview_id
                    or basis.get("recipe_id") != recipe_id
                ):
                    raise RecipeStalePreview("recipe preview identity conflicts with stored plan")
                return existing

            pending = self.pending.get(preview_id)
            if pending is None or pending.recipe_id != recipe_id:
                raise RecipeStalePreview("recipe preview is no longer available")
            now = self.plans.clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("recipe confirmation clock must be timezone-aware")
            if now < pending.staged_at or now - pending.staged_at > RECIPE_PREVIEW_LIFETIME:
                self.pending.pop(preview_id, None)
                raise RecipeStalePreview("recipe preview expired; calculate it again")
            if self.household.history() != pending.history:
                self.pending.pop(preview_id, None)
                raise RecipeStalePreview("household facts changed; calculate the recipe again")
            record = self.plans.commit_result(
                pending.result, plan_id=plan_id, decision_basis=pending.basis,
            )
            self.pending.pop(preview_id, None)
            return record

    def discard_previews(self) -> None:
        with self.lock:
            self.pending.clear()
