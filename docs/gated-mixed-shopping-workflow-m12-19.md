# M12.19 — Governed end-to-end mixed shopping workflow

## Source verification, 2026-10-11

Official Globus Online catalog: https://globus-online.kg/ru-kg/catalog/grocery

The public catalog explicitly identifies itself as a **demo catalog** until
the shopper supplies a delivery address. Official weighted listings show
amounts in **KGS/kg** (e.g. the Globus Buffet listings on
https://globus-online.kg/ru-kg/catalog/grocery/category/348c9d78254711c6ca50b3e6e2ce86d6).
We searched the official public site for an independently verifiable
seller-wide or item-specific **minimum order mass**, **mass increment**, or
authenticated checkout rule and could not establish any. These pages do
NOT provide authority to sell arbitrary grams.

The actual weight/cost at checkout is distinct from the public price
estimate. An external seller checkout connector and authenticated order
evidence are **not available** in this project. Nothing in this milestone
makes per-kg prices into fake fixed-size packages or allows purchases from
the public demo catalog.

An injected `VerifiedWeightSaleRule` is the only supported route to an
orderable mass; such a rule must be supplied by a separately trusted
integration, with item/source ID and freshness checks already implemented
in M12.15. **The synthetic rule (minimum 300g/step 100g) used in automated
tests is not a Globus fact**, nor is the public source URL a standalone
attestation of minimum and increment. Before a real rollout, acquire an
authoritative, address-scoped seller API/checkout rule and independent
source-provenance/acceptance gates; otherwise leave the full path off.

## Product experience (private pilot only)

1. Shopping → «Общий список потребностей — блюда и весовые товары»:
   Kyrgyz candidate recipes remain fully visible and read-only, even when
   some ingredients are not purchasable. A private-gated button can
   transfer the selected recipe to a new mixed shopping panel.
2. In the new «Единая закупка» panel, choose either an entire dish or
   a **manual 1–32 Item** shopping set using canonical catalog and
   weighted Item IDs. Enter exact quantities/units and a total **KGS
   merchandise budget**. Unknown on-hand stock is not zero.
3. Press «Рассчитать весь смешанный план». The UI sends ONLY the
   selected recipe ID or explicit Item demands plus budget to the
   existing `POST /mixed/plans/preview`. The backend reacquires
   market prices, verifies seller-weight rule authority, compares
   household observations, and rejects incomplete/budget-exceeding
   allocations. No client-supplied price, household balance, source
   reference, mass step, or retailer checkout permission is admitted.
4. The preliminary result displays every picked whole-pack SKU,
   sellable mass, observed weighted quote, source identities,
   min/step evidence supplied by the private backend, item costs,
   total merchandise estimate and server preview expiry. This is NOT
   a purchase or an all-in checkout total.
5. Editing ANY shopping assumption invalidates the preview and
   disables confirmation. A separate operator acknowledgment allows
   `POST /mixed/plans/confirm` to **save an immutable plan only**.
   On lost HTTP response the exact ID is queried for possible
   successful save, rather than generating another plan by guess.
6. A saved plan can be reopened by ID. «После покупки сверить
   фактический чек» moves to the M12.18 History receipt editor
   and opens the exact plan ID. Only a separate actual-receipt
   preview + final received-goods consent ultimately updates inventory,
   via M12.17's atomic SQLite event journal.

The protected panel's visibility requires **both** a trusted-injected
`MixedPlanWebApi` and a `MixedReceiptWebApi`. The default demo and
`--live-globus` builds do not inject either. Their existing
`GET /catalog/weighted-candidates` flags keep the action hidden, and
unauthorized mixed URLs respond 404. A static same-origin script is
not an authority grant.

## Chromium acceptance

An isolated loopback-only E2E fixture constructs synthetic market and
weight rule providers. Selenium at 390 × 844:

- Edits manual onion 0.35kg + rice 0.2kg and shared 110 KGS budget.
- Asserts a mixed preview has no household events or saved purchase.
- Alters the budget, proves consent is invalidated, restores the
  amount and requires a fresh preview.
- Explicitly saves the complete mixed plan, verifies it can be
  loaded via its new ID and household inventory is still unchanged.
- Clicks direct handoff to the receipt review, enters actual onion
  290g against the required 300g plus one rice pack, and sees 10g
  still missing.
- Asserts receipt preview remains read-only; edits after review must
  revalidate; separate final consent publishes the whole batch.
- Reloads the previously saved receipt and proves zero duplicate
  household purchase events.

Existing Python 3.11/3.13 and mobile smoke suites remain in force.
A failure to acquire trustworthy rules or market prices is a blocked
plan, not a partially saved basket or an implicit purchase.

## Remaining work for real Globus usage

- Address-scoped seller item catalog and order availability.
- Independently verified authoritative per-Item mass minimum/step
  and their source/expiry/revocation mechanism.
- Delivery/service fees and total checkout authorization.
- Seller-confirmed order status and authenticated cash receipt,
  including substitutions and returns.
- A local operator setting and secure credentials for live checkout
  with explicit human purchase authorization (not delegated to a
  public product listing or a preexisting saved plan).

Do not enable real Globus mixed purchase workflow in a default build
until these facts and permissions are proven. The pilot UI is **not**
a workaround to those missing facts.
