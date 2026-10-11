# M12.17 — Atomic household batch journal for mixed receipts

## Why this exists

M12.16 correctly stored a single immutable receipt intent, but the actual
household inventory was updated through **one JSON file per Item**. If the
process crashed after the first file, other HSM readers could see one received
product but not the other. The receipt was marked `pending_recovery`, but that
did not prevent incorrect transient household balances.

**M12.17 changes the publication unit.** A batch of 1–32 independently typed
household events now lives in one SQLite record with an associated unique
event-ID index. SQLite commits the record and all member IDs in a single
transaction. No household `get` or `history` reader sees a partial new batch.

This is an **inventory commit/visibility guarantee**, not a claim that all
independent files (plan, receipt intent, SQLite journal, receipt-completed
marker) commit in the same transaction, nor a Globus checkout integration.

## Storage model and compatibility

- `household/household-batches.sqlite3`: a local standard-library SQLite
  journal containing `batches(batch_id PRIMARY KEY, payload)` and
  `members(event_id PRIMARY KEY, batch_id)`. The batch row is a versioned
  integrity-digested serialization of *all* canonical `PurchaseEvent`
  payloads and their individual checksums. Entries are bounded to 32 events
  and 256 KiB per batch. Index and payload consistency are checked on reads.
- `HouseholdBatchJournal.writer()` uses `BEGIN IMMEDIATE` for cross-process
  serialization. Existing `FileHouseholdEventRepository.append()` uses the
  same writer lock and rejects an event ID already committed in a batch.
  `append_batch()` checks for collisions with legacy files and batches,
  publishes the entire batch or raises, and accepts exact idempotent replay
  of a previously committed identical batch.
- `FileHouseholdEventRepository.history()` and `get()` read **both**
  legacy single-event JSON files and committed batches. They still return
  the same `HouseholdEvent`/`HouseholdHistory` domain objects; projection,
  depletion, usual basket and household views need no new event types.
  Data corruption, duplicate IDs, or inconsistent batch membership fail
  closed. Local reset clears both old JSON events and the batch rows.
- `InMemoryHouseholdEventRepository` has equivalent create-only
  `append_batch` semantics for tests and local app components.

The DB lives next to existing local household events, not in any provider
or shared cloud store. No network call is made by this journal.

## Receipt confirmation sequence

1. The user explicitly confirms a receipt **against an already saved**
   typed mixed PlanRecord. The receipt includes actual packs, measured
   weighed mass, line prices, delivery, discount, total paid, and explicit
   price-change approval; all M12.16 safeguards still apply.
2. An immutable plan-keyed receipt **intent** is created. This pins a single
   receipt fingerprint across processes; it is not yet a stock mutation.
3. Build and preflight every deterministic `PurchaseEvent` against the
   ordinary household projection/depletion rules.
4. Submit the sorted events to one `append_batch("batch-" + plan_id, events)`
   transaction.
5. Only once that batch is visible in household history, write the
   separate receipt `completed` marker. The existing receipt status API
   reconciles the recorded Item IDs, actual mass and remaining deficits.

If execution stops **before** the SQLite commit, none of the new purchase
events appear; `GET /mixed/plans/<id>/receipt` returns
`pending_recovery`, with zero credited new Items. An exact same-receipt
retry can commit the batch.

If execution stops **after** the SQLite commit but before the receipt
completion marker, *all* purchase events are visible at once; status is
still `pending_recovery`, with **all** Items credited. Exact replay checks
the batch identity and writes the completion marker without a second stock
increase. Replacing the receipt ref, weights, prices or identifiers fails.

A pre-M12.17 receipt interrupted after partially writing legacy single
events is still recoverable through the old compatibility path. It cannot
retroactively obtain atomic visibility for its already-published partial
legacy records; this exception is restricted to existing legacy files.
**New receipt confirmations always use the atomic batch path.**

## Tests / failure boundaries

- New batch vs single-file history/get/stock projection.
- SQLite pre-commit reader isolation and transaction rollback.
- Restart with same database, exact replay and no duplicate balances.
- Concurrent appends of competing batch IDs with same events; only one
  may commit. Single-event append vs committed batch collision.
- Integrity tamper and index/payload disagreement fail closed.
- Local reset clears both event kinds.
- M12.16 receipt-level failure injected **before** and **after** batch commit:
  zero vs all new household events, then safe exact retry.
- Default mobile browser still cannot post private mixed receipts;
  normal offline/live Globus behavior stays unchanged.

## Important caveats

SQLite guarantees transaction atomicity for the batch journal on a supported
local filesystem. The implementation does not provide an atomic global
snapshot spanning unrelated simultaneously written legacy JSON records,
other storage directories, a nonlocal/misbehaving filesystem, or code
writing behind the repository's lock. User-defined events outside this
repository are not automatically serialized. Local data reset should be
performed with other writers paused; reset is not an all-filesystem
transaction.

The receipt is **human supplied**, not seller-signed or automatically checked
for authenticity. Public Globus listing pages still do not establish a
minimum/step for weighed purchasing. No live checkout, payment or
auto-purchase authority was enabled.
