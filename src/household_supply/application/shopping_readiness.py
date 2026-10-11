"""Shared read-only shopping readiness across packed and weighted Items.

The boundary reports one truthful list of needs. It does not partially save
a packaged plan while dropping weighted deficits, assume unknown stock is 0,
or infer seller min/step from a per-kg reference price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from household_supply.application.kyrgyz_recipes import KYRGYZ_RECIPE_SPECS
from household_supply.domain import CatalogSnapshot, Quantity
from household_supply.domain._decimal import subtract_decimals_exact
from household_supply.household import HouseholdLearningService, HouseholdState
from household_supply.market.catalogs.globus_weight_candidates import WEIGHT_CANDIDATES


Clock = Callable[[], datetime]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ShoppingReadinessError(ValueError):
    """Requested shared shopping review is invalid or ambiguous."""


@dataclass(frozen=True, slots=True)
class ShoppingDemand:
    item_id: str
    required: Quantity

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise ShoppingReadinessError("shopping demand needs an Item ID")
        if self.required.amount <= 0:
            raise ShoppingReadinessError("shopping demand quantity must be positive")
        object.__setattr__(self, "item_id", self.item_id.strip())


def _quant(value: Quantity | None) -> dict[str, str] | None:
    if value is None:
        return None
    return {"amount": format(value.amount.normalize(), "f"), "unit": value.unit}


def recipe_candidates() -> tuple[dict, ...]:
    return tuple(
        {
            "recipe_id": ident, "name": name, "category": category,
            "cuisine": cuisine, "servings": servings,
            "ingredient_count": len(ingredients),
            "definition_only": True,
        }
        for ident, name, category, cuisine, servings, ingredients, _ in KYRGYZ_RECIPE_SPECS
    )


@dataclass(frozen=True, slots=True)
class ShoppingReadinessService:
    household: HouseholdLearningService
    catalog: CatalogSnapshot
    clock: Clock = _now

    def _classify(self, item_id: str, required: Quantity) -> tuple[str, str, str | None]:
        skus = [sku for sku in self.catalog.skus if sku.item.id == item_id]
        candidates = [c for c in WEIGHT_CANDIDATES if c.item_id == item_id]
        if len(candidates) > 1:
            raise ShoppingReadinessError("ambiguous weighted catalog identity")
        if skus:
            if any(sku.package_quantity.compatible_with(required) for sku in skus):
                return skus[0].item.canonical_name, "fixed_package", None
            return skus[0].item.canonical_name, "unsupported_units", None
        if candidates:
            candidate = candidates[0]
            if required.dimension != "mass":
                return candidate.item_name, "unsupported_units", candidate.source_url
            return candidate.item_name, "weighted_reference", candidate.source_url
        return item_id, "unsupported_item", None

    def _recipe_demands(self, recipe_id: str) -> tuple[str, tuple[ShoppingDemand, ...]]:
        matches = [entry for entry in KYRGYZ_RECIPE_SPECS if entry[0] == recipe_id]
        if len(matches) != 1:
            raise ShoppingReadinessError("recipe does not exist in candidate catalog")
        _, name, _, _, _, ingredients, _ = matches[0]
        return name, tuple(
            ShoppingDemand(item_id, Quantity(amount, unit))
            for item_id, amount, unit in ingredients
        )

    def preview(
        self, *,
        demands: tuple[ShoppingDemand, ...] | None = None,
        recipe_id: str | None = None,
    ) -> dict:
        if (demands is None) == (recipe_id is None):
            raise ShoppingReadinessError("provide exactly one of demands or recipe_id")
        label = None
        if recipe_id is not None:
            label, demands = self._recipe_demands(recipe_id)
        assert demands is not None
        if not 1 <= len(demands) <= 32:
            raise ShoppingReadinessError("shopping preview expects 1-32 demand lines")

        # Combine repeated canonical Item IDs before comparing stock; do not
        # subtract one pantry balance separately for every recipe ingredient.
        required: dict[str, Quantity] = {}
        for entry in demands:
            base = entry.required.as_base()
            previous = required.get(entry.item_id)
            if previous is not None and not previous.compatible_with(base):
                raise ShoppingReadinessError("duplicate shopping Item has conflicting dimensions")
            required[entry.item_id] = base if previous is None else previous + base

        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ShoppingReadinessError("shopping review clock must be timezone-aware")
        state: HouseholdState = self.household.state(as_of=now)
        lines = []
        for item_id, quantity in sorted(required.items()):
            name, kind, source_ref = self._classify(item_id, quantity)
            observed = state.quantity_for(item_id)
            missing = None
            if observed is None:
                stock = "unverified"
            elif not observed.compatible_with(quantity):
                stock = "unit_conflict"
            elif observed.base_amount >= quantity.base_amount:
                stock = "covered"
                missing = Quantity(0, quantity.base_unit)
            else:
                stock = "short"
                missing = Quantity(
                    subtract_decimals_exact(quantity.base_amount, observed.base_amount),
                    quantity.base_unit,
                )

            if stock == "unit_conflict":
                review = "stock_unit_conflict"
            elif stock == "unverified":
                review = "inventory_unverified"
            elif stock == "covered":
                review = "already_on_hand"
            elif kind == "fixed_package":
                review = "package_quote_required"
            elif kind == "weighted_reference":
                review = "weighted_sale_rule_unverified"
            else:
                review = "catalog_unsupported"

            lines.append({
                "item_id": item_id, "name": name, "kind": kind,
                "required": _quant(quantity),
                "recorded_on_hand": _quant(observed),
                "missing": _quant(missing),
                "stock_status": stock, "review_status": review,
                "source_ref": source_ref,
                # A reference price is never a sale rule or a purchase amount.
                "purchase_authorized": False,
            })

        inventory_ready = all(line["stock_status"] == "covered" for line in lines)
        blockers_set = {
            entry["review_status"] for entry in lines
            if entry["review_status"] not in {"already_on_hand", "package_quote_required"}
        }
        # Unknown inventory must not hide other independent blockers.
        for entry in lines:
            if entry["stock_status"] == "covered":
                continue
            if entry["kind"] == "weighted_reference":
                blockers_set.add("weighted_sale_rule_unverified")
            elif entry["kind"] in {"unsupported_units", "unsupported_item"}:
                blockers_set.add("catalog_unsupported")
        blockers = sorted(blockers_set)
        return {
            "source": "recipe" if recipe_id is not None else "manual",
            "recipe_id": recipe_id,
            "label": label,
            "as_of": state.as_of.isoformat(),
            "inventory_ready": inventory_ready,
            "can_quote_packaged_only": (
                not inventory_ready
                and all(entry["kind"] == "fixed_package" for entry in lines)
                and all(entry["stock_status"] in {"covered", "short"} for entry in lines)
            ),
            # The shared preview has NO partial-save or mixed-plan authority;
            # already covered means "cookable from recorded stock", not a plan.
            "can_save_as_plan": False,
            "status": "covered" if inventory_ready else "needs_review",
            "lines": lines,
            "blockers": blockers,
            "notice": (
                "Это полный состав потребностей на основе записанных остатков. "
                "Неизвестный остаток не равен нулю. Весовые цены не подтверждают "
                "возможность покупки заданной массы; этот предпросмотр не создаёт план."
            ),
        }
