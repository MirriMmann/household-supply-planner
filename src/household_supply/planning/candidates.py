from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import product

from household_supply.domain import Money, PlanningProblem, Purchase, Quantity
from household_supply.domain._decimal import (
    add_decimals_exact,
    ceil_decimal_ratio_exact,
    multiply_decimal_by_int_exact,
    subtract_decimals_exact,
)

from .compile import CompiledRequirement


_MAX_COMBINATIONS_PER_ITEM = 200_000


@dataclass(frozen=True, slots=True)
class ItemCandidate:
    purchases: tuple[Purchase, ...]
    purchased: Quantity
    purchase_cost: Money
    surplus: Quantity
    sellers: frozenset[str]
    total_packs: int
    count_signature: tuple[int, ...]


def enumerate_item_candidates(
    problem: PlanningProblem, requirement: CompiledRequirement
) -> tuple[ItemCandidate, ...]:
    currency = problem.policy.budget.currency
    compatible_offers = []
    for offer in sorted(problem.market.offers, key=lambda candidate: candidate.id):
        if not offer.available:
            continue
        if offer.price.currency != currency:
            continue
        if offer.sku.item.id != requirement.item_id:
            continue
        package = offer.sku.package_quantity.as_base()
        if not package.compatible_with(requirement.net_required):
            continue
        compatible_offers.append((offer, package))

    if requirement.net_required.amount == 0:
        zero = Quantity(0, requirement.required.base_unit)
        return (
            ItemCandidate(
                purchases=(),
                purchased=zero,
                purchase_cost=Money.zero(currency),
                surplus=zero,
                sellers=frozenset(),
                total_packs=0,
                count_signature=(),
            ),
        )

    if not compatible_offers:
        return ()

    ranges: list[range] = []
    combinations = 1
    for _, package in compatible_offers:
        max_packs = ceil_decimal_ratio_exact(
            requirement.net_required.base_amount,
            package.base_amount,
        )
        candidate_range = range(max_packs + 1)
        ranges.append(candidate_range)
        combinations *= len(candidate_range)

    if combinations > _MAX_COMBINATIONS_PER_ITEM:
        raise RuntimeError(
            f"candidate search space for {requirement.item_id} is too large: "
            f"{combinations} combinations"
        )

    candidates: list[ItemCandidate] = []
    for counts in product(*ranges):
        if not any(counts):
            continue

        total_quantity = Decimal("0")
        total_cost = Decimal("0")
        total_packs = 0
        purchases: list[Purchase] = []
        sellers: set[str] = set()

        for count, (offer, package) in zip(counts, compatible_offers, strict=True):
            if count == 0:
                continue
            acquired_amount = multiply_decimal_by_int_exact(package.base_amount, count)
            purchase_cost = offer.price * count
            acquired = Quantity(acquired_amount, package.base_unit)
            purchases.append(
                Purchase(
                    offer=offer,
                    packs=count,
                    acquired_quantity=acquired,
                    cost=purchase_cost,
                )
            )
            total_quantity = add_decimals_exact(total_quantity, acquired_amount)
            total_cost = add_decimals_exact(total_cost, purchase_cost.amount)
            total_packs += count
            sellers.add(offer.seller_id)

        if total_quantity < requirement.net_required.base_amount:
            continue

        surplus_amount = subtract_decimals_exact(
            total_quantity, requirement.net_required.base_amount
        )
        candidates.append(
            ItemCandidate(
                purchases=tuple(purchases),
                purchased=Quantity(total_quantity, requirement.required.base_unit),
                purchase_cost=Money(total_cost, currency),
                surplus=Quantity(surplus_amount, requirement.required.base_unit),
                sellers=frozenset(sellers),
                total_packs=total_packs,
                count_signature=tuple(counts),
            )
        )

    return tuple(candidates)


def baseline_candidate_key(candidate: ItemCandidate) -> tuple:
    return (
        candidate.purchase_cost.amount,
        candidate.surplus.base_amount,
        candidate.total_packs,
        candidate.count_signature,
    )
