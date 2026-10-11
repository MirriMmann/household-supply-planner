"""M12.17: committed multi-event household batches are indivisible to readers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3

import pytest

from household_supply.domain import Item, Quantity
from household_supply.household import (
    FileHouseholdEventRepository, HouseholdEventId,
    HouseholdEventRepositoryError, InMemoryHouseholdEventRepository,
    PurchaseEvent,
)
from household_supply.household.batch_journal import BatchJournalError


NOW = datetime(2026, 10, 11, 15, tzinfo=timezone.utc)


def purchase(name: str) -> PurchaseEvent:
    return PurchaseEvent(
        event_id=HouseholdEventId("batch-event-" + name),
        item=Item(name, name.title(), "test_grocery"),
        quantity=Quantity("100", "g"),
        occurred_at=NOW, recorded_at=NOW,
        source_ref="batch-unit-test",
    )


def test_file_batch_creates_both_events_in_one_committed_history(tmp_path):
    repo = FileHouseholdEventRepository(tmp_path / "household")
    a, b = purchase("carrot"), purchase("onion")
    assert repo.history().events == ()
    repo.append_batch("batch-purchase-1", (a, b))
    assert repo.history().events == (a, b)
    assert repo.get(a.event_id) == a
    assert repo.get(b.event_id) == b
    assert list(repo.root.glob("*.json")) == []  # Not two separate files.
    assert len(list(repo.root.glob("*.sqlite3"))) == 1
    reopened = FileHouseholdEventRepository(repo.root)
    assert reopened.history().events == (a, b)
    assert reopened.get(b.event_id) == b
    # Retrying identical committed evidence does not change inventory.
    reopened.append_batch("batch-purchase-1", (a, b))
    assert reopened.history().events == (a, b)


def test_uncommitted_batch_index_and_rows_never_reach_household_readers(tmp_path):
    repo = FileHouseholdEventRepository(tmp_path / "household")
    a, b = purchase("carrot"), purchase("onion")
    batch = repo._batch_journal
    with batch.writer() as conn:
        content = batch._encode("batch-hold", (a, b))
        conn.execute(
            "INSERT INTO batches(batch_id,payload) VALUES(?,?)",
            ("batch-hold", content),
        )
        conn.execute(
            "INSERT INTO members(event_id,batch_id) VALUES(?,?)",
            (a.event_id.value, "batch-hold"),
        )
        # Reader sees ZERO events until the entire group and event ID index
        # commit. It never sees the one member already inserted.
        reader = FileHouseholdEventRepository(repo.root)
        assert reader.history().events == ()
        assert reader.get(a.event_id) is None
        conn.execute(
            "INSERT INTO members(event_id,batch_id) VALUES(?,?)",
            (b.event_id.value, "batch-hold"),
        )
        assert reader.history().events == ()
    assert repo.history().events == (a, b)


def test_batch_rollback_does_not_publish_half_a_purchase(tmp_path):
    repo = FileHouseholdEventRepository(tmp_path / "household")
    a, b = purchase("carrot"), purchase("onion")
    journal = repo._batch_journal
    with pytest.raises(RuntimeError, match="failure before commit"):
        with journal.writer() as conn:
            conn.execute(
                "INSERT INTO batches(batch_id,payload) VALUES(?,?)",
                ("batch-rollback", journal._encode("batch-rollback", (a, b))),
            )
            conn.execute(
                "INSERT INTO members(event_id,batch_id) VALUES(?,?)",
                (a.event_id.value, "batch-rollback"),
            )
            raise RuntimeError("failure before commit")
    assert repo.history().events == ()
    repo.append_batch("batch-rollback", (a, b))
    assert len(repo.history().events) == 2


def test_single_legacy_events_and_batches_coexist_and_reject_collisions(tmp_path):
    repo = FileHouseholdEventRepository(tmp_path / "household")
    a, b, c = purchase("carrot"), purchase("onion"), purchase("potato")
    repo.append(a)
    with pytest.raises(HouseholdEventRepositoryError, match="single event"):
        repo.append_batch("batch-collision", (a, b))
    repo.append_batch("batch-only", (b, c))
    with pytest.raises(HouseholdEventRepositoryError, match="already exists in batch"):
        repo.append(b)
    assert repo.history().events == (a, b, c)
    assert repo.get(a.event_id) == a
    assert repo.get(b.event_id) == b
    assert len(list(repo.root.glob("*.json"))) == 1
    assert repo.clear() == 3
    assert repo.history().events == ()
    assert repo.get(a.event_id) is None
    assert repo.get(b.event_id) is None


def test_two_process_style_concurrent_claims_of_same_event_only_one_succeeds(tmp_path):
    root = tmp_path / "household"
    one = FileHouseholdEventRepository(root)
    two = FileHouseholdEventRepository(root)
    a, b = purchase("carrot"), purchase("onion")

    def worker(repo, batch_id):
        try:
            repo.append_batch(batch_id, (a, b))
            return "committed"
        except HouseholdEventRepositoryError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(
            lambda args: worker(*args),
            [(one, "batch-worker-a"), (two, "batch-worker-b")],
        ))
    assert sorted(outcomes) == ["committed", "conflict"]
    assert len(one.history().events) == 2
    assert len(two.history().events) == 2


def test_atomic_batch_corruption_fails_closed_and_cannot_be_used_for_stock(tmp_path):
    repo = FileHouseholdEventRepository(tmp_path / "household")
    a, b = purchase("carrot"), purchase("onion")
    repo.append_batch("batch-corrupt", (a, b))
    with sqlite3.connect(repo._batch_journal.path) as conn:
        row = conn.execute(
            "SELECT payload FROM batches WHERE batch_id='batch-corrupt'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE batches SET payload=? WHERE batch_id='batch-corrupt'",
            (row.replace("100", "900"),),
        )
    with pytest.raises(HouseholdEventRepositoryError):
        repo.history()
    with pytest.raises(HouseholdEventRepositoryError):
        repo.get(a.event_id)


def test_in_memory_batch_publishes_all_or_none(tmp_path):
    repo = InMemoryHouseholdEventRepository()
    a, b = purchase("carrot"), purchase("onion")
    repo.append_batch("batch-memory", (a, b))
    assert repo.history().events == (a, b)
    with pytest.raises(HouseholdEventRepositoryError):
        repo.append_batch("batch-other", (a, b))
    assert repo.history().events == (a, b)
