from __future__ import annotations

from datetime import UTC, datetime

import pytest

from household_supply.domain import (
    Demand,
    InventorySnapshot,
    Item,
    MarketSnapshot,
    Money,
    Offer,
    PlanStatus,
    PlanningPolicy,
    PlanningProblem,
    ProcurementPlan,
    ProjectedLeftover,
    Purchase,
    Quantity,
    RequirementCoverage,
    SKU,
)
from household_supply.planning import PlanValidationError, build_plan, validate_plan


NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


def fixture_problem() -> tuple[PlanningProblem, Offer]:
    rice = Item("rice", "Rice")
    rice_sku = SKU("rice-1kg", rice, "Rice 1kg", Quantity(1, "kg"))
    market_offer = Offer(
        "market-rice",
        rice_sku,
        "store-a",
        Money(100, "KGS"),
        NOW,
        "fixture",
    )
    problem = PlanningProblem(
        demands=(Demand(rice, Quantity(1, "kg")),),
        inventory=InventorySnapshot(()),
        market=MarketSnapshot(NOW, (market_offer,)),
        policy=PlanningPolicy(Money(3000, "KGS")),
    )
    return problem, market_offer


def valid_plan() -> tuple[PlanningProblem, ProcurementPlan]:
    problem, _ = fixture_problem()
    return problem, build_plan(problem)


def test_validator_rejects_missing_requirement_coverage() -> None:
    problem, _ = fixture_problem()
    invalid = ProcurementPlan(
        status=PlanStatus.FEASIBLE,
        purchases=(),
        requirement_coverage=(),
        projected_leftovers=(),
        total_cost=Money(0, "KGS"),
        budget_remaining=Money(3000, "KGS"),
    )

    with pytest.raises(PlanValidationError, match="coverage does not match problem"):
        validate_plan(problem, invalid)


def test_validator_rejects_falsified_required_quantity() -> None:
    problem, _ = fixture_problem()
    invalid = ProcurementPlan(
        status=PlanStatus.FEASIBLE,
        purchases=(),
        requirement_coverage=(
            RequirementCoverage(
                "rice",
                Quantity(0, "g"),
                Quantity(0, "g"),
                Quantity(0, "g"),
                Quantity(0, "g"),
            ),
        ),
        projected_leftovers=(ProjectedLeftover("rice", Quantity(0, "g")),),
        total_cost=Money(0, "KGS"),
        budget_remaining=Money(3000, "KGS"),
    )

    with pytest.raises(PlanValidationError, match="required quantity mismatch"):
        validate_plan(problem, invalid)


def test_validator_rejects_offer_outside_market_snapshot() -> None:
    problem, market_offer = fixture_problem()
    foreign_offer = Offer(
        "foreign",
        market_offer.sku,
        "store-b",
        Money(1, "KGS"),
        NOW,
        "foreign-fixture",
    )
    invalid = ProcurementPlan(
        status=PlanStatus.FEASIBLE,
        purchases=(
            Purchase(foreign_offer, 1, Quantity(1, "kg"), Money(1, "KGS")),
        ),
        requirement_coverage=(
            RequirementCoverage(
                "rice",
                Quantity(1000, "g"),
                Quantity(0, "g"),
                Quantity(1000, "g"),
                Quantity(1000, "g"),
            ),
        ),
        projected_leftovers=(ProjectedLeftover("rice", Quantity(0, "g")),),
        total_cost=Money(1, "KGS"),
        budget_remaining=Money(2999, "KGS"),
    )

    with pytest.raises(PlanValidationError, match="outside market snapshot"):
        validate_plan(problem, invalid)


def test_validator_rejects_wrong_purchase_cost() -> None:
    problem, plan = valid_plan()
    purchase = plan.purchases[0]
    invalid_purchase = Purchase(
        purchase.offer,
        purchase.packs,
        purchase.acquired_quantity,
        Money(999, "KGS"),
    )
    invalid = ProcurementPlan(
        status=plan.status,
        purchases=(invalid_purchase,),
        requirement_coverage=plan.requirement_coverage,
        projected_leftovers=plan.projected_leftovers,
        total_cost=plan.total_cost,
        budget_remaining=plan.budget_remaining,
        minimum_required_cost=plan.minimum_required_cost,
    )

    with pytest.raises(PlanValidationError, match="purchase cost does not match"):
        validate_plan(problem, invalid)


def test_validator_rejects_wrong_budget_remaining() -> None:
    problem, plan = valid_plan()
    invalid = ProcurementPlan(
        status=plan.status,
        purchases=plan.purchases,
        requirement_coverage=plan.requirement_coverage,
        projected_leftovers=plan.projected_leftovers,
        total_cost=plan.total_cost,
        budget_remaining=Money(1234, "KGS"),
        minimum_required_cost=plan.minimum_required_cost,
    )

    with pytest.raises(PlanValidationError, match="remaining budget"):
        validate_plan(problem, invalid)


def test_validator_rejects_wrong_projected_leftover() -> None:
    problem, plan = valid_plan()
    invalid = ProcurementPlan(
        status=plan.status,
        purchases=plan.purchases,
        requirement_coverage=plan.requirement_coverage,
        projected_leftovers=(ProjectedLeftover("rice", Quantity(999, "g")),),
        total_cost=plan.total_cost,
        budget_remaining=plan.budget_remaining,
        minimum_required_cost=plan.minimum_required_cost,
    )

    with pytest.raises(PlanValidationError, match="projected leftover mismatch"):
        validate_plan(problem, invalid)
