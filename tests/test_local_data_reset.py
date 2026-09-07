from __future__ import annotations

from datetime import datetime, timezone

from household_supply.application import (
    FilePlanRepository,
    LocalDataResetService,
    PlanId,
    PlanRecord,
)
from household_supply.domain import Item, Quantity
from household_supply.household import (
    FileHouseholdEventRepository,
    HouseholdEventId,
    HouseholdLearningService,
    InventoryCorrection,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def plan_record() -> PlanRecord:
    return PlanRecord.create(
        plan_id=PlanId("reset-plan"),
        created_at=NOW,
        request={
            "budget": {"amount": "1000", "currency": "KGS"},
            "demands": [],
            "inventory": [],
            "objective": None,
        },
        result={
            "status": "feasible",
            "market": {},
            "total_cost": {"amount": "0", "currency": "KGS"},
            "budget_remaining": {"amount": "1000", "currency": "KGS"},
            "purchases": [],
            "coverage": [],
            "projected_leftovers": [],
            "infeasibility_reasons": [],
            "warnings": [],
            "explanation": [],
        },
        market_evidence={
            "captured_at": NOW.isoformat(),
            "policy": {},
            "catalog": {},
            "batches": [],
            "dispositions": [],
            "offers": [],
        },
    )


def test_file_local_data_reset_clears_only_published_profile_records(tmp_path) -> None:
    household_repo = FileHouseholdEventRepository(tmp_path / "household-events")
    plan_repo = FilePlanRepository(tmp_path / "plans")
    household = HouseholdLearningService(household_repo)

    milk = Item("milk", "Milk", "dairy")
    household.record(
        InventoryCorrection(
            HouseholdEventId("count-1"),
            milk,
            Quantity("1", "l"),
            NOW,
            NOW,
            "test",
        )
    )
    plan_repo.save(plan_record())

    household_keep = household_repo.root / "keep.txt"
    plan_keep = plan_repo.root / "keep.txt"
    household_keep.write_text("keep", encoding="utf-8")
    plan_keep.write_text("keep", encoding="utf-8")

    result = LocalDataResetService(household, plan_repo).reset()

    assert result.household_events_deleted == 1
    assert result.plans_deleted == 1
    assert household.history().events == ()
    assert plan_repo.list_recent(10) == ()
    assert household_keep.read_text(encoding="utf-8") == "keep"
    assert plan_keep.read_text(encoding="utf-8") == "keep"


def test_empty_reset_is_idempotent(tmp_path) -> None:
    household_repo = FileHouseholdEventRepository(tmp_path / "household-events")
    plan_repo = FilePlanRepository(tmp_path / "plans")
    household = HouseholdLearningService(household_repo)

    result = LocalDataResetService(household, plan_repo).reset()
    assert result.household_events_deleted == 0
    assert result.plans_deleted == 0
