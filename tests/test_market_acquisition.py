from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from household_supply import (
    CatalogBinding,
    CatalogResolutionMethod,
    CatalogResolutionStatus,
    CatalogSnapshot,
    Demand,
    ExternalListingKey,
    InventorySnapshot,
    Item,
    MarketAcquisitionBatch,
    MarketCompilationPolicy,
    MarketObservation,
    MarketObservationDispositionStatus,
    Money,
    PlanningPolicy,
    PlanningProblem,
    ProductIdentifier,
    Quantity,
    SKU,
    StaticMarketProvider,
    acquire_market,
    build_plan,
    compile_market_snapshot,
    resolve_market_observation,
)

NOW = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)


def _rice_catalog() -> tuple[Item, SKU, CatalogSnapshot]:
    rice = Item("rice", "Rice")
    sku = SKU(
        "rice-800",
        rice,
        "Rice 800 g",
        Quantity(800, "g"),
        brand="Example",
        identifiers=(ProductIdentifier("GTIN", "00012345678905"),),
    )
    binding = CatalogBinding(
        ExternalListingKey("fixture", "store-a", "listing-rice"),
        sku.id,
        "manual-test-binding",
    )
    return rice, sku, CatalogSnapshot([sku], [binding])


def _observation(
    *,
    observation_id: str = "obs-1",
    external_product_id: str = "listing-rice",
    seller_id: str = "store-a",
    price: int | str = 120,
    observed_at: datetime = NOW - timedelta(minutes=30),
    product_identifier: ProductIdentifier | None = None,
    package_quantity: Quantity | None = Quantity(800, "g"),
    available: bool = True,
    provider_id: str = "fixture",
) -> MarketObservation:
    return MarketObservation(
        id=observation_id,
        provider_id=provider_id,
        seller_id=seller_id,
        external_product_id=external_product_id,
        price=Money(price, "KGS"),
        observed_at=observed_at,
        available=available,
        product_identifier=product_identifier,
        package_quantity=package_quantity,
        name="Example Rice",
        source_ref=f"fixture://{observation_id}",
    )


def test_product_identifier_is_normalized_and_sku_identifiers_are_immutable() -> None:
    rice = Item("rice", "Rice")
    identifiers = [ProductIdentifier(" GTIN ", " 123 ")]
    sku = SKU("rice", rice, "Rice", Quantity(1, "kg"), identifiers=identifiers)

    identifiers.append(ProductIdentifier("ean13", "999"))

    assert sku.identifiers == (ProductIdentifier("gtin", "123"),)


def test_catalog_rejects_same_global_identifier_on_two_skus() -> None:
    rice = Item("rice", "Rice")
    identifier = ProductIdentifier("gtin", "123")
    a = SKU("a", rice, "A", Quantity(500, "g"), identifiers=(identifier,))
    b = SKU("b", rice, "B", Quantity(1, "kg"), identifiers=(identifier,))

    with pytest.raises(ValueError, match="assigned to multiple SKUs"):
        CatalogSnapshot([a, b])


def test_catalog_rejects_unknown_binding_sku_and_duplicate_listing_key() -> None:
    _, sku, _ = _rice_catalog()
    key = ExternalListingKey("fixture", "store-a", "rice")

    with pytest.raises(ValueError, match="unknown sku"):
        CatalogSnapshot([sku], [CatalogBinding(key, "missing", "manual")])

    with pytest.raises(ValueError, match="duplicate external listing binding"):
        CatalogSnapshot(
            [sku],
            [
                CatalogBinding(key, sku.id, "manual-a"),
                CatalogBinding(key, sku.id, "manual-b"),
            ],
        )


def test_free_text_alone_does_not_resolve_catalog_identity() -> None:
    _, sku, _ = _rice_catalog()
    catalog_without_binding = CatalogSnapshot([sku])
    observation = _observation(
        external_product_id="unknown-listing",
        product_identifier=None,
    )

    resolution = resolve_market_observation(catalog_without_binding, observation)

    assert resolution.status is CatalogResolutionStatus.UNRESOLVED
    assert resolution.sku is None


def test_explicit_binding_resolves_without_product_identifier() -> None:
    _, sku, catalog = _rice_catalog()

    resolution = resolve_market_observation(catalog, _observation())

    assert resolution.status is CatalogResolutionStatus.RESOLVED
    assert resolution.sku == sku
    assert resolution.method is CatalogResolutionMethod.EXPLICIT_BINDING


def test_exact_product_identifier_resolves_without_listing_binding() -> None:
    _, sku, _ = _rice_catalog()
    catalog = CatalogSnapshot([sku])
    observation = _observation(
        external_product_id="new-listing",
        product_identifier=ProductIdentifier("gtin", "00012345678905"),
    )

    resolution = resolve_market_observation(catalog, observation)

    assert resolution.status is CatalogResolutionStatus.RESOLVED
    assert resolution.sku == sku
    assert resolution.method is CatalogResolutionMethod.PRODUCT_IDENTIFIER


def test_explicit_binding_and_identifier_can_corroborate() -> None:
    _, sku, catalog = _rice_catalog()
    observation = _observation(
        product_identifier=ProductIdentifier("gtin", "00012345678905")
    )

    resolution = resolve_market_observation(catalog, observation)

    assert resolution.status is CatalogResolutionStatus.RESOLVED
    assert resolution.sku == sku
    assert resolution.method is CatalogResolutionMethod.CORROBORATED


def test_binding_identifier_conflict_is_rejected() -> None:
    rice = Item("rice", "Rice")
    sku_a = SKU(
        "rice-a",
        rice,
        "Rice A",
        Quantity(800, "g"),
        identifiers=(ProductIdentifier("gtin", "111"),),
    )
    sku_b = SKU(
        "rice-b",
        rice,
        "Rice B",
        Quantity(800, "g"),
        identifiers=(ProductIdentifier("gtin", "222"),),
    )
    key = ExternalListingKey("fixture", "store-a", "listing-rice")
    catalog = CatalogSnapshot(
        [sku_a, sku_b],
        [CatalogBinding(key, sku_a.id, "manual")],
    )
    observation = _observation(product_identifier=ProductIdentifier("gtin", "222"))

    resolution = resolve_market_observation(catalog, observation)

    assert resolution.status is CatalogResolutionStatus.CONFLICT
    assert resolution.candidate_sku_ids == ("rice-a", "rice-b")


def test_bound_sku_rejects_different_identifier_in_same_namespace() -> None:
    _, sku, catalog = _rice_catalog()
    observation = _observation(product_identifier=ProductIdentifier("gtin", "999"))

    resolution = resolve_market_observation(catalog, observation)

    assert resolution.status is CatalogResolutionStatus.CONFLICT
    assert resolution.candidate_sku_ids == (sku.id,)


def test_resolved_sku_rejects_material_package_mismatch() -> None:
    _, _, catalog = _rice_catalog()
    observation = _observation(package_quantity=Quantity(1, "kg"))

    resolution = resolve_market_observation(catalog, observation)

    assert resolution.status is CatalogResolutionStatus.CONFLICT
    assert "package quantity" in resolution.detail


def test_equivalent_package_units_are_accepted() -> None:
    milk = Item("milk", "Milk")
    sku = SKU("milk-1l", milk, "Milk 1L", Quantity(1, "l"))
    key = ExternalListingKey("fixture", "store-a", "milk")
    catalog = CatalogSnapshot([sku], [CatalogBinding(key, sku.id, "manual")])
    observation = MarketObservation(
        "obs",
        "fixture",
        "store-a",
        "milk",
        Money(90, "KGS"),
        NOW,
        package_quantity=Quantity(1000, "ml"),
    )

    assert resolve_market_observation(catalog, observation).status is CatalogResolutionStatus.RESOLVED


def test_acquisition_batch_captures_list_and_enforces_provider_attribution() -> None:
    observations = [_observation()]
    batch = MarketAcquisitionBatch("fixture", NOW, observations)
    observations.append(_observation(observation_id="obs-2"))

    assert len(batch.observations) == 1

    foreign = _observation(provider_id="other")
    with pytest.raises(ValueError, match="does not match"):
        MarketAcquisitionBatch("fixture", NOW, [foreign])


def test_acquisition_rejects_naive_timestamps_and_future_observation() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _observation(observed_at=datetime(2026, 9, 2, 11, 0))

    future = _observation(observed_at=NOW + timedelta(minutes=1))
    with pytest.raises(ValueError, match="after acquisition"):
        MarketAcquisitionBatch("fixture", NOW, [future])


def test_acquire_market_rejects_provider_that_misattributed_batch() -> None:
    batch = MarketAcquisitionBatch("fixture", NOW, [_observation()])

    @dataclass
    class BadProvider:
        provider_id: str = "claimed-provider"

        def acquire(self) -> MarketAcquisitionBatch:
            return batch

    with pytest.raises(ValueError, match="different provider_id"):
        acquire_market(BadProvider())

    assert acquire_market(StaticMarketProvider(batch)) is batch


def test_compiler_uses_latest_observation_and_preserves_provenance() -> None:
    _, sku, catalog = _rice_catalog()
    old = _observation(
        observation_id="old",
        price=140,
        observed_at=NOW - timedelta(hours=2),
    )
    latest = _observation(
        observation_id="latest",
        price=120,
        observed_at=NOW - timedelta(minutes=10),
    )
    batch = MarketAcquisitionBatch("fixture", NOW, [latest, old])

    compilation = compile_market_snapshot(catalog, [batch], captured_at=NOW)

    assert len(compilation.snapshot.offers) == 1
    offer = compilation.snapshot.offers[0]
    assert offer.sku == sku
    assert offer.price == Money(120, "KGS")
    assert offer.provenance is not None
    assert offer.provenance.observation_id == "latest"
    assert offer.provenance.source_ref == "fixture://latest"
    statuses = {entry.observation.id: entry.status for entry in compilation.dispositions}
    assert statuses == {
        "old": MarketObservationDispositionStatus.SUPERSEDED,
        "latest": MarketObservationDispositionStatus.ACCEPTED,
    }


def test_offer_identity_is_stable_across_new_observations_of_same_listing() -> None:
    _, _, catalog = _rice_catalog()
    first = MarketAcquisitionBatch(
        "fixture", NOW - timedelta(hours=1),
        [_observation(observation_id="one", observed_at=NOW - timedelta(hours=1))],
    )
    second = MarketAcquisitionBatch(
        "fixture", NOW,
        [_observation(observation_id="two", price=99, observed_at=NOW)],
    )

    first_snapshot = compile_market_snapshot(
        catalog, [first], captured_at=NOW - timedelta(hours=1)
    ).snapshot
    second_snapshot = compile_market_snapshot(catalog, [second], captured_at=NOW).snapshot

    assert first_snapshot.offers[0].id == second_snapshot.offers[0].id
    assert first_snapshot.offers[0].price != second_snapshot.offers[0].price


def test_compiler_rejects_equal_timestamp_latest_tie_instead_of_using_input_order() -> None:
    _, _, catalog = _rice_catalog()
    a = _observation(observation_id="a", price=100)
    b = _observation(observation_id="b", price=120)
    batch = MarketAcquisitionBatch("fixture", NOW, [b, a])

    compilation = compile_market_snapshot(catalog, [batch], captured_at=NOW)

    assert compilation.snapshot.offers == ()
    assert {
        disposition.status for disposition in compilation.dispositions
    } == {MarketObservationDispositionStatus.CONFLICT}


def test_compiler_excludes_stale_latest_observation() -> None:
    _, _, catalog = _rice_catalog()
    observation = _observation(observed_at=NOW - timedelta(hours=5))
    batch = MarketAcquisitionBatch("fixture", NOW, [observation])

    compilation = compile_market_snapshot(
        catalog,
        [batch],
        captured_at=NOW,
        policy=MarketCompilationPolicy(max_observation_age=timedelta(hours=4)),
    )

    assert compilation.snapshot.offers == ()
    assert compilation.dispositions[0].status is MarketObservationDispositionStatus.STALE


def test_compiler_excludes_unresolved_and_catalog_conflict() -> None:
    _, _, catalog = _rice_catalog()
    unresolved = _observation(
        observation_id="unknown",
        external_product_id="unknown",
        product_identifier=None,
    )
    conflict = _observation(
        observation_id="wrong-pack",
        package_quantity=Quantity(1, "kg"),
    )
    batch = MarketAcquisitionBatch("fixture", NOW, [unresolved, conflict])

    compilation = compile_market_snapshot(catalog, [batch], captured_at=NOW)

    assert compilation.snapshot.offers == ()
    statuses = {entry.observation.id: entry.status for entry in compilation.dispositions}
    assert statuses["unknown"] is MarketObservationDispositionStatus.UNRESOLVED
    assert statuses["wrong-pack"] is MarketObservationDispositionStatus.CONFLICT


def test_compiler_accepts_unavailable_offer_as_market_state() -> None:
    _, _, catalog = _rice_catalog()
    batch = MarketAcquisitionBatch(
        "fixture", NOW, [_observation(available=False)]
    )

    compilation = compile_market_snapshot(catalog, [batch], captured_at=NOW)

    assert len(compilation.snapshot.offers) == 1
    assert compilation.snapshot.offers[0].available is False


def test_compiler_rejects_batch_acquired_after_requested_snapshot() -> None:
    _, _, catalog = _rice_catalog()
    batch = MarketAcquisitionBatch(
        "fixture",
        NOW + timedelta(minutes=1),
        [_observation(observed_at=NOW)],
    )

    with pytest.raises(ValueError, match="acquired after"):
        compile_market_snapshot(catalog, [batch], captured_at=NOW)


def test_compiler_rejects_duplicate_provider_observation_identity_across_batches() -> None:
    _, _, catalog = _rice_catalog()
    observation = _observation()
    batch_a = MarketAcquisitionBatch("fixture", NOW, [observation])
    batch_b = MarketAcquisitionBatch("fixture", NOW, [observation])

    with pytest.raises(ValueError, match="duplicate provider observation identity"):
        compile_market_snapshot(catalog, [batch_a, batch_b], captured_at=NOW)


def test_market_compilation_is_independent_of_batch_and_observation_order() -> None:
    rice, rice_sku, rice_catalog = _rice_catalog()
    milk = Item("milk", "Milk")
    milk_sku = SKU(
        "milk",
        milk,
        "Milk 1L",
        Quantity(1, "l"),
        identifiers=(ProductIdentifier("gtin", "milk-gtin"),),
    )
    catalog = CatalogSnapshot(
        [rice_sku, milk_sku],
        list(rice_catalog.bindings),
    )
    rice_old = _observation(
        observation_id="rice-old",
        price=130,
        observed_at=NOW - timedelta(hours=2),
    )
    rice_new = _observation(
        observation_id="rice-new",
        price=120,
        observed_at=NOW - timedelta(hours=1),
    )
    milk_obs = MarketObservation(
        "milk",
        "fixture",
        "store-b",
        "milk-listing",
        Money(95, "KGS"),
        NOW - timedelta(minutes=30),
        product_identifier=ProductIdentifier("gtin", "milk-gtin"),
        package_quantity=Quantity(1000, "ml"),
    )
    batch_a = MarketAcquisitionBatch("fixture", NOW, [rice_old, milk_obs])
    batch_b = MarketAcquisitionBatch("fixture", NOW, [rice_new])

    first = compile_market_snapshot(catalog, [batch_a, batch_b], captured_at=NOW)
    batch_a_reversed = MarketAcquisitionBatch("fixture", NOW, [milk_obs, rice_old])
    second = compile_market_snapshot(catalog, [batch_b, batch_a_reversed], captured_at=NOW)

    assert first == second
    assert {offer.sku.item.id for offer in first.snapshot.offers} == {rice.id, milk.id}


def test_compiled_market_snapshot_feeds_existing_m1_planner_without_adapter_logic() -> None:
    rice, _, catalog = _rice_catalog()
    batch = MarketAcquisitionBatch("fixture", NOW, [_observation(price=120)])
    compilation = compile_market_snapshot(catalog, [batch], captured_at=NOW)
    problem = PlanningProblem(
        demands=(Demand(rice, Quantity(600, "g"), "test"),),
        inventory=InventorySnapshot(()),
        market=compilation.snapshot,
        policy=PlanningPolicy(Money(500, "KGS")),
    )

    plan = build_plan(problem)

    assert plan.status.value == "feasible"
    assert plan.total_cost == Money(120, "KGS")
    assert plan.purchases[0].offer.provenance is not None
    assert plan.purchases[0].offer.provenance.observation_id == "obs-1"


def test_market_observation_rejects_zero_package_and_non_bool_availability() -> None:
    with pytest.raises(ValueError, match="package_quantity must be positive"):
        _observation(package_quantity=Quantity(0, "g"))

    with pytest.raises(TypeError, match="available must be bool"):
        MarketObservation(
            "obs",
            "fixture",
            "store-a",
            "listing-rice",
            Money(100, "KGS"),
            NOW,
            available="yes",  # type: ignore[arg-type]
        )


def test_market_compilation_rejects_forged_offer_without_accepted_disposition() -> None:
    _, sku, catalog = _rice_catalog()
    observation = _observation()
    batch = MarketAcquisitionBatch("fixture", NOW, [observation])
    good = compile_market_snapshot(catalog, [batch], captured_at=NOW)
    offer = good.snapshot.offers[0]

    from household_supply import MarketCompilation

    with pytest.raises(ValueError, match="dispositions do not match"):
        MarketCompilation(
            catalog=catalog,
            batches=(batch,),
            policy=MarketCompilationPolicy(),
            snapshot=good.snapshot,
            dispositions=(),
        )

    assert offer.sku == sku


def test_market_compilation_rejects_accepted_disposition_missing_from_snapshot() -> None:
    _, _, catalog = _rice_catalog()
    batch = MarketAcquisitionBatch("fixture", NOW, [_observation()])
    good = compile_market_snapshot(catalog, [batch], captured_at=NOW)

    from household_supply import MarketCompilation, MarketSnapshot

    with pytest.raises(ValueError, match="snapshot does not match"):
        MarketCompilation(
            catalog=catalog,
            batches=(batch,),
            policy=MarketCompilationPolicy(),
            snapshot=MarketSnapshot(NOW, ()),
            dispositions=good.dispositions,
        )




def test_market_compilation_rejects_forged_catalog_resolution() -> None:
    _, sku, catalog = _rice_catalog()
    observation = _observation(package_quantity=Quantity(1, "kg"))
    batch = MarketAcquisitionBatch("fixture", NOW, (observation,))

    canonical = compile_market_snapshot(catalog, (batch,), captured_at=NOW)
    assert canonical.snapshot.offers == ()
    assert canonical.dispositions[0].status is MarketObservationDispositionStatus.CONFLICT

    from household_supply import (
        CatalogResolution,
        MarketCompilation,
        MarketObservationDisposition,
        MarketSnapshot,
        Offer,
        OfferProvenance,
    )

    forged_resolution = CatalogResolution(
        observation_id=observation.id,
        status=CatalogResolutionStatus.RESOLVED,
        sku=sku,
        method=CatalogResolutionMethod.EXPLICIT_BINDING,
        candidate_sku_ids=(sku.id,),
    )
    forged_disposition = MarketObservationDisposition(
        observation=observation,
        status=MarketObservationDispositionStatus.ACCEPTED,
        resolution=forged_resolution,
    )
    forged_offer = Offer(
        id="forged",
        sku=sku,
        seller_id=observation.seller_id,
        price=observation.price,
        observed_at=observation.observed_at,
        source=observation.provider_id,
        provenance=OfferProvenance(
            observation_id=observation.id,
            listing_key=observation.listing_key,
            source_ref=observation.source_ref,
        ),
    )

    with pytest.raises(ValueError, match="snapshot does not match"):
        MarketCompilation(
            catalog=catalog,
            batches=(batch,),
            policy=MarketCompilationPolicy(),
            snapshot=MarketSnapshot(NOW, (forged_offer,)),
            dispositions=(forged_disposition,),
        )


def test_market_snapshot_and_offer_require_timezone_aware_market_time() -> None:
    rice = Item("rice", "Rice")
    sku = SKU("rice", rice, "Rice", Quantity(1, "kg"))
    naive = datetime(2026, 9, 2, 12, 0)

    from household_supply import MarketSnapshot, Offer

    with pytest.raises(ValueError, match="offer observed_at must be timezone-aware"):
        Offer("offer", sku, "store", Money(100, "KGS"), naive, "fixture")

    with pytest.raises(ValueError, match="snapshot captured_at must be timezone-aware"):
        MarketSnapshot(naive, ())


def test_catalog_snapshot_captures_input_collections_by_value() -> None:
    rice = Item("rice", "Rice")
    sku = SKU("rice", rice, "Rice", Quantity(1, "kg"))
    key = ExternalListingKey("fixture", "store", "rice")
    binding = CatalogBinding(key, sku.id, "manual")
    skus = [sku]
    bindings = [binding]

    catalog = CatalogSnapshot(skus, bindings)
    skus.clear()
    bindings.clear()

    assert catalog.skus == (sku,)
    assert catalog.bindings == (binding,)


def test_market_compilation_captures_dispositions_by_value() -> None:
    _, _, catalog = _rice_catalog()
    batch = MarketAcquisitionBatch("fixture", NOW, [_observation()])
    good = compile_market_snapshot(catalog, [batch], captured_at=NOW)
    dispositions = list(good.dispositions)

    from household_supply import MarketCompilation

    compilation = MarketCompilation(
        catalog=catalog,
        batches=(batch,),
        policy=MarketCompilationPolicy(),
        snapshot=good.snapshot,
        dispositions=dispositions,
    )
    dispositions.clear()

    assert compilation.dispositions == good.dispositions
    assert compilation.batches == good.batches


def test_external_listing_identity_is_provider_scoped() -> None:
    rice = Item("rice", "Rice")
    sku_a = SKU("rice-a", rice, "Rice A", Quantity(800, "g"))
    sku_b = SKU("rice-b", rice, "Rice B", Quantity(800, "g"))
    catalog = CatalogSnapshot(
        (sku_a, sku_b),
        (
            CatalogBinding(
                ExternalListingKey("provider-a", "store", "same-id"),
                sku_a.id,
                "binding-a",
            ),
            CatalogBinding(
                ExternalListingKey("provider-b", "store", "same-id"),
                sku_b.id,
                "binding-b",
            ),
        ),
    )
    obs_a = MarketObservation(
        "obs",
        "provider-a",
        "store",
        "same-id",
        Money(100, "KGS"),
        NOW,
        package_quantity=Quantity(800, "g"),
    )
    obs_b = MarketObservation(
        "obs",
        "provider-b",
        "store",
        "same-id",
        Money(110, "KGS"),
        NOW,
        package_quantity=Quantity(800, "g"),
    )

    compilation = compile_market_snapshot(
        catalog,
        (
            MarketAcquisitionBatch("provider-a", NOW, (obs_a,)),
            MarketAcquisitionBatch("provider-b", NOW, (obs_b,)),
        ),
        captured_at=NOW,
    )

    assert {offer.sku.id for offer in compilation.snapshot.offers} == {
        "rice-a",
        "rice-b",
    }
    assert len({offer.id for offer in compilation.snapshot.offers}) == 2


def test_latest_unavailable_observation_does_not_fall_back_to_older_available_price() -> None:
    rice, _, catalog = _rice_catalog()
    old = _observation(
        observation_id="available-old",
        price=90,
        observed_at=NOW - timedelta(hours=1),
        available=True,
    )
    latest = _observation(
        observation_id="unavailable-new",
        price=90,
        observed_at=NOW - timedelta(minutes=1),
        available=False,
    )
    compilation = compile_market_snapshot(
        catalog,
        (MarketAcquisitionBatch("fixture", NOW, (old, latest)),),
        captured_at=NOW,
    )
    problem = PlanningProblem(
        demands=(Demand(rice, Quantity(100, "g"), "test"),),
        inventory=InventorySnapshot(()),
        market=compilation.snapshot,
        policy=PlanningPolicy(Money(500, "KGS")),
    )

    plan = build_plan(problem)

    assert compilation.snapshot.offers[0].available is False
    assert plan.status.value == "infeasible"


def test_latest_catalog_conflict_does_not_fall_back_to_older_resolved_observation() -> None:
    _, _, catalog = _rice_catalog()
    old = _observation(
        observation_id="resolved-old",
        observed_at=NOW - timedelta(hours=1),
    )
    latest = _observation(
        observation_id="conflict-new",
        observed_at=NOW - timedelta(minutes=1),
        package_quantity=Quantity(1, "kg"),
    )

    compilation = compile_market_snapshot(
        catalog,
        (MarketAcquisitionBatch("fixture", NOW, (old, latest)),),
        captured_at=NOW,
    )

    assert compilation.snapshot.offers == ()
    statuses = {d.observation.id: d.status for d in compilation.dispositions}
    assert statuses["resolved-old"] is MarketObservationDispositionStatus.SUPERSEDED
    assert statuses["conflict-new"] is MarketObservationDispositionStatus.CONFLICT
