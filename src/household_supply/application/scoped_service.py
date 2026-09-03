from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from household_supply.domain import CatalogSnapshot
from household_supply.market import MarketCompilationPolicy, MarketProvider

from .models import (
    ApplicationPlanRequest,
    ApplicationPlanResult,
    validate_application_request_catalog,
)
from .service import ApplicationMarketError, Clock, PlanApplicationService


ProviderFactory = Callable[[frozenset[str]], tuple[MarketProvider, ...]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class DemandScopedPlanApplicationService:
    """Plan with market providers selected from the request's demanded Items.

    The factory is invoked only after the request has passed the normal catalog
    preflight. It selects acquisition scope; it does not own catalog identity,
    market compilation, planning arithmetic, or budget semantics.
    """

    catalog: CatalogSnapshot
    provider_factory: ProviderFactory
    market_policy: MarketCompilationPolicy = MarketCompilationPolicy()
    clock: Clock = _utc_now

    def __post_init__(self) -> None:
        if not callable(self.provider_factory):
            raise TypeError("demand-scoped provider_factory must be callable")
        if not callable(self.clock):
            raise TypeError("demand-scoped clock must be callable")

    def plan(self, request: ApplicationPlanRequest) -> ApplicationPlanResult:
        validate_application_request_catalog(request, self.catalog)
        demanded_item_ids = frozenset(demand.item_id for demand in request.demands)
        providers = tuple(self.provider_factory(demanded_item_ids))
        if not providers:
            raise ApplicationMarketError(
                "demand-scoped market provider factory returned no providers"
            )
        return PlanApplicationService(
            catalog=self.catalog,
            providers=providers,
            market_policy=self.market_policy,
            clock=self.clock,
        ).plan(request)
