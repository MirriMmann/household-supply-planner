# M12.13 — Weighted procurement lifecycle (governed pilot)

This is an opt-in end-to-end vertical proof for a **retailer-verified variable
weight sale**, not a claim that Globus has disclosed enough information to turn
its public `сом/кг` pages into confirmed HSM purchases.

## Authority gates

The normal `--live-globus` build exposes only the M12.12 read-only
`/catalog/weighted-candidates/estimate`. **It does not wire in**
`WeightedProcurementService` or `WeightedProcurementWebApi`. A trusted
integration must independently supply `VerifiedWeightSaleRule` (source-backed
minimum, increment, time), with a matching per-kg price observation. An API
caller cannot post or invent a sellability rule.

The separate governed pilot enforces:

1. A source-identity-matched price observation and sale rule, with configurable
   price/rule freshness TTL and hard currency/budget check.
2. Exact sellable mass = minimum + N × step, rounded **up** from the requested
   quantity; the rounded whole selected mass must fit the budget.
3. A bounded in-memory preview that is **not** durable or actionable. Only the
   explicit save step creates a durable immutable plan. A process restart before
   save discards authorization context; duplicate save after success replays
   the identical existing snapshot.
4. Immutable plan JSON under `weighted/plans`, typed re-validation and
   SHA-256 integrity digest. The digest detects accidental corruption; it is
   **not** a cryptographic signature or proof of seller authorization.
5. A separate explicit *actual purchase* stage. Require a receipt reference,
   observed positive actual mass and actual cost. Reject purchase quantity
   above the approved sellable mass or money above the approved upper bound,
   the remaining budget, or the wrong currency.
6. After explicit confirmation append a regular HSM `PurchaseEvent` (the
   **actual measured grams**, with no fictitious fixed SKU); persist a typed
   receipt snapshot and bind its item/mass/cost/reference to the purchase
   event source fingerprint. Idempotent retries do not duplicate inventory.
   Another event cannot complete the same single-line plan.
7. If household event persistence succeeded but receipt persistence failed,
   retry the same event/receipt to recover the missing record; conflicting
   receipt data fails closed. The test proves this boundary for offline data.
8. Existing fixed-package `/plans` and `/household/purchases` are not
   reinterpreted: independent bounded pilot data lives under `weighted`.

## Optional HTTP contract (injected/test harness ONLY)

Use `WeightedProcurementWebApi(service)` in `HouseholdWebJsonApi` only after
connecting a trusted sale-rule provider; it is **not** enabled by the normal
example assembly.

```http
POST /weighted/plans/preview
{"item_id":"onion","quantity":{"amount":"350","unit":"g"},"budget":{"amount":"25","currency":"KGS"}}
```

Returns a full non-persisted plan preview, including its generated `plan_id`,
exact observed price, verified seller minimum/step, selected sellable mass and
bounded estimated cost. Test values are *synthetic* examples only.

```http
POST /weighted/plans/confirm
{"plan_id":"<preview.plan.plan_id>"}
```

Returns the immutable saved plan. The user must explicitly confirm; saving is
not inferred from a GET or from fetching a price estimate.

```http
POST /weighted/plans/<id>/purchases
{"event_id":"receipt-onion-1",
 "actual_mass":{"amount":"375","unit":"g"},
 "actual_cost":{"amount":"19.50","currency":"KGS"},
 "receipt_ref":"receipt:external-identifier"}
```

Persists exact *reported* purchase mass and paid amount as separate facts. HSM
does not independently verify a submitted receipt reference. Live point-of-sale
integration or human verification remains a distinct source-authority step.

`GET /weighted/plans/<id>` reads immutable plan evidence, and regular
`GET /household/state` includes any explicitly confirmed weighed purchase.

## Proven and not proven

Synthetic regression tests cover preview, save, typed restart recovery,
quantity/price bound, conflicting receipts, second purchase rejection, budget,
missing attestation, tampering and the optional JSON API. Python CI and the
existing Chromium mobile gate serve as regression proof.

**Not live-ready:** the Globus public product HTML exposes a unit price but not
a verified min/step sale rule, therefore the default app cannot authorize or
store actual weighted purchase plans. There is no real checkout automation or
automatic user approval.

Follow-on integration before production:
- Acquire fresh *seller-authenticated* sale min/step and address-specific price.
- Decide whether variable weights are measured before or after checkout and
  model any checkout-price variance/refund/approval window.
- Add household stocktake UX for weighted-only canonical Item identities;
  currently ordinary stocktake demands a fixed-SKU catalog identity.
- Integrate weighted plans into general plan review/search and mobile shopping
  without conflating `Purchase.packs` with grams.
- Demonstrate a supervised live receipt and repeat purchase against a real
  store; only then promote items into recipe price planning.
