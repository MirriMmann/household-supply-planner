from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

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
    HouseholdHistory,
    HouseholdLearningService,
    HouseholdState,
    RecurringNeedSource,
    estimate_all_depletion,
    project_household_state,
)

from .lifecycle import PlanLifecycleService, serialize_plan_request
from .models import (
    ApplicationPlanRequest,
    ApplicationRequestError,
    InventoryInput,
    RequestedItem,
    UnknownCatalogItemError,
    catalog_items_by_id,
    validate_application_request_catalog,
)
from .persistence import PlanRecord


ReplenishmentClock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _require_aware(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


class HouseholdReplenishmentRequestError(ApplicationRequestError):
    """A household replenishment request cannot be compiled safely."""


@dataclass(frozen=True, slots=True)
class HouseholdReplenishmentRequest:
    budget: Money
    horizon_days: Decimal
    explicit_needs: tuple[RequestedItem, ...] = ()
    objective_policy: MultiObjectivePolicy | None = None

    def __init__(
        self,
        budget: Money,
        horizon_days: DecimalLike,
        explicit_needs: tuple[RequestedItem, ...] = (),
        objective_policy: MultiObjectivePolicy | None = None,
    ) -> None:
        object.__setattr__(self, "budget", budget)
        object.__setattr__(self, "horizon_days", as_decimal(horizon_days))
        object.__setattr__(self, "explicit_needs", tuple(explicit_needs))
        object.__setattr__(self, "objective_policy", objective_policy)
        self.__post_init__()

    def __post_init__(self) -> None:
        if self.budget.amount < 0:
            raise HouseholdReplenishmentRequestError(
                "household replenishment budget must not be negative"
            )
        if self.horizon_days <= 0:
            raise HouseholdReplenishmentRequestError(
                "household replenishment horizon_days must be positive"
            )
        explicit = tuple(self.explicit_needs)
        item_ids = [need.item_id for need in explicit]
        if len(item_ids) != len(set(item_ids)):
            raise HouseholdReplenishmentRequestError(
                "household replenishment contains duplicate explicit item_id values"
            )
        explicit = tuple(sorted(explicit, key=lambda need: need.item_id))
        if (
            self.objective_policy is not None
            and self.objective_policy.additional_store_penalty.currency
            != self.budget.currency
        ):
            raise HouseholdReplenishmentRequestError(
                "objective policy currency must match replenishment budget currency"
            )
        object.__setattr__(self, "explicit_needs", explicit)


@dataclass(frozen=True, slots=True)
class HouseholdReplenishmentPreparation:
    """Self-validating household-to-application planning preparation snapshot."""

    request: HouseholdReplenishmentRequest
    catalog: CatalogSnapshot
    history: HouseholdHistory
    as_of: datetime
    state: HouseholdState
    estimates: tuple[ConsumptionEstimate, ...]
    demand_compilation: DemandCompilation
    application_request: ApplicationPlanRequest

    def __post_init__(self) -> None:
        _require_aware(self.as_of, label="household replenishment as_of")
        estimates = tuple(self.estimates)
        object.__setattr__(self, "estimates", estimates)

        expected_state = project_household_state(self.history, as_of=self.as_of)
        if self.state != expected_state:
            raise ValueError(
                "household replenishment state does not match history/as_of basis"
            )
        expected_estimates = estimate_all_depletion(self.history, as_of=self.as_of)
        if estimates != expected_estimates:
            raise ValueError(
                "household replenishment estimates do not match history/as_of basis"
            )
        expected_compilation, expected_application = _compile_replenishment_inputs(
            self.request,
            self.catalog,
            self.state,
            estimates,
        )
        if self.demand_compilation != expected_compilation:
            raise ValueError(
                "household replenishment demand compilation does not match source basis"
            )
        if self.application_request != expected_application:
            raise ValueError(
                "household replenishment application request does not match derived basis"
            )


@dataclass(frozen=True, slots=True)
class HouseholdReplenishmentResult:
    preparation: HouseholdReplenishmentPreparation
    plan_record: PlanRecord

    def __post_init__(self) -> None:
        expected_request = serialize_plan_request(self.preparation.application_request)
        if self.plan_record.request.to_mapping() != expected_request:
            raise ValueError(
                "persisted plan request does not match household replenishment preparation"
            )


def _validate_catalog_identity(
    catalog_items: dict[str, object],
    *,
    item_id: str,
    item: object,
    source: str,
) -> None:
    catalog_item = catalog_items.get(item_id)
    if catalog_item is None:
        raise UnknownCatalogItemError(
            f"{source} item is not present in configured catalog: {item_id}"
        )
    if catalog_item != item:
        raise HouseholdReplenishmentRequestError(
            f"{source} Item identity conflicts with configured catalog: {item_id}"
        )


def _compile_replenishment_inputs(
    request: HouseholdReplenishmentRequest,
    catalog: CatalogSnapshot,
    state: HouseholdState,
    estimates: tuple[ConsumptionEstimate, ...],
) -> tuple[DemandCompilation, ApplicationPlanRequest]:
    items = catalog_items_by_id(catalog)
    sources = []

    if estimates:
        for estimate in estimates:
            _validate_catalog_identity(
                items,
                item_id=estimate.item.id,
                item=estimate.item,
                source="learned recurring",
            )
        sources.append(
            RecurringNeedSource(
                "household:recurring",
                request.horizon_days,
                estimates,
            )
        )

    if request.explicit_needs:
        explicit = []
        for requested in request.explicit_needs:
            item = items.get(requested.item_id)
            if item is None:
                raise UnknownCatalogItemError(
                    "explicit replenishment item is not present in configured catalog: "
                    f"{requested.item_id}"
                )
            explicit.append(ExplicitNeed(item, requested.quantity))
        sources.append(ExplicitNeedSource("request:explicit", tuple(explicit)))

    if not sources:
        raise HouseholdReplenishmentRequestError(
            "replenishment requires explicit needs or recorded consumption estimates"
        )

    compilation = compile_demand_sources(tuple(sources))
    demand_ids = {demand.item.id for demand in compilation.demands}

    inventory = []
    for balance in state.balances:
        if balance.item.id not in demand_ids or balance.quantity.amount <= 0:
            continue
        _validate_catalog_identity(
            items,
            item_id=balance.item.id,
            item=balance.item,
            source="household inventory",
        )
        inventory.append(
            InventoryInput(
                lot_id=f"household:{balance.item.id}",
                item_id=balance.item.id,
                quantity=balance.quantity,
            )
        )

    application_request = ApplicationPlanRequest(
        demands=tuple(
            RequestedItem(demand.item.id, demand.quantity)
            for demand in compilation.demands
        ),
        inventory=tuple(inventory),
        budget=request.budget,
        objective_policy=request.objective_policy,
    )
    validate_application_request_catalog(application_request, catalog)
    return compilation, application_request


@dataclass(frozen=True, slots=True)
class HouseholdReplenishmentService:
    household: HouseholdLearningService
    plans: PlanLifecycleService
    clock: ReplenishmentClock = _utc_now

    def __post_init__(self) -> None:
        if not callable(self.clock):
            raise TypeError("household replenishment clock must be callable")

    def prepare(
        self, request: HouseholdReplenishmentRequest
    ) -> HouseholdReplenishmentPreparation:
        as_of = self.clock()
        _require_aware(as_of, label="household replenishment as_of")
        history = self.household.history()
        state = project_household_state(history, as_of=as_of)
        estimates = estimate_all_depletion(history, as_of=as_of)
        compilation, application_request = _compile_replenishment_inputs(
            request,
            self.plans.planner.catalog,
            state,
            estimates,
        )
        return HouseholdReplenishmentPreparation(
            request=request,
            catalog=self.plans.planner.catalog,
            history=history,
            as_of=as_of,
            state=state,
            estimates=estimates,
            demand_compilation=compilation,
            application_request=application_request,
        )

    def create(self, request: HouseholdReplenishmentRequest) -> HouseholdReplenishmentResult:
        preparation = self.prepare(request)
        record = self.plans.create(preparation.application_request)
        return HouseholdReplenishmentResult(preparation, record)
