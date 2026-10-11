# M12.18 — Explicit receipt review UI (private pilot)

## User workflow

The **History** tab contains a hidden-by-default disclosure:
**«Сверить фактический чек — закрытый пилот»**. It is enabled only when
`HouseholdWebJsonApi` has BOTH a manually injected `MixedPlanWebApi`
and `MixedReceiptWebApi`. The default offline and `--live-globus`
assemblies do **not** expose these capabilities or allow receipt posting.
The static JavaScript asset may be served, but the UI stays hidden and the
server routes return 404 when the service is not connected.

1. Enter the ID of a **previously saved** typed mixed plan, e.g.
   `mp-<id>`. Loading queries `GET /mixed/plans/<id>` and
   `GET /mixed/plans/<id>/receipt`. No stock mutation occurs.
2. If the receipt is not already claimed, the operator checks every planned
   packaged SKU's **actual number of packs** and every weighted Item's
   **actual received mass in g/kg**, plus real paid amount for each line.
   Enter a human-readable original receipt identifier, purchase time,
   delivery/service fees, discount and final paid total. Price rises and
   paid delivery fees need a separate explicit checkbox. The resulting
   overall charged total still cannot exceed the approved plan budget.
3. Press **«Проверить чек без записи запасов»**. New private endpoint
   `POST /mixed/plans/<id>/receipt/preview` executes the same typed
   validation as the final confirmation, but never claims the receipt,
   publishes events or changes inventory. The response includes
   `validated=true`, `persisted=false`,
   `inventory_updated=false`, each line's shortfall after actual
   delivery, the actual overall paid amount, hard budget and remainder.
4. The operator reads the reviewed details, marks a **second separate
   received-goods consent**, and explicitly chooses
   **«Подтвердить получение и обновить запасы»**. Only then does
   `POST /mixed/plans/<id>/receipt` claim the unique immutable receipt
   and publish all actual inventory events in the M12.17 SQLite batch.
5. Editing any draft field **invalidates the earlier preview and disables
   final confirmation**. The confirmation sends exactly the reviewed JSON
   payload, never a silently resynthesized quantity or price. A network
   error after sending confirmation hides the editable form until the
   user reloads server status: a lost response may mean a completed
   commit, so the UI never encourages an alternative second receipt.
6. If receipt status is already **completed**, the view is read-only.
   If **pending_recovery**, a separate opt-in action
   `POST /mixed/plans/<id>/receipt/retry`
   with `{"confirmation":"RETRY"}` recompletes the **same immutable
   stored receipt**, without asking the user to retype potentially
   ambiguous values (microsecond timestamps, decimal representation,
   receipt identity).

## Testing

Backend tests cover side-effect-free repeated preview, missing receipt
lines, overlarge weighted mass, incorrect totals, server permission,
conflicting receipt references, exact receipt replay, explicit retry,
and no accidental enabling in default app.

Mobile Chromium 390×844 launches a test-only loopback private fixture.
It uses **synthetic** seller rules (onion minimum 300 g / increment 100 g)
and a synthetic 50 KGS/kg listing; these are NEVER connected to production
Globus. The test enters an actual 290 g delivery and associated line
price, checks a remaining 10 g shortage, asserts **zero newly posted
inventory events at preview**, asserts the second consent is required,
then commits and checks both household events appear exactly once and
survive reentry.

## Boundaries

- This is an **operator-attested receipt**, not scanned OCR, retailer
  authentication, a seller-confirmed checkout, or payment processing.
- The original planned item/SKU identity is immutable. An actual
  substitution, removed item, returned item, refund, split delivery or
  receipt above the budget requires a separate governed flow; the UI
  does **not** silently reinterpret it as a valid receipt.
- The UI uses the server as the single source of validation, totals,
  trusted seller-rule provenance and purchase permissions; it never
  grants itself store authorization.
- The M12.17 atomicity guarantee is about publishing the **entire batch
  of inventory events**. Receipt claim/completion metadata lives in
  separate persistence and can be recovered by exact replay.
- Default product modes remain read-only for unverified weighted selling
  rules from the public addressless Globus catalog.
