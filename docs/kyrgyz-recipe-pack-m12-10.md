# M12.10 — Kyrgyz recipe pack: audited first slice

This is **originally authored recipe data**, not a reproduced restaurant/chef
recipe, and **not** a promise of inventory or retailer stock. The five measured
candidates implement home variants rather than definitive national standards.

## Scope and current catalog gate

The M5.1 checked-in Globus sample catalog contains 27 Item types / 61 SKUs.
Every recipe requires all of its ingredient Item IDs to exist **and** have
a package with compatible units. A missing Item cannot be replaced with a
similarly named product or a generic seasoning. Recipes not meeting this gate
remain definitions for future inventory/catalog expansion; they are not
published to the consumer recipe list and cannot receive market quotations.

| Recipe ID | Dish | Exact M5.1 catalog result |
|---|---|---|
| `kg-boorsok-unleavened` | Боорсок из пресного теста | Eligible (flour/water/salt/sunflower_oil) |
| `kg-kuurdak-beef` | Куурдак с говядиной и картофелем | Blocked: beef, potato, onion, black_pepper |
| `kg-manty-beef` | Манты с говядиной и луком | Blocked: beef, onion, black_pepper |
| `kg-oromo-beef-potato` | Оромо с мясом и картофелем | Blocked: beef, potato, onion, black_pepper |
| `kg-lagman-hand-pulled` | Лагман с домашней лапшой | Blocked: beef, onion, carrot, tomato, bell_pepper, garlic, black_pepper |

These catalog gaps are *not* confirmed market unavailability; they are simply
absent from the project's **curated** retailer Item/SKU map. Add them only
after binding real package SKUs and source-backed listings. Do not create
placeholder product URLs, prices, or guessed unit conversions.

### Product semantics

- The public `GET /recipes` contains only eligible, fully catalog-supported
  recipes. `POST /recipes/{id}/quote` rejects unsupported IDs with 404.
- An eligible recipe can still be `unverified` until every ingredient's
  household quantity is confirmed; this never assumes zero or availability.
- Only after exact verified shortfalls and a fresh sourced market quote can
  the existing planner offer a pack-aware estimate. Saved plan != purchase.
- Cooking/frying is not a stocktake or depletion observation. Oil specified for
  deep frying describes oil needed in the pan, not amount eaten or an automatic
  household stock write.
- For boorsok specifically the supported variant is **unleavened** and may
  be denser than the common yeast-raised boorsok. Yeast should be introduced
  as its own real Item/SKU before a yeast-raised variant is made eligible.
- `kyrgyz_recipe_coverage(catalog)` produces a deterministic developer audit
  of missing Item IDs vs incompatible unit matches; it is not a price quote.

### Culinary background (research pointers, not copied instructions)

- Boorsok variations and usual yeasted form:
  https://ilovekg.com/en/food/boorsok/
- Kuurdak meat/potato home variations:
  https://ilovekg.com/en/food/kuurdak/
- Manty individually wrapped/steamed:
  https://ilovekg.com/en/food/manty/
- Oromo rolled and steamed:
  https://ilovekg.com/en/food/oromo/
- Lagman pulled noodles and meat/vegetable sauce, Dungan/Uyghur roots:
  https://ilovekg.com/en/food/lagman/

The ingredient numbers and short Russian instructions in this pack are
independently formulated pilot variants; the URLs are culinary research
references, **not attribution claiming authorship or licensing of our text**.
This first pass has not been kitchen-tested or approved by a culinary reviewer.
Any later chef/source-linked recipes require explicit source/license review.

## Follow-ups

1. Verify real catalog supply for the missing ingredients before making the
   four blocked dishes user-available, starting with onion/potato/meat.
2. Do not collapse beef/lamb into the same Item without a deliberate substitute
   choice; nor dry packaged pasta into hand-pulled dough as if identical.
3. Run a manual cook/quantity pilot: ingredient correctness, batch yields,
   oil accounting, and unit coverage. If variants change, retain stable IDs
   only for semantically equivalent recipes.
4. Keep API/plan provenance tests, Python 3.11/3.13 and mobile Chromium gates.
