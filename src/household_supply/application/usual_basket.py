"""Explicit, editable routine preferences and deterministic proposal composition.

A routine is not a demand fact and never mutates household history. Preparation
chooses exactly one demand source per item before reaching the existing planner.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .usual_basket_persistence import UsualBasketRepository

from household_supply.demand import (
    DemandCompilation,
    ExplicitNeed,
    ExplicitNeedSource,
    compile_demand_sources,
)
from household_supply.domain import CatalogSnapshot, Money, MultiObjectivePolicy, Quantity
from household_supply.domain.money import DecimalLike, as_decimal
from household_supply.household import (
    ConsumptionEstimate,
    HouseholdLearningService,
    HouseholdState,
    RecurringNeedSource,
    admitted_recurring_estimates,
    project_household_state,
)

from .models import (
    ApplicationPlanRequest,
    ApplicationRequestError,
    InventoryInput,
    RequestedItem,
    UnknownCatalogItemError,
    catalog_items_by_id,
    validate_application_request_catalog,
)


class UsualBasketError(ApplicationRequestError):
    """A routine cannot be safely edited or compiled."""


@dataclass(frozen=True, slots=True)
class UsualBasketItem:
    item_id: str
    fallback_quantity: Quantity | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.item_id, str) or not self.item_id.strip():
            raise UsualBasketError("usual basket item_id must be nonempty")
        object.__setattr__(self, "item_id", self.item_id.strip())
        if self.fallback_quantity is not None:
            if not isinstance(self.fallback_quantity, Quantity):
                raise UsualBasketError("usual basket fallback must be Quantity")
            if self.fallback_quantity.amount <= 0:
                raise UsualBasketError("usual basket fallback must be positive")


@dataclass(frozen=True, slots=True)
class UsualBasket:
    """Canonical selection; absence of fallback means ask, not buy one pack."""

    items: tuple[UsualBasketItem, ...] = ()

    def __post_init__(self) -> None:
        items = tuple(self.items)
        if not all(isinstance(item, UsualBasketItem) for item in items):
            raise UsualBasketError("usual basket requires UsualBasketItem entries")
        ids = [item.item_id for item in items]
        if len(ids) != len(set(ids)):
            raise UsualBasketError("usual basket contains duplicate item_id")
        object.__setattr__(self, "items", tuple(sorted(items, key=lambda x: x.item_id)))


@dataclass(frozen=True, slots=True)
class UsualBasketChoice:
    item_id: str
    basis: str  # recurring, fallback, override, excluded, needs_quantity
    quantity: Quantity | None = None


@dataclass(frozen=True, slots=True)
class UsualBasketProposal:
    """Read-only preparation. Missing quantities block planning, not silently vanish."""

    choices: tuple[UsualBasketChoice, ...]
    compilation: DemandCompilation | None
    application_request: ApplicationPlanRequest | None

    @property
    def needs_clarification(self) -> tuple[str, ...]:
        return tuple(c.item_id for c in self.choices if c.basis == "needs_quantity")

    @property
    def ready(self) -> bool:
        return self.application_request is not None


def compose_usual_basket(
    *,
    basket: UsualBasket,
    catalog: CatalogSnapshot,
    state: HouseholdState,
    admitted_estimates: tuple[ConsumptionEstimate, ...],
    budget: Money,
    horizon_days: DecimalLike,
    overrides: tuple[RequestedItem, ...] = (),
    exclusions: tuple[str, ...] = (),
    objective_policy: MultiObjectivePolicy | None = None,
) -> UsualBasketProposal:
    """Select one basis per item; delegate arithmetic to M2/M9 and M1/M3.

    Precedence: exclusion > one-off override > accepted recurring > fallback
    > clarification. One-off override replaces, rather than adds to, recurring.
    No history, market or repository writes occur here.
    """
    if not isinstance(basket, UsualBasket):
        raise TypeError("basket must be UsualBasket")
    horizon = as_decimal(horizon_days)
    if horizon <= 0:
        raise UsualBasketError("horizon_days must be positive")
    if budget.amount < 0:
        raise UsualBasketError("budget must not be negative")
    items = catalog_items_by_id(catalog)
    routine = {item.item_id: item for item in basket.items}
    overrides = tuple(overrides)
    if not all(isinstance(item, RequestedItem) for item in overrides):
        raise UsualBasketError("overrides must contain RequestedItem")
    override_ids = [need.item_id for need in overrides]
    if len(set(override_ids)) != len(override_ids):
        raise UsualBasketError("duplicate one-off override item_id")
    overrides_by_id = {entry.item_id: entry for entry in overrides}

    excluded = tuple(exclusions)
    if any(not isinstance(item_id, str) or not item_id.strip() for item_id in excluded):
        raise UsualBasketError("invalid exclusion item_id")
    if len(set(excluded)) != len(excluded):
        raise UsualBasketError("duplicate excluded item_id")
    selected_ids = set(routine) | set(overrides_by_id)
    if set(excluded) - selected_ids:
        raise UsualBasketError("exclusion references item outside routine/overrides")

    active_ids = selected_ids - set(excluded)
    for item_id in active_ids:
        if item_id not in items:
            raise UnknownCatalogItemError(f"usual basket item not in catalog: {item_id}")
    # Exclusions precede quantity interpretation, even for stale preferences.
    # Validate active choices even if an accepted estimate would take precedence.
    for item_id, entry in routine.items():
        if item_id in excluded:
            continue
        if entry.fallback_quantity is not None and not any(
            sku.item.id == item_id
            and entry.fallback_quantity.compatible_with(sku.package_quantity)
            for sku in catalog.skus
        ):
            raise UsualBasketError(f"fallback unit incompatible with catalog: {item_id}")
    for item_id, entry in overrides_by_id.items():
        if item_id in excluded:
            continue
        if not any(
            sku.item.id == item_id and entry.quantity.compatible_with(sku.package_quantity)
            for sku in catalog.skus
        ):
            raise UsualBasketError(f"override unit incompatible with catalog: {item_id}")

    estimates = tuple(admitted_estimates)
    if len(set(e.item.id for e in estimates)) != len(estimates):
        raise UsualBasketError("duplicate admitted recurring estimate item_id")
    estimates_by_id = {estimate.item.id: estimate for estimate in estimates}
    for item_id in active_ids & estimates_by_id.keys():
        if estimates_by_id[item_id].item != items[item_id]:
            raise UsualBasketError(f"recurring Item identity conflicts with catalog: {item_id}")

    choices = []
    recurring = []
    fallback = []
    one_off = []
    for item_id in sorted(selected_ids):
        if item_id in excluded:
            choices.append(UsualBasketChoice(item_id, "excluded"))
        elif item_id in overrides_by_id:
            quantity = overrides_by_id[item_id].quantity
            choices.append(UsualBasketChoice(item_id, "override", quantity))
            one_off.append(ExplicitNeed(items[item_id], quantity))
        elif item_id in estimates_by_id:
            estimate = estimates_by_id[item_id]
            choices.append(UsualBasketChoice(item_id, "recurring"))
            recurring.append(estimate)
        elif item_id in routine and routine[item_id].fallback_quantity is not None:
            quantity = routine[item_id].fallback_quantity
            choices.append(UsualBasketChoice(item_id, "fallback", quantity))
            fallback.append(ExplicitNeed(items[item_id], quantity))
        else:
            choices.append(UsualBasketChoice(item_id, "needs_quantity"))

    choices_tuple = tuple(choices)
    if any(choice.basis == "needs_quantity" for choice in choices):
        return UsualBasketProposal(choices_tuple, None, None)

    sources = []
    if recurring:
        sources.append(RecurringNeedSource("household:recurring", horizon, tuple(recurring)))
    if fallback:
        sources.append(ExplicitNeedSource("routine:fallback", tuple(fallback)))
    if one_off:
        sources.append(ExplicitNeedSource("request:override", tuple(one_off)))
    if not sources:
        return UsualBasketProposal(choices_tuple, None, None)

    compilation = compile_demand_sources(tuple(sources))
    demand_ids = {d.item.id for d in compilation.demands}
    inventory = []
    for balance in sorted(state.balances, key=lambda balance: balance.item.id):
        if balance.item.id not in demand_ids or balance.quantity.amount <= 0:
            continue
        if items[balance.item.id] != balance.item:
            raise UsualBasketError(
                f"household balance Item identity conflicts with catalog: {balance.item.id}"
            )
        inventory.append(
            InventoryInput(
                f"household:{balance.item.id}",
                balance.item.id,
                balance.quantity,
            )
        )
    request = ApplicationPlanRequest(
        demands=tuple(
            RequestedItem(d.item.id, d.quantity) for d in compilation.demands
        ),
        inventory=tuple(inventory),
        budget=budget,
        objective_policy=objective_policy,
    )
    validate_application_request_catalog(request, catalog)
    return UsualBasketProposal(choices_tuple, compilation, request)


@dataclass(frozen=True, slots=True)
class UsualBasketPreparationService:
    """Read real household evidence; return a candidate without making a plan."""

    household: HouseholdLearningService
    catalog: CatalogSnapshot
    basket_repository: UsualBasketRepository
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def prepare(
        self,
        *,
        budget: Money,
        horizon_days: DecimalLike,
        overrides: tuple[RequestedItem, ...] = (),
        exclusions: tuple[str, ...] = (),
        objective_policy: MultiObjectivePolicy | None = None,
    ) -> UsualBasketProposal:
        as_of = self.clock()
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise UsualBasketError("clock must be timezone-aware")
        history = self.household.history()
        state = project_household_state(history, as_of=as_of)
        estimates = admitted_recurring_estimates(history, as_of=as_of)
        return compose_usual_basket(
            basket=self.basket_repository.load(),
            catalog=self.catalog,
            state=state,
            admitted_estimates=estimates,
            budget=budget,
            horizon_days=horizon_days,
            overrides=overrides,
            exclusions=exclusions,
            objective_policy=objective_policy,
        )
