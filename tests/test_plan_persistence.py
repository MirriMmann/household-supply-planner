from __future__ import annotations

from datetime import timedelta
import json

import pytest

from household_supply.application import (
    CanonicalJsonObject,
    FilePlanRepository,
    InMemoryPlanRepository,
    PlanId,
    PlanLifecycleService,
    PlanRecord,
    PlanRecordCorruptionError,
    PlanRepositoryError,
    build_plan_record,
    serialize_market_evidence,
    serialize_plan_request,
)
from household_supply.domain import Money

from test_application_service import NOW, make_request, make_service


def make_record(*, plan_id: str = "plan-1", created_offset: int = 1) -> PlanRecord:
    result = make_service().plan(make_request())
    return build_plan_record(
        plan_id=PlanId(plan_id),
        created_at=NOW + timedelta(seconds=created_offset),
        result=result,
    )


def test_plan_id_rejects_path_and_noncanonical_values() -> None:
    for value in ("", "../x", "a/b", "A", ".hidden", "has space", "é"):
        with pytest.raises(ValueError):
            PlanId(value)
    assert PlanId("abc_123-x").value == "abc_123-x"


def test_canonical_json_object_is_immutable_by_value() -> None:
    original = {"b": [2, 1], "a": {"x": "y"}}
    snapshot = CanonicalJsonObject.from_mapping(original)
    original["a"]["x"] = "changed"
    assert snapshot.to_mapping() == {"a": {"x": "y"}, "b": [2, 1]}
    assert snapshot.text == '{"a":{"x":"y"},"b":[2,1]}'


def test_plan_record_digest_rejects_modified_content() -> None:
    record = make_record()
    with pytest.raises(ValueError, match="digest"):
        PlanRecord(
            plan_id=record.plan_id,
            created_at=record.created_at,
            request=CanonicalJsonObject.from_mapping({"changed": True}),
            result=record.result,
            market_evidence=record.market_evidence,
            digest=record.digest,
        )


def test_plan_request_snapshot_preserves_explicit_request_semantics() -> None:
    payload = serialize_plan_request(make_request())
    assert payload["budget"] == {"amount": "1000", "currency": "KGS"}
    assert payload["demands"][0] == {
        "item_id": "milk",
        "quantity": {"amount": "1500", "unit": "ml"},
    }
    assert payload["objective"] is None


def test_market_evidence_snapshot_contains_complete_m4_basis() -> None:
    result = make_service().plan(make_request())
    evidence = serialize_market_evidence(result.market_compilation)
    assert evidence["captured_at"] == NOW.isoformat()
    assert [sku["id"] for sku in evidence["catalog"]["skus"]] == [
        "milk-1l",
        "oil-1l",
    ]
    assert evidence["batches"][0]["observations"][0]["id"] == "obs-milk"
    assert evidence["dispositions"][0]["status"] == "accepted"
    provenance_by_sku = {
        offer["sku_id"]: offer["provenance"]["source_ref"]
        for offer in evidence["offers"]
    }
    assert provenance_by_sku["milk-1l"] == "fixture://milk"


def test_in_memory_repository_round_trip_and_recent_order() -> None:
    repository = InMemoryPlanRepository()
    older = make_record(plan_id="older", created_offset=1)
    newer = make_record(plan_id="newer", created_offset=2)
    repository.save(older)
    repository.save(newer)
    assert repository.get(older.plan_id) == older
    assert repository.list_recent(1) == (newer,)
    assert repository.list_recent(2) == (newer, older)


def test_in_memory_repository_rejects_overwrite() -> None:
    repository = InMemoryPlanRepository()
    record = make_record()
    repository.save(record)
    with pytest.raises(PlanRepositoryError, match="already exists"):
        repository.save(record)
    assert repository.get(record.plan_id) == record


def test_repository_limit_is_strict() -> None:
    repository = InMemoryPlanRepository()
    for value in (0, -1, 1001, True, 1.5):
        with pytest.raises(ValueError):
            repository.list_recent(value)  # type: ignore[arg-type]


def test_file_repository_round_trip(tmp_path) -> None:
    repository = FilePlanRepository(tmp_path / "plans")
    record = make_record()
    repository.save(record)
    restored = repository.get(record.plan_id)
    assert restored == record
    assert json.loads((tmp_path / "plans" / "plan-1.json").read_text())["digest"] == record.digest


def test_file_repository_does_not_overwrite_existing_record(tmp_path) -> None:
    repository = FilePlanRepository(tmp_path)
    record = make_record()
    repository.save(record)
    before = (tmp_path / "plan-1.json").read_bytes()
    with pytest.raises(PlanRepositoryError, match="already exists"):
        repository.save(record)
    assert (tmp_path / "plan-1.json").read_bytes() == before


def test_file_repository_detects_content_corruption(tmp_path) -> None:
    repository = FilePlanRepository(tmp_path)
    record = make_record()
    repository.save(record)
    path = tmp_path / "plan-1.json"
    payload = json.loads(path.read_text())
    payload["result"]["total_cost"]["amount"] = "999"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PlanRecordCorruptionError, match="digest"):
        repository.get(record.plan_id)


def test_file_repository_rejects_filename_identity_mismatch(tmp_path) -> None:
    repository = FilePlanRepository(tmp_path)
    record = make_record(plan_id="actual")
    payload = record.to_storage_payload()
    (tmp_path / "different.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PlanRecordCorruptionError, match="identity"):
        repository.get(PlanId("different"))


def test_file_repository_enforces_record_size_limit(tmp_path) -> None:
    repository = FilePlanRepository(tmp_path, max_record_bytes=10)
    with pytest.raises(PlanRepositoryError, match="size limit"):
        repository.save(make_record())


def test_plan_record_storage_schema_is_strict() -> None:
    payload = make_record().to_storage_payload()
    payload["unexpected"] = True
    with pytest.raises(PlanRecordCorruptionError, match="schema"):
        PlanRecord.from_storage_payload(payload)


def test_lifecycle_computes_then_persists_one_historical_record() -> None:
    repository = InMemoryPlanRepository()
    lifecycle = PlanLifecycleService(
        make_service(),
        repository,
        clock=lambda: NOW + timedelta(seconds=1),
        id_factory=lambda: PlanId("created-plan"),
    )
    record = lifecycle.create(make_request())
    assert record.plan_id == PlanId("created-plan")
    assert record.result.to_mapping()["total_cost"] == {
        "amount": "310",
        "currency": "KGS",
    }
    assert lifecycle.get(record.plan_id) == record


def test_lifecycle_persists_infeasible_as_completed_history() -> None:
    repository = InMemoryPlanRepository()
    request = make_request()
    request = type(request)(
        demands=request.demands,
        budget=Money(1, "KGS"),
        inventory=request.inventory,
    )
    lifecycle = PlanLifecycleService(
        make_service(),
        repository,
        clock=lambda: NOW + timedelta(seconds=1),
        id_factory=lambda: PlanId("infeasible-plan"),
    )
    record = lifecycle.create(request)
    assert record.result.to_mapping()["status"] == "infeasible"
    assert repository.get(record.plan_id) == record


def test_lifecycle_get_does_not_recompute_or_reacquire_market() -> None:
    service = make_service()

    class CountingProvider:
        provider_id = "fixture"

        def __init__(self, batch):
            self.batch = batch
            self.calls = 0

        def acquire(self):
            self.calls += 1
            return self.batch

    provider = CountingProvider(service.providers[0].batch)
    guarded = type(service)(service.catalog, (provider,), clock=lambda: NOW)
    repository = InMemoryPlanRepository()
    lifecycle = PlanLifecycleService(
        guarded,
        repository,
        clock=lambda: NOW + timedelta(seconds=1),
        id_factory=lambda: PlanId("one"),
    )
    record = lifecycle.create(make_request())
    assert provider.calls == 1
    assert lifecycle.get(record.plan_id) == record
    assert lifecycle.list_recent() == (record,)
    assert provider.calls == 1


def test_lifecycle_history_does_not_change_when_market_changes() -> None:
    repository = InMemoryPlanRepository()
    first = PlanLifecycleService(
        make_service(milk_price="120"),
        repository,
        clock=lambda: NOW + timedelta(seconds=1),
        id_factory=lambda: PlanId("first"),
    ).create(make_request())
    second = PlanLifecycleService(
        make_service(milk_price="300"),
        repository,
        clock=lambda: NOW + timedelta(seconds=2),
        id_factory=lambda: PlanId("second"),
    ).create(make_request())

    assert first.result.to_mapping()["total_cost"]["amount"] == "310"
    assert second.result.to_mapping()["total_cost"]["amount"] == "490"
    restored_first = repository.get(PlanId("first"))
    assert restored_first is not None
    first_prices = [
        observation["price"]["amount"]
        for observation in restored_first.market_evidence.to_mapping()["batches"][0]["observations"]
    ]
    assert first_prices == ["120", "190"]


def test_lifecycle_rejects_creation_clock_before_market_capture() -> None:
    repository = InMemoryPlanRepository()
    lifecycle = PlanLifecycleService(
        make_service(),
        repository,
        clock=lambda: NOW - timedelta(seconds=1),
        id_factory=lambda: PlanId("bad-time"),
    )
    with pytest.raises(RuntimeError, match="precedes"):
        lifecycle.create(make_request())
    assert lifecycle.list_recent() == ()


def test_lifecycle_rejects_non_plan_id_factory_result() -> None:
    lifecycle = PlanLifecycleService(
        make_service(),
        InMemoryPlanRepository(),
        clock=lambda: NOW + timedelta(seconds=1),
        id_factory=lambda: "not-a-plan-id",  # type: ignore[return-value]
    )
    with pytest.raises(TypeError, match="PlanId"):
        lifecycle.create(make_request())


def test_duplicate_plan_id_is_rejected_before_market_acquisition() -> None:
    service = make_service()

    class CountingProvider:
        provider_id = "fixture"

        def __init__(self, batch):
            self.batch = batch
            self.calls = 0

        def acquire(self):
            self.calls += 1
            return self.batch

    provider = CountingProvider(service.providers[0].batch)
    guarded = type(service)(service.catalog, (provider,), clock=lambda: NOW)
    repository = InMemoryPlanRepository()
    existing = make_record(plan_id="same")
    repository.save(existing)
    lifecycle = PlanLifecycleService(
        guarded,
        repository,
        clock=lambda: NOW + timedelta(seconds=1),
        id_factory=lambda: PlanId("same"),
    )
    with pytest.raises(PlanRepositoryError, match="already exists"):
        lifecycle.create(make_request())
    assert provider.calls == 0


def test_file_repository_parallel_same_id_publishes_one_complete_record(tmp_path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    repository = FilePlanRepository(tmp_path)
    record = make_record(plan_id="parallel")

    def save_once() -> str:
        try:
            repository.save(record)
        except PlanRepositoryError:
            return "duplicate"
        return "saved"

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _: save_once(), range(8)))

    assert outcomes.count("saved") == 1
    assert outcomes.count("duplicate") == 7
    assert repository.get(record.plan_id) == record
    assert not list(tmp_path.glob("*.tmp"))


def test_plan_record_snapshot_schemas_are_closed() -> None:
    record = make_record()
    request = record.request.to_mapping()
    request["unexpected"] = True
    with pytest.raises(ValueError, match="invalid schema"):
        PlanRecord.create(
            plan_id=PlanId("schema-test"),
            created_at=record.created_at,
            request=request,
            result=record.result.to_mapping(),
            market_evidence=record.market_evidence.to_mapping(),
        )
