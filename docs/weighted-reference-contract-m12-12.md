# M12.12 — Weighted unit-price acquisition and strict sellability fence

## Scope shipped in this experimental branch

- The live Globus build can fetch the **one explicitly selected** weight-listed
  product and parse an observed, source-backed **som/kg** reference rate.
- A deterministic **read-only reference estimate** multiplies requested mass by
  that unit rate. It uses exact rational arithmetic and **rounds up to a cent**
  as a conservative displayed bound; it is not an actual checkout amount.
- A separate typed `VerifiedWeightSaleRule` / `quote_verified_mass`
  domain contract can calculate a quantized sellable mass **only** if *both*
  the seller's minimum and step are provided with trusted timestamped
  evidence. The test fixtures are deliberately synthetic.
- No `SKU(package_quantity=1kg)` is synthesized from a unit-price reference.
  The existing `Purchase(packs: int)`, budget planner, PlanRecord and
  actual-purchase events remain unchanged and do **not** accept these estimates.

### API (live Globus stand only)

```http
POST /catalog/weighted-candidates/estimate
Content-Type: application/json

{
  "item_id": "onion",
  "quantity": {"amount": "375", "unit": "g"}
}
```

A successful observation returns:

```json
{
  "item_id": "onion",
  "requested": {"amount": "375", "unit": "g"},
  "reference_unit_price": {
    "amount": "<current source-backed value>",
    "currency": "KGS",
    "per": {"amount": "1", "unit": "kg"}
  },
  "estimated_upper_bound": {
    "amount": "<computed upper cent bound>",
    "currency": "KGS"
  },
  "listing_available": true,
  "observed_at": "<acquisition timestamp>",
  "source_ref": "<same verified Globus product link>",
  "estimate_only": true,
  "plannable": false,
  "minimum_sellable_quantity": null,
  "weight_increment": null,
  "notice": "<explicit restriction>"
}
```

The live local web stand also exposes a compact **«Товары на вес —
справочный расчёт»** panel: pick one of the verified product identities,
enter the desired kilograms and review the source-linked estimate. This panel
does not offer a Save/Buy control and is hidden in the offline-only demo.

This endpoint rejects unknown Items, floats and incompatible units. It does
not let a client submit its own authoritative price. No model-generated
price enters purchase planning. The local three-Item offline demo keeps this
endpoint disabled (404) to avoid accidental web access.

Globus price evidence is from a **public addressless demo view**, not a
guarantee of address-specific store availability. The parser accepts only
unambiguous `som/kg` within the selected product page, requires public-demo
context and preserves sold-out/no-price states. A different redirected good ID
or external host is rejected.

### Audited correction

The previously curated `black_pepper` source page itself advertises
`som/kg`, even though the M12.11 weight reference had been marked 100g.
The reference metadata is corrected to **1 kg**, without altering the
existing fixed-SKU catalog. Do not confuse recipe's 2 g demand with the
retailer's per-kilogram **price basis**.

### Deliberate non-goals and remaining blockers

1. No verified min-sale mass or increment is exposed by the observed public
   product pages. Reference-rate math cannot infer these.
2. Discounted/multiple weight-price surfaces are currently **fail-closed**
   unless the per-kg rate is unambiguous, rather than silently choosing the
   cheapest displayed number.
3. Receipt-level actual mass may differ from requested/planned weight. An
   end-to-end weighted purchase workflow needs price adjustment/confirmation,
   consent, and idempotent household event semantics before being called ready.
4. Need to extend the full market evidence, planning, validation, persistence,
   shopping UI and purchase confirmation pipeline before weight SKUs can be
   admitted to the main catalog and Kyrgyz dishes unblocked.
5. Avoid cross-unit substitution: per-kg meat vs packaged mince, blended
   pepper vs black pepper, tomato paste vs fresh tomato all remain separate.

**Pilot gate:** 3.11/3.13 Python unit tests, mobile Chromium smoke,
synthetic min/step decision proof, error cases and no ghost purchases.
No user-facing checkout is claimed and no silent stock changes are allowed.
