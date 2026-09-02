from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from household_supply.domain import Item, Quantity
from household_supply.household import (
    ConsumptionObservation,
    FileHouseholdEventRepository,
    HouseholdEventCorruptionError,
    HouseholdEventId,
    HouseholdEventRepositoryError,
    HouseholdLearningService,
    InMemoryHouseholdEventRepository,
    InventoryCorrection,
    PurchaseEvent,
    deserialize_household_event,
    serialize_household_event,
)


UTC = timezone.utc
BASE = datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


@pytest.fixture
def milk() -> Item:
    return Item("milk", "Milk", "dairy", ("молоко",))


def sample_events(milk: Item):
    return (
        PurchaseEvent(
            HouseholdEventId("purchase-1"),
            milk,
            Quantity("2", "l"),
            BASE,
            BASE,
            "milk-1l",
            "receipt-1",
        ),
        ConsumptionObservation(
            HouseholdEventId("consume-1"),
            milk,
            Quantity("500", "ml"),
            BASE,
            BASE + timedelta(days=1),
            BASE + timedelta(days=1),
            "manual observation",
        ),
        InventoryCorrection(
            HouseholdEventId("count-1"),
            milk,
            Quantity("1400", "ml"),
            BASE + timedelta(days=2),
            BASE + timedelta(days=2),
            "manual fridge count",
        ),
    )


@pytest.mark.parametrize("index", [0, 1, 2])
def test_event_serialization_round_trip(milk: Item, index: int) -> None:
    event = sample_events(milk)[index]
    assert deserialize_household_event(serialize_household_event(event)) == event


def test_event_serialization_is_key_order_independent(milk: Item) -> None:
    event = sample_events(milk)[0]
    payload = serialize_household_event(event)
    reordered = dict(reversed(tuple(payload.items())))
    assert deserialize_household_event(reordered) == event


def test_tampered_event_digest_is_rejected(milk: Item) -> None:
    payload = serialize_household_event(sample_events(milk)[0])
    payload["body"]["quantity"]["amount"] = "999"
    with pytest.raises(HouseholdEventCorruptionError, match="digest"):
        deserialize_household_event(payload)


def test_recomputed_digest_cannot_make_invalid_event_semantics_valid(milk: Item) -> None:
    # Construct invalid event through JSON shape: zero purchase. Recomputing the
    # digest is intentionally not a trust mechanism; semantic validation remains.
    payload = serialize_household_event(sample_events(milk)[0])
    payload["body"]["quantity"]["amount"] = "0"
    unsigned = dict(payload)
    del unsigned["digest"]
    from hashlib import sha256

    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    payload["digest"] = sha256(canonical).hexdigest()
    with pytest.raises(HouseholdEventCorruptionError, match="positive"):
        deserialize_household_event(payload)


def test_deserializer_rejects_unknown_fields(milk: Item) -> None:
    payload = serialize_household_event(sample_events(milk)[0])
    payload["surprise"] = True
    with pytest.raises(HouseholdEventCorruptionError, match="schema"):
        deserialize_household_event(payload)


def test_in_memory_repository_is_append_only(milk: Item) -> None:
    repo = InMemoryHouseholdEventRepository()
    event = sample_events(milk)[0]
    repo.append(event)
    assert repo.get(event.event_id) == event
    with pytest.raises(HouseholdEventRepositoryError, match="already exists"):
        repo.append(event)


def test_in_memory_history_is_recorded_time_ordered(milk: Item) -> None:
    first, second, _ = sample_events(milk)
    repo = InMemoryHouseholdEventRepository()
    repo.append(second)
    repo.append(first)
    assert repo.history().events == (first, second)


def test_file_repository_round_trip_and_history(tmp_path, milk: Item) -> None:
    repo = FileHouseholdEventRepository(tmp_path / "events")
    events = sample_events(milk)
    for event in reversed(events):
        repo.append(event)
    assert repo.get(events[0].event_id) == events[0]
    assert repo.history().events == events


def test_file_repository_never_overwrites_event_id(tmp_path, milk: Item) -> None:
    repo = FileHouseholdEventRepository(tmp_path / "events")
    event = sample_events(milk)[0]
    repo.append(event)
    with pytest.raises(HouseholdEventRepositoryError, match="already exists"):
        repo.append(event)
    assert repo.get(event.event_id) == event


def test_file_repository_rejects_corrupt_json(tmp_path, milk: Item) -> None:
    repo = FileHouseholdEventRepository(tmp_path / "events")
    event = sample_events(milk)[0]
    repo.append(event)
    (repo.root / f"{event.event_id}.json").write_text("not json", encoding="utf-8")
    with pytest.raises(HouseholdEventCorruptionError, match="UTF-8 JSON"):
        repo.get(event.event_id)


def test_file_repository_rejects_filename_identity_mismatch(tmp_path, milk: Item) -> None:
    repo = FileHouseholdEventRepository(tmp_path / "events")
    event = sample_events(milk)[0]
    payload = serialize_household_event(event)
    (repo.root / "other-id.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    with pytest.raises(HouseholdEventCorruptionError, match="filename"):
        repo.get(HouseholdEventId("other-id"))


def test_file_repository_rejects_oversized_record(tmp_path, milk: Item) -> None:
    repo = FileHouseholdEventRepository(tmp_path / "events", max_event_bytes=100)
    with pytest.raises(HouseholdEventRepositoryError, match="size limit"):
        repo.append(sample_events(milk)[0])


def test_file_repository_rejects_symlink_record_when_supported(tmp_path, milk: Item) -> None:
    repo = FileHouseholdEventRepository(tmp_path / "events")
    target = repo.root / "target.txt"
    target.write_text("x", encoding="utf-8")
    link = repo.root / "linked.json"
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(HouseholdEventCorruptionError, match="symbolic link"):
        repo.get(HouseholdEventId("linked"))


def test_parallel_same_event_id_has_one_winner(tmp_path, milk: Item) -> None:
    repo = FileHouseholdEventRepository(tmp_path / "events")
    event = sample_events(milk)[0]

    def attempt() -> str:
        try:
            repo.append(event)
            return "saved"
        except HouseholdEventRepositoryError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=8) as executor:
        outcomes = list(executor.map(lambda _: attempt(), range(8)))
    assert outcomes.count("saved") == 1
    assert outcomes.count("duplicate") == 7
    assert repo.get(event.event_id) == event


def test_learning_service_derives_state_and_recurring_source(tmp_path, milk: Item) -> None:
    service = HouseholdLearningService(FileHouseholdEventRepository(tmp_path / "events"))
    for event in sample_events(milk):
        service.record(event)
    state = service.state(as_of=BASE + timedelta(days=3))
    assert state.quantity_for("milk") == Quantity("1400", "ml")
    estimates = service.estimates(as_of=BASE + timedelta(days=3))
    assert len(estimates) == 1
    assert estimates[0].daily_quantity == Quantity("500.000000000000", "ml")
    source = service.recurring_need_source(
        source_id="next-week", horizon_days="7", as_of=BASE + timedelta(days=3)
    )
    assert source.emit_contributions()[0].quantity == Quantity(
        "3500.000000000000", "ml"
    )


def test_file_repository_survives_repository_restart(tmp_path, milk: Item) -> None:
    root = tmp_path / "events"
    first = FileHouseholdEventRepository(root)
    events = sample_events(milk)
    for event in events:
        first.append(event)
    restarted = FileHouseholdEventRepository(root)
    assert restarted.history().events == events
    assert restarted.get(events[1].event_id) == events[1]


def test_file_repository_rejects_duplicate_json_keys(tmp_path, milk: Item) -> None:
    repo = FileHouseholdEventRepository(tmp_path / "events")
    event = sample_events(milk)[0]
    repo.append(event)
    path = repo.root / f"{event.event_id}.json"
    raw = path.read_text(encoding="utf-8")
    # Duplicate a top-level key while retaining syntactically valid JSON.
    raw = raw.replace('{\n  "body"', '{\n  "schema_version": 1,\n  "body"', 1)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(HouseholdEventCorruptionError, match="UTF-8 JSON"):
        repo.get(event.event_id)


def test_file_repository_rejects_non_finite_json(tmp_path, milk: Item) -> None:
    repo = FileHouseholdEventRepository(tmp_path / "events")
    path = repo.root / "bad.json"
    path.write_text(
        '{"schema_version":NaN,"event_type":"purchase","event_id":"bad",'
        '"recorded_at":"2026-09-01T00:00:00+00:00","item":{},"body":{},'
        '"digest":"0000000000000000000000000000000000000000000000000000000000000000"}',
        encoding="utf-8",
    )
    with pytest.raises(HouseholdEventCorruptionError, match="UTF-8 JSON"):
        repo.get(HouseholdEventId("bad"))
