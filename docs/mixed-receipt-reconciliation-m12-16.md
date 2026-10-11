# M12.16 — Actual mixed receipt reconciliation and recoverable inventory posting

## Scope and authority

This milestone closes a **previously saved** mixed package+weighted plan
with a **household-operator supplied receipt**. It does not claim any Globus
checkout connection, retailer-signed receipts, automatic payment, or proof
that a public per-kilogram listing is orderable. The public addressless Globus
catalog still has no verified seller minimum/weight increment source, so the
standard offline and --live-globus product assemblies leave these endpoints
disabled. The mixed plan itself remains an opt-in governed pilot.

An actual purchased amount is a separate observation from the estimate:
each full-pack line has an actual **integer pack count**, each weighed line
an **actual measured mass**, and every line a **paid** amount. The receipt
also requires an external receipt reference, occurred_at, explicit fees,
discount, total_paid, and a boolean price_changes_approved. The latter must
be explicitly true to accept any line price above its planned amount or
nonzero delivery/service fees. Discounts cannot exceed the charged amount;
all amounts must be in the saved plan currency, and the final total must
equal **sum(line paid) + fees - discount**.

The final total is not allowed above the original approved mixed-plan budget.
Any amount above this budget is rejected and requires a **new authorized
plan/budget decision**, not a silent post hoc approval or invented retailer
quantity. This restriction is deliberately conservative for the pilot.

Every planned Item must appear once in the receipt. Actual pack count may
be less than the approved pack count; actual measured weight may be less
than the sellable weight. Neither may exceed its approved amount. Receipt
closure means *the received facts have been recorded*, not that the entire
recipe's original demand was covered: a short delivery remains short.

## Explicit private API

Only when a trusted private pilot injects MixedReceiptWebApi(service) into
HouseholdWebJsonApi, the following becomes available:

    POST /mixed/plans/mp-<id>/receipt
    {
      "receipt_ref": "operator-observed-receipt-001",
      "occurred_at": "2026-10-11T13:45:00+00:00",
      "lines": [
        {"kind":"fixed_package", "item_id":"rice","sku_id":"rice-1kg",
         "packs":1, "paid":{"amount":"95","currency":"KGS"}},
        {"kind":"weighted", "item_id":"onion",
         "actual_mass":{"amount":"290","unit":"g"},
         "paid":{"amount":"14.5","currency":"KGS"}}
      ],
      "delivery_and_fees":{"amount":"0.5","currency":"KGS"},
      "discount":{"amount":"0","currency":"KGS"},
      "total_paid":{"amount":"110","currency":"KGS"},
      "price_changes_approved":true
    }

    GET /mixed/plans/mp-<id>/receipt

The values above are synthetic test examples, **not actual Globus prices,
selling increments, receipts, or approved checkout values**. GET never makes
market requests or household changes. The receipt API never creates the
mixed plan; that plan must have been separately saved first.

## Durable state machine and failure policy

1. Re-open and typed-validate the **immutable mixed PlanRecord**. Validate
   item IDs, SKU catalog identity and pack sizes, exact expected receipt
   lines, quantities, cost, currency, and the approved total.
2. Canonicalize equivalent quantities (0.29 kg = 290 g) and monetary
   spellings (14.50 = 14.5); commit a **plan-keyed, create-only receipt
   intent** with a SHA-256 integrity digest. Two processes cannot claim
   different receipts for the same saved plan.
3. Preflight the complete set of deterministic future household PurchaseEvents
   through the same projection and depletion policy as ordinary events.
4. Append each real PurchaseEvent, with its canonical Item, true *received*
   quantity, pack SKU where applicable and source fingerprint binding the
   receipt intent. The full checkout amount/ref/fees live in the separate
   receipt, not a fictitious per-event purchase price.
5. After **all** expected events match the stored receipt, write a separate
   create-once completion marker linked to the receipt basis digest. Only
   then does GET return state=completed.
6. If a crash/interruption occurs after one or more events but before the
   completion marker, GET returns **pending_recovery** and the list of Items
   already credited. Re-posting the exact same receipt is an idempotent
   repair. Another receipt/amount/event cannot claim the same plan.

The current HouseholdEventRepository is one file **per event**, not a
transactional batch journal. As a result, some balances can become visible
to other household readers **before** the receipt is fully completed.
This milestone does not promise all-or-nothing visibility. It promises
explicit status and deterministic, conflict-detecting exact recovery.

SHA256 digests catch unintentional corruption and naive edits; they do not
authenticate a human receipt, make the store trustworthy, or protect
against an adversary who can rewrite the entire local repository.

## Verified via synthetic CI

Regressions cover saving a plan, a two-line real-quantity/paid-amount receipt,
normal household balance after posting, idempotent replay and restart,
equivalent g/kg and decimal currency inputs, missing or duplicate lines,
mass/pack over-authorization, unapproved price change, delivery fees,
discount/total/budget mismatches, conflicting receipt, interrupted second
line, exact recovery, tampered persistence, and disabled-by-default HTTP.
Existing Python 3.11/3.13 and mobile Chromium smoke gates remain in place.

## Still out of scope

- Authenticated retailer checkout APIs, receipt OCR and automatic extraction.
- Verified real Globus weighed selling rules (minimum, step), address-specific
  availability, delivery/fee quoting, and third-party seller confirmation.
- Truly atomic multi-Item inventory visibility across all household readers.
  That requires a transaction-aware event journal/projection version.
- Seller refunds, returns, substitutions, cancellation, multi-receipt
  deliveries, and explicit budget reauthorization of overruns.
- UI for reviewing scanned receipts and matching receipt Item identities.

Do not promote live weighted purchases until the required seller evidence
and purchase/receipt authorization have been established.
