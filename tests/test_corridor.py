from chargepath import GeoJsonLineString, Station
from chargepath.corridor import CandidateSelectionConfig, select_corridor_candidates
from chargepath.station_data import (
    EPDK_SOURCE_NAME,
    EPDK_SOURCE_URL,
    CompatibleStationOption,
    SnapshotProvenance,
)

PROVENANCE = SnapshotProvenance(
    snapshot_id="epdk-synthetic-test-001",
    source_name=EPDK_SOURCE_NAME,
    source_url=EPDK_SOURCE_URL,
    retrieved_at="2026-08-09T14:13:33Z",
    response_sha256="a" * 64,
    reuse_status="pending_verification",
)


def make_option(
    station_id: str, *, longitude: float, latitude: float, power_kw: float = 150
) -> CompatibleStationOption:
    source_id = station_id.removeprefix("epdk:")
    return CompatibleStationOption(
        station=Station(
            id=station_id,
            name=f"Synthetic {source_id}",
            latitude=latitude,
            longitude=longitude,
            max_power_kw=power_kw,
        ),
        site_source_id=source_id,
        socket_source_ids=("DC-1",),
        provenance=PROVENANCE,
    )


def test_corridor_selection_is_capped_stable_and_manifested() -> None:
    route = GeoJsonLineString(((32.0, 39.0), (33.0, 39.0)))
    options = (
        make_option("epdk:b", longitude=32.5, latitude=39.01, power_kw=200),
        make_option("epdk:a", longitude=32.5, latitude=39.01),
        make_option("epdk:early", longitude=32.2, latitude=39.02, power_kw=350),
        make_option("epdk:outside", longitude=32.5, latitude=40.0),
    )
    config = CandidateSelectionConfig(corridor_width_km=5.0, candidate_cap=2)

    first = select_corridor_candidates(route_geometry=route, options=options, config=config)
    second = select_corridor_candidates(
        route_geometry=route, options=tuple(reversed(options)), config=config
    )

    assert first.station_ids == ("epdk:early", "epdk:b")
    assert second.station_ids == first.station_ids
    assert first.eligible_count == 3


def test_corridor_cap_covers_route_progress_instead_of_clustering() -> None:
    route = GeoJsonLineString(((0.0, 39.0), (10.0, 39.0)))
    options = (
        make_option("epdk:progress-20", longitude=2.0, latitude=39.01),
        make_option("epdk:progress-40", longitude=4.0, latitude=39.01),
        make_option("epdk:progress-60", longitude=6.0, latitude=39.01),
        make_option("epdk:cluster-91", longitude=9.1, latitude=39.0),
        make_option("epdk:cluster-92", longitude=9.2, latitude=39.0),
        make_option("epdk:cluster-93", longitude=9.3, latitude=39.0),
        make_option("epdk:cluster-94", longitude=9.4, latitude=39.0),
    )
    config = CandidateSelectionConfig(corridor_width_km=5.0, candidate_cap=4)

    first = select_corridor_candidates(route_geometry=route, options=options, config=config)
    second = select_corridor_candidates(
        route_geometry=route,
        options=tuple(reversed(options)),
        config=config,
    )

    assert set(first.station_ids) >= {
        "epdk:progress-20",
        "epdk:progress-40",
        "epdk:progress-60",
    }
    assert sum(station_id.startswith("epdk:cluster-") for station_id in first.station_ids) == 1
    assert second.station_ids == first.station_ids
    assert {
        min(3, int(candidate.route_progress_fraction * 4)) for candidate in first.candidates
    } == {0, 1, 2, 3}


def test_corridor_selection_is_prefix_monotonic_as_cap_grows() -> None:
    route = GeoJsonLineString(((0.0, 39.0), (10.0, 39.0)))
    options = tuple(
        make_option(
            f"epdk:{index:02d}",
            longitude=index / 2,
            latitude=39.0 + (index % 3) * 0.01,
            power_kw=50 + index * 5,
        )
        for index in range(1, 20)
    )
    selections = [
        select_corridor_candidates(
            route_geometry=route,
            options=options,
            config=CandidateSelectionConfig(corridor_width_km=5.0, candidate_cap=cap),
        ).station_ids
        for cap in (4, 8, 12)
    ]

    assert selections[1][:4] == selections[0]
    assert selections[2][:8] == selections[1]
