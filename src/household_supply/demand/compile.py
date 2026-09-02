from __future__ import annotations

from dataclasses import dataclass

from household_supply.domain.demand import Demand
from household_supply.domain.items import Item
from household_supply.domain.quantity import Quantity

from .sources import DemandContribution, DemandSource


@dataclass(frozen=True, slots=True)
class DemandCompilation:
    """Normalized demands plus their exact attributable source contributions."""

    demands: tuple[Demand, ...]
    contributions: tuple[DemandContribution, ...]


@dataclass(frozen=True, slots=True)
class _Aggregate:
    item: Item
    quantity: Quantity


def compile_demand_sources(
    sources: tuple[DemandSource, ...],
) -> DemandCompilation:
    if not sources:
        raise ValueError("at least one demand source is required")

    source_ids: set[str] = set()
    contribution_keys: set[tuple[str, str]] = set()
    contributions: list[DemandContribution] = []

    for source in sources:
        source_id = source.source_id.strip()
        if not source_id:
            raise ValueError("demand source id must not be empty")
        if source_id in source_ids:
            raise ValueError(f"duplicate demand source id: {source_id}")
        source_ids.add(source_id)

        emitted = tuple(source.emit_contributions())
        if not emitted:
            raise ValueError(f"demand source emitted no contributions: {source_id}")
        for contribution in emitted:
            if contribution.source_id != source_id:
                raise ValueError(
                    "demand contribution source attribution mismatch: "
                    f"expected {source_id}, got {contribution.source_id}"
                )
            key = (source_id, contribution.contribution_id)
            if key in contribution_keys:
                raise ValueError(
                    "duplicate demand contribution id: "
                    f"{source_id}/{contribution.contribution_id}"
                )
            contribution_keys.add(key)
            contributions.append(contribution)

    aggregates: dict[str, _Aggregate] = {}
    for contribution in contributions:
        base = contribution.quantity.as_base()
        existing = aggregates.get(contribution.item.id)
        if existing is None:
            aggregates[contribution.item.id] = _Aggregate(
                item=contribution.item,
                quantity=base,
            )
            continue

        if existing.item != contribution.item:
            raise ValueError(
                f"conflicting item identity for id: {contribution.item.id}"
            )
        if not existing.quantity.compatible_with(base):
            raise ValueError(
                f"incompatible demand units for item {contribution.item.id}: "
                f"{existing.quantity.unit} and {base.unit}"
            )
        aggregates[contribution.item.id] = _Aggregate(
            item=existing.item,
            quantity=existing.quantity + base,
        )

    demands = tuple(
        Demand(
            item=aggregate.item,
            quantity=aggregate.quantity,
            source="compiled",
        )
        for _, aggregate in sorted(aggregates.items())
    )
    return DemandCompilation(demands=demands, contributions=tuple(contributions))
