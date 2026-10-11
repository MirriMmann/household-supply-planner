"""SQLite-backed all-or-nothing publication of grouped household events.

The row containing *all* events and its event-ID index commit in one SQLite
transaction. A reader never sees a subset of a new batch. Existing single
event JSON records retain their original representation and API semantics.
"""

from __future__ import annotations

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from .events import HouseholdEvent, HouseholdEventId


class BatchJournalError(RuntimeError):
    """The local batch journal cannot read, commit, or verify a batch."""


class BatchJournalConflict(BatchJournalError):
    """An event or batch identifier already belongs to different evidence."""


def _canonical(data: dict) -> str:
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def _strict(pairs):
    obj = {}
    for key, val in pairs:
        if key in obj:
            raise ValueError("duplicate batch JSON key")
        obj[key] = val
    return obj


class HouseholdBatchJournal:
    """Transaction-bound events stored in one atomic SQLite row per batch.

    All local repository writes (including legacy file append) use a SQLite
    BEGIN IMMEDIATE mutex. It is held only for the short commit operation, not
    for projecting household state. This guards cross-process identifier races
    when all writers use this repository implementation.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / "household-batches.sqlite3"
        if self.path.is_symlink():
            raise BatchJournalError("batch database cannot be a symbolic link")
        try:
            with self.connection() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS batches ("
                    "batch_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS members ("
                    "event_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL "
                    "REFERENCES batches(batch_id))"
                )
        except sqlite3.DatabaseError as exc:
            raise BatchJournalError("cannot initialize household batch journal") from exc

    def _connect(self) -> sqlite3.Connection:
        if self.path.is_symlink():
            raise BatchJournalError("batch database cannot be a symbolic link")
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def writer(self) -> Iterator[sqlite3.Connection]:
        try:
            with self.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                else:
                    conn.execute("COMMIT")
        except sqlite3.DatabaseError as exc:
            raise BatchJournalError("batch transaction failed") from exc

    @staticmethod
    def _encode(batch_id: str, events: tuple[HouseholdEvent, ...]) -> str:
        from .persistence import serialize_household_event

        unsigned = {
            "schema_version": 1,
            "batch_id": batch_id,
            "events": [serialize_household_event(event) for event in events],
        }
        return _canonical({**unsigned, "digest": sha256(
            _canonical(unsigned).encode("utf-8")
        ).hexdigest()})

    @staticmethod
    def _decode(payload: str, *, expected_id: str | None = None):
        from .persistence import deserialize_household_event

        try:
            value = json.loads(
                payload, object_pairs_hook=_strict,
                parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)),
            )
            if not isinstance(value, dict) or set(value) != {
                "schema_version", "batch_id", "events", "digest",
            }:
                raise ValueError("invalid batch envelope")
            if type(value["schema_version"]) is not int or value["schema_version"] != 1:
                raise ValueError("unsupported batch version")
            batch_id = HouseholdEventId(value["batch_id"]).value
            if expected_id is not None and batch_id != expected_id:
                raise ValueError("batch ID does not match stored key")
            unsigned = {
                "schema_version": 1, "batch_id": batch_id, "events": value["events"],
            }
            if sha256(_canonical(unsigned).encode("utf-8")).hexdigest() != value["digest"]:
                raise ValueError("batch checksum mismatch")
            if not isinstance(value["events"], list) or not 1 <= len(value["events"]) <= 32:
                raise ValueError("batch event count invalid")
            events = tuple(deserialize_household_event(event) for event in value["events"])
            ids = [event.event_id for event in events]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate event ID in batch")
            if ids != sorted(ids):
                raise ValueError("batch events must be sorted by event ID")
            return events
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as exc:
            raise BatchJournalError("stored batch is invalid or corrupted") from exc

    def append_batch(
        self,
        batch_id: str,
        events: tuple[HouseholdEvent, ...],
    ) -> None:
        batch_id = HouseholdEventId(batch_id).value
        events = tuple(events)
        if not 1 <= len(events) <= 32:
            raise BatchJournalConflict("batch must contain 1-32 household events")
        if any(not isinstance(e.event_id, HouseholdEventId) for e in events):
            raise BatchJournalConflict("batch contains invalid event identities")
        ids = [event.event_id for event in events]
        if ids != sorted(set(ids)):
            raise BatchJournalConflict("batch event identities must be sorted and unique")
        encoded = self._encode(batch_id, events)
        if len(encoded.encode("utf-8")) > 256 * 1024:
            raise BatchJournalConflict("batch exceeds journal size bound")

        with self.writer() as conn:
            current = conn.execute(
                "SELECT payload FROM batches WHERE batch_id=?", (batch_id,)
            ).fetchone()
            if current is not None:
                existing = self._decode(current[0], expected_id=batch_id)
                if existing != events:
                    raise BatchJournalConflict("batch ID already committed with different events")
                self._check_index(conn, batch_id, existing)
                return
            for event in events:
                # Legacy writes participate in the same SQLite writer lock
                # and cannot race this existence check within this repository.
                if (self.root / f"{event.event_id.value}.json").exists():
                    raise BatchJournalConflict("batch event collides with existing single event")
                other = conn.execute(
                    "SELECT batch_id FROM members WHERE event_id=?",
                    (event.event_id.value,),
                ).fetchone()
                if other is not None:
                    raise BatchJournalConflict("household event already belongs to another batch")
            conn.execute(
                "INSERT INTO batches(batch_id, payload) VALUES(?,?)", (batch_id, encoded)
            )
            for event in events:
                conn.execute(
                    "INSERT INTO members(event_id,batch_id) VALUES(?,?)",
                    (event.event_id.value, batch_id),
                )

    @staticmethod
    def _check_index(conn, batch_id, events):
        stored = conn.execute(
            "SELECT event_id FROM members WHERE batch_id=? ORDER BY event_id",
            (batch_id,),
        ).fetchall()
        if [e[0] for e in stored] != [e.event_id.value for e in events]:
            raise BatchJournalError("batch event index inconsistent with payload")

    def member_exists(self, event_id: HouseholdEventId, *, conn=None) -> bool:
        if conn is not None:
            return conn.execute(
                "SELECT 1 FROM members WHERE event_id=?", (event_id.value,)
            ).fetchone() is not None
        try:
            with self.connection() as connection:
                return self.member_exists(event_id, conn=connection)
        except sqlite3.DatabaseError as exc:
            raise BatchJournalError("cannot query batch event index") from exc

    def get(self, event_id: HouseholdEventId) -> HouseholdEvent | None:
        try:
            with self.connection() as conn:
                found = conn.execute(
                    "SELECT b.batch_id, b.payload FROM batches b "
                    "JOIN members m ON m.batch_id=b.batch_id "
                    "WHERE m.event_id=?", (event_id.value,),
                ).fetchone()
                if found is None:
                    return None
                events = self._decode(found[1], expected_id=found[0])
                self._check_index(conn, found[0], events)
                return next(
                    (event for event in events if event.event_id == event_id),
                    None,
                ) or self._missing_member()
        except sqlite3.DatabaseError as exc:
            raise BatchJournalError("cannot read batch event") from exc

    @staticmethod
    def _missing_member():
        raise BatchJournalError("batch member index points outside event payload")

    def events(self) -> tuple[HouseholdEvent, ...]:
        try:
            with self.connection() as conn:
                rows = conn.execute(
                    "SELECT batch_id, payload FROM batches ORDER BY batch_id"
                ).fetchall()
                events = []
                for batch_id, payload in rows:
                    decoded = self._decode(payload, expected_id=batch_id)
                    self._check_index(conn, batch_id, decoded)
                    events.extend(decoded)
                return tuple(events)
        except sqlite3.DatabaseError as exc:
            raise BatchJournalError("cannot enumerate committed batch events") from exc

    def clear(self) -> int:
        with self.writer() as conn:
            count = conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
            conn.execute("DELETE FROM members")
            conn.execute("DELETE FROM batches")
            return count
