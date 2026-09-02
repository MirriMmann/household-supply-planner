from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from household_supply.domain import Demand, PlanningProblem, Quantity


@dataclass(frozen=True, slots=True)
class CompiledRequirement:
    item_id: str
    required: Quantity
    inventory_available: Quantity
    inventory_used: Quantity
    net_required: Quantity


def _aggregate_demands(demands: tuple[Demand, ...]) -> dict[str, Quantity]:
    aggregated: dict[str, Quantity] = {}
    for demand in demands:
        base = demand.quantity.as_base()
        previous = aggregated.get(demand.item.id)
        if previous is None:
            aggregated[demand.item.id] = base
            continue
        if not previous.compatible_with(base):
            raise ValueError(
                f"incompatible demand units for item {demand.item.id}: "
                f"{previous.unit} and {base.unit}"
            )
        aggregated[demand.item.id] = Quantity(
            previous.base_amount + base.base_amount,
            previous.base_unit,
        )
    return aggregated


def _aggregate_inventory(problem: PlanningProblem) -> dict[str, Quantity]:
    aggregated: dict[str, Quantity] = {}
    for lot in problem.inventory.lots:
        base = lot.quantity.as_base()
        previous = aggregated.get(lot.item.id)
        if previous is None:
            aggregated[lot.item.id] = base
            continue
        if not previous.compatible_with(base):
            raise ValueError(
                f"incompatible inventory units for item {lot.item.id}: "
                f"{previous.unit} and {base.unit}"
            )
        aggregated[lot.item.id] = Quantity(
            previous.base_amount + base.base_amount,
            previous.base_unit,
        )
    return aggregated


def compile_requirements(problem: PlanningProblem) -> tuple[CompiledRequirement, ...]:
    demands = _aggregate_demands(problem.demands)
    inventory = _aggregate_inventory(problem)
    result: list[CompiledRequirement] = []

    for item_id in sorted(demands):
        required = demands[item_id]
        available = inventory.get(item_id, Quantity(0, required.base_unit))
        if not required.compatible_with(available):
            raise ValueError(
                f"inventory unit for item {item_id} is incompatible with demand"
            )
        available = available.as_base()
        used_amount = min(required.base_amount, available.base_amount)
        net_amount = required.base_amount - used_amount
        result.append(
            CompiledRequirement(
                item_id=item_id,
                required=required,
                inventory_available=available,
                inventory_used=Quantity(used_amount, required.base_unit),
                net_required=Quantity(net_amount, required.base_unit),
            )
        )
    return tuple(result)
