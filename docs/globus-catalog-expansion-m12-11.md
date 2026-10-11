# M12.11 — Globus catalog expansion with an explicit weighted-goods fence

Verified against the public **addressless demo catalog** of Globus Online on
2026-10-11. Product links establish retailer *listing identity*, **not**
live inventory availability or a future price. Prices remain fetched on demand
by the bounded M5 market provider and stored as market evidence at planning time.

## Packaged goods: planner-compatible, 29 new SKUs / 18 new Item identities

The existing curated catalog goes from **61 SKUs / 27 Items** to
**90 SKUs / 45 Items**, without changing the legacy inventory, plan or market
contract. These represent fixed, explicitly labeled package quantities,
not fractions inferred from the retailer's price display.

| Canonical Item | Source-backed Globus listing(s) |
|---|---|
| `paprika` | [Пряно 10г](https://globus-online.kg/ru-kg/good/f1f3f73c485847aeb42fa7cba2c5d2ed000200010001), [Пряно 50г](https://globus-online.kg/ru-kg/good/a5aeee02d7854d2fb4d66061d0b26fa5000200010000) |
| `turmeric` | [Приправыч 20г](https://globus-online.kg/ru-kg/good/32985f9a4d0d418eabfcccb75fb8aa7c000200010000), [Пряно 20г](https://globus-online.kg/ru-kg/good/46c775d5b9fd439d97a4d02fbc3e85e2000200010001) |
| `dried_parsley` | [Татымал 7г](https://globus-online.kg/ru-kg/good/fb4c5e39178647e3bac5d9a8f11f3fd6000200010000) |
| `dried_basil` | [Kotanyi 9г](https://globus-online.kg/ru-kg/good/b4dc592391c045019c3243892c8fe7bc000300010001) |
| `canned_corn` | [Bonduelle 340г](https://globus-online.kg/ru-kg/good/d3b13e1e87174eeca4266cba8368f764000200010000), [Globus 340г](https://globus-online.kg/ru-kg/good/39b778dc95db442f8ccf6d7ad4daaa7f000200010001), [3 Желания 340г](https://globus-online.kg/ru-kg/good/4df0bc9862bd4a148a1a464e254910c3000200010001) |
| `tomato_paste` | [№1 250г](https://globus-online.kg/ru-kg/good/f294182e5bd6406d8bfd42681ae67bbd000200010001), [Татым 250г](https://globus-online.kg/ru-kg/good/a90c8e277f6b4bf6965ef958b95b5a55000300010001), [Айлана 900г](https://globus-online.kg/ru-kg/good/39f7505d68f04e7ea469b7f4f0264a50000100010000) |
| `vinegar_9_percent` | [UNIDAN 500мл 9%](https://globus-online.kg/ru-kg/good/2eb1183b2f2b4e6a802c593da9387386000300010001) |
| `breadcrumbs` | [Русский Аппетит 200г](https://globus-online.kg/ru-kg/good/85ac4327bcc841ea9cfe87c5a3836a20000200010001) |
| `pepper_blend` | [Пряно смесь перцев 15г](https://globus-online.kg/ru-kg/good/3cfe46c19cd942e594153a8fc7b86e90000100010000) |
| `dishwashing_liquid` | [Fairy ромашка 450мл](https://globus-online.kg/ru-kg/good/3635656d8201496d9c6ffd6a1cb2d412000100010000), [AOS Алоэ 450мл](https://globus-online.kg/ru-kg/good/52343c9e85394e839825b734a88aeeb9000300010001) |
| `toothpaste` | [Splat Sensitive 100мл](https://globus-online.kg/ru-kg/good/13a88ad22c754b70bf0d560c9f2d2116000100010000), [Splat Лечебные травы 100мл](https://globus-online.kg/ru-kg/good/9e36e0639469424d86d7f002cd27a524000100010000) |
| `liquid_hand_soap` | [Esviro 500мл](https://globus-online.kg/ru-kg/good/a4a16e870b0a43a4b411a6d351e79438000200010001) |
| `dish_sponge` | [№1 1шт](https://globus-online.kg/ru-kg/good/c03c0cf3fee540f39c192b359b76c616000200010001), [Dora Forte 7шт](https://globus-online.kg/ru-kg/good/89131f073bff41a3baab4b46e3c40db0000100010000) |
| `toilet_paper_roll` | [№1 4 рулона](https://globus-online.kg/ru-kg/good/1aec5b97061a4ebf9349a8c5eadd92a1000200010000), [Pero Rose 8 рулонов](https://globus-online.kg/ru-kg/good/1edb740495f742fa9c4ce0c28e359d5d000300010001) |
| `trash_bag_30l` | [№1 30л, 20шт](https://globus-online.kg/ru-kg/good/c1806278afdc4d73875258de6a1e8454000200010001) |
| `trash_bag_60l` | [№1 60л, 20шт](https://globus-online.kg/ru-kg/good/31d587634dd546149216660d63ec614d000200010001) |
| `laundry_powder_color` | [Persil для цветного 3кг](https://globus-online.kg/ru-kg/good/ca5bd88e607c45329fcf307276d1795f000300010001) |
| `shampoo` | [Garnier Fructis 400мл](https://globus-online.kg/ru-kg/good/f7e6d9949ef44206be4ead1c51d4beef000100010000), [Likato Recovery 400мл](https://globus-online.kg/ru-kg/good/dc3d18d7892a4d01b7b649b0798d0c09000200010001) |

`toilet_paper_roll` tracks **individual rolls** even though an offer sells a
fixed 4- or 8-roll bundle. `dish_sponge` tracks individual sponges, while
trash bags are split by capacity (30l vs 60l) to prevent silent use of the
wrong size. These are explicit package quantities, never a claim that the
retailer sells each internal unit separately.

`pepper_blend` is NOT `black_pepper`; `tomato_paste` is NOT fresh
`tomato`, canned corn is not fresh corn, and vinegar 9% is not vinegar 5%.
No implicit substitutions are introduced by these additions.

## Weighed goods: ten sourced references, not purchasable SKUs

The real retailer displays these goods as **som/kg** (black pepper by **100g**).
Existing Globus HTML parser rejects per-kilogram listings explicitly, and
M1–M3 optimizes integer numbers of fixed packages. Modeling a per-kilogram
unit as a compulsory 1kg pack would undercut both budget and household
quantity validity.

So the observed listings live under
`market/catalogs/globus_weight_candidates.py` and
`GET /catalog/weighted-candidates`: **read-only, no prices, no plan, and
`plannable=false`**.

| Item ID | Official weight-listed product |
|---|---|
| `onion` | [Лук репчатый фас вес](https://globus-online.kg/ru-kg/good/9ca7b9b184f14fe3af97d319a64dd364000200010001) |
| `potato` | [Картофель фасованный вес](https://globus-online.kg/ru-kg/good/268f8c2e69184dc5834205503a1c53f1000100010000) |
| `beef` | [Говядина стейк](https://globus-online.kg/ru-kg/good/623c4eaaea2f45eeb256209980309780000300010001) |
| `beef_mince` | [Фарш говяжий диетический](https://globus-online.kg/ru-kg/good/f700f604344843ddab854e4208ca841f000100010000) |
| `carrot` | [Морковь мытая](https://globus-online.kg/ru-kg/good/f4c437e4e6c74f048cc5f77a3ea64cbe000300010001) |
| `tomato` | [Помидоры вес](https://globus-online.kg/ru-kg/good/f1eb82e519d94ff981bd71140f690d52000200010000) |
| `bell_pepper` | [Перец болгарский зелёный](https://globus-online.kg/ru-kg/good/8e23e7215445494f833ad77c002f3dc1000300010001) |
| `garlic` | [Чеснок вес](https://globus-online.kg/ru-kg/good/6828d37edf754f6a8a4ac7740428f4af000200010000) |
| `chicken_fillet` | [Куриное филе охлаждённое](https://globus-online.kg/ru-kg/good/96003deeeb1546019404766f87bf9f4d000100010000) |
| `black_pepper` | [Перец чёрный 2 сорт, цена за 100г](https://globus-online.kg/ru-kg/good/3853044f04ec4d3d9733d68d5167aac0000200010000) |

These links are product-research entries only; item IDs for weighed sources
are proposals for canonical identity, **not new SKUs** and not proof of a
current store price or availability.

### Next engineering step: a separate weighted-product contract

1. Introduce retailer unit price with explicit `KGS/kg` (or `KGS/100g`),
   observed-at/source/availability and the vendor's actual weight increments
   and minimum quantities. **Do not infer step size from the reference unit.**
2. Introduce requested sellable mass and a deterministic quantization policy
   for each weighed SKU, then price exact selected masses without relying on
   the existing integer-pack arithmetic. Missing step/min data must block
   purchases or require an explicit user confirmation.
3. Update plan snapshot/validation, overbuy, shopping confirmation, household
   event quantities and repeat/idempotency to preserve exact mass semantics.
4. Only then promote verified weighted candidate listings into the live
   `CatalogSnapshot`, gate recipes accordingly, and collect real field evidence.

This intentionally does NOT unblock kuurdak, manty, oromo and lagman yet.
One extra safe packaged spice/ingredient is useful now, but cannot represent
their missing fresh meat/vegetables. Existing boorsok remains catalog-eligible.
