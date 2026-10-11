# M12.14 — Shared shopping readiness: full recipes and weighed groceries

This is a **read-only shared needs view** for the existing household inventory
and the M12.8–M12.13 recipe/weight infrastructure. It **does not** claim live
Globus checkout support for weighed goods, nor does it change the immutable
fixed-pack plan or the guarded weighted-plan authorization process.

## Retailer verification and boundary

Official Globus public product pages, e.g.
[onion](https://globus-online.kg/ru-kg/good/9ca7b9b184f14fe3af97d319a64dd364000200010001)
and [potato](https://globus-online.kg/ru-kg/good/268f8c2e69184dc5834205503a1c53f1000100010000),
show a **price reference per kilogram** in an **addressless demo catalog**.
They do **not** provide reliably attributable minimum order weight, step
increment, address-specific checkout or received final mass in the public
extracted product page. We did not invent them or use a 1kg fixed package.

The M12.13 guarded weighted procurement pilot is valid only with an explicitly
injected trusted `VerifiedWeightSaleRule`; it is not auto-enabled by public
Globus listings. Price estimates have no purchasing authority.

## Unified discovery and preview endpoints

`GET /shopping/recipe-candidates` lists all five authored Kyrgyz home
variations — including the four dishes not yet price-plannable. These are
*recipe definitions*, not a separate set of purchasable retailer products.
The existing `GET /recipes` and `/recipes/{id}/quote` remain catalog-gated;
a blocked dish cannot silently become a partial fixed-pack shopping plan.

`POST /shopping/preview` accepts **exactly one** of:

```json
{"recipe_id": "kg-kuurdak-beef"}
```

or

```json
{"demands":[
  {"item_id":"onion","quantity":{"amount":"0.35","unit":"kg"}},
  {"item_id":"rice","quantity":{"amount":"160","unit":"g"}}
]}
```

No client-supplied market prices, stock balances or sale min/step rules. The
response derives current `HouseholdState` itself and contains:

- Full requested quantities (including original meat/vegetables), in base
  units; duplicate Items are summed before subtracting stock.
- Exact recorded on-hand quantities if observed; **unknown stays null**;
  incompatible unit state is surfaced rather than silently corrected.
- Exact *known* shortfalls, `stock_status` and `review_status` per Item.
- Source-backed class `fixed_package`, `weighted_reference`,
  `unsupported_units` or `unsupported_item`.
- A read-only official Globus source reference for weighted candidates;
  not a purchased-SKU identity and not a checkout guarantee.
- Explicit blockers: `inventory_unverified`,
  `weighted_sale_rule_unverified`, `catalog_unsupported` or a quantity
  mismatch.
- **`can_save_as_plan=false` always**. This preview intentionally has no
  partial-save path; the existing separately validated plan endpoints still
  control purchasing authority. An all-covered recipe sets
  `inventory_ready=true` without pretending a purchase was made.

There is no retailer request in `/shopping/preview`. The browser offers an
additional explicit **Check weight reference price** on known weighted
shortfalls, invoking the existing M12.12 live source-bound quote in
`--live-globus` only. That quote does **not** clear a sale-rule blocker or
create a persisted plan.

## Mobile/local UI

The existing shopping view now has **«Общий список потребностей — блюда и
весовые товары»** with a recipe selector or an individual weighed-grocery
mass. It shows the full read-only list, recorded stock, shortfalls, and why
purchase authorization remains blocked. Even the offline demo can review
registered weighed needs and unknown stock without going online.

No automatic bulk market requests, stock writes, plans, purchase events or
unjustified substitute IDs arise from browsing and reviewing this surface.

## Gate and next evidence

Tests cover all five recipes, unknown vs short stock, g/kg normalization,
full recipe ingredient set preservation, manual mixed needs, duplicate
aggregation, invalid data, no phantom persisted plan or purchases. Python CI
3.11/3.13 and mobile Chromium validate regression behavior.

The next step toward a **real combined purchasable plan** is a retailer-verified
sale-rule/checkout source and a single atomic mixed `PlanRecord` with
price, package, weight, budget, actual receipt adjustment and replay semantics.
This cannot be inferred from Globus's public demo HTML.
