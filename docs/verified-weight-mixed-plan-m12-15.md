# M12.15 — verified weight rules and one mixed plan authority (opt-in pilot)

## Grounded retailer evidence (2026-10-11)

Public official Globus Online [onion product page](https://globus-online.kg/ru-kg/good/9ca7b9b184f14fe3af97d319a64dd364000200010001)
quotes a **per-kilogram** reference price. This is a public **addressless
demo catalog**, not the address-specific, orderable stock or the seller's
minimum weight/increment. The public price surface does **not** prove whether
350g or 400g can actually be ordered.

The retailer's own [License Agreement](https://globus-online.kg/ru-kg/about/l/licenseagreement)
(section «Общие положения») explains that the actual cost of goods sold by
weight is calculated from their **actual mass** and reflected in the cash
receipt, so it can differ from the checkout estimate. Replacement/price
changes may require the customer's approval.

**Therefore this milestone does NOT fabricate a confirmed Globus rule source.**
The public Globus integration continues to advertise
`seller_weight_rules_verified=false` and `mixed_plan_enabled=false`.
No user-supplied min/step is admitted via HTTP. A separate trusted, audited
`sale_rules(item_id, source_ref)` injection point is the only authority to
provide a `VerifiedWeightSaleRule`, and its source identity and timestamp
are rechecked together with the price.

## New typed, separately persisted `MixedPlanRecord`

Unlike the existing package-only immutable `PlanRecord`, this is a distinct
typed v1 record, not a mutation of the legacy schema. It models a **single
full demand and budget decision**, containing:

- `MixedDemandLine`: each verified on-hand shortage with exact original
  required quantity, measured stock and missing amount.
- `MixedPackLine`: concrete selected Offer/SKU, whole-pack count, observed
  seller/reference, package mass or volume, item and extended cost.
- `MixedWeightLine`: validated `WeightedPlanRecord` with real per-kg price
  observation and separately attested seller minimum/step. The sellable mass
  is rounded **up**, not assumed to be a 1kg package.
- One `estimated_total` under one shared merchandise-only budget, validated
  against **all** line prices and purchased quantities.
- Current household event IDs as an immutable decision basis; a preview cannot
  be saved if home-stock facts changed in the meantime.
- A create-only SHA256 integrity-checked file `mixed-plans/<id>.json`,
  with strict JSON decoding, typed schema/reconstruction, and idempotent retry.

All weighted amounts are priced **before** the packaged subset is optimized.
The package planner receives only the remaining budget and must cover **all**
its deficits, otherwise the mixed preview fails closed. No partial saved
packaged plan is created by this new service.

No client-supplied price enters a planning decision. In a missing-rule case,
the service never calls the fixed-pack planner to produce a misleading
partial result.

## Optional pilot endpoints

An explicitly injected `MixedPlanWebApi` provides:

```http
POST /mixed/plans/preview
{"budget":{"amount":"110","currency":"KGS"},
 "demands":[
   {"item_id":"onion","quantity":{"amount":"350","unit":"g"}},
   {"item_id":"rice","quantity":{"amount":"200","unit":"g"}}
 ]}
```

or `{"budget":...,"recipe_id":"kg-manty-beef"}`. The service requires
**recorded on-hand balances for every ingredient**, never assumes unknown
stock to be zero. The returned mixed plan preview has **no durable side
effects**.

```http
POST /mixed/plans/confirm
{"plan_id":"mp-<id from preview>"}

GET /mixed/plans/mp-<id>
```

Confirm saves **the immutable plan snapshot only**, not a purchase or seller
checkout. The response always explicitly says `checkout_confirmed=false`.
There is no endpoint that can turn a weighted reference price into a seller
approval or automatically write a purchase event.

All ordinary `--live-globus` and offline app instances leave the mixed
plan API *disconnected*; an organization must provide separately verified
seller rules and explicitly configure a private pilot.

## Proven boundaries and remaining work

The tests use a clearly **synthetic sale rule** (onion minimum 300g, step
100g), while prices and stock are obtained through controlled providers.
Tests assert full mixed package+weight line coverage, shared-budget accounting,
explicit confirmation, immutable restore, missing rule, unknown stock,
post-preview stock changes, overbudget, file integrity, disabled live routes,
and no ghost ordinary HSM plans/events.

**Not yet delivered:** external Globus checkout, real authoritative seller
min/step, delivery/service fees, paid receipt reconciliation, actual weighed
mass changes, stock mutation from a completed mixed checkout, mixed-plan
search in the ordinary plan history, and a fully production-atomic batch
purchase completion. In particular, `estimated_total` is a **merchandise
estimate** and cannot be presented as an all-in hard checkout ceiling while
delivery fees and receipt-level mass variation remain unknown.

This implementation is the correct bounded plan representation for those
future integrations, not a claim that those integrations are already live.
