from __future__ import annotations

import math
from dataclasses import replace

import pytest

from chargepath import (
    EVRouteOptimizer,
    NoFeasibleRouteError,
    RoadLeg,
    Station,
    VehicleProfile,
    verify_route_plan,
)
from chargepath.energy import charging_minutes
from chargepath.providers import StaticRoadNetwork
from chargepath.speed import (
    PILOT_QUADRATIC_SHARE,
    PILOT_REFERENCE_SPEED_KMH,
    SpeedAwareConsumption,
    SpeedChoiceNetwork,
)

# Example inputs fitted on one Volkswagen ID.3 in the TUM usage dataset (CC BY-NC 4.0); the
# aggregate fit is in docs/benchmarks/field-pilot/tum_id1_v1.json. 75 kW effective cap, half
# power from 65% SOC; the drop starts at 64.9% so the curve never exceeds the fitted step.
ID3_PILOT_CHARGING_CURVE: tuple[tuple[float, float], ...] = (
    (0.0, 75.0),
    (64.9, 75.0),
    (65.0, 37.5),
    (100.0, 37.5),
)

ID3_PILOT_PROFILE = VehicleProfile(
    name="ID.3 pilot example",
    usable_battery_kwh=58.0,
    initial_soc_pct=80.0,
    # Fitted constant driving rate on the pilot's training windows, before the safety factor.
    consumption_kwh_per_100km=19.39,
    max_dc_power_kw=100.0,
    charging_curve=ID3_PILOT_CHARGING_CURVE,
)

MODEL = SpeedAwareConsumption()


def make_vehicle(**changes: object) -> VehicleProfile:
    base = VehicleProfile(
        name="Model EV",
        usable_battery_kwh=60,
        initial_soc_pct=50,
        consumption_kwh_per_100km=20,
        max_dc_power_kw=150,
        reserve_soc_pct=10,
        energy_safety_factor=1.0,
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def leg(origin: str, destination: str, km: float, kmh: float) -> RoadLeg:
    return RoadLeg(origin, destination, km, km * 60 / kmh)


def test_model_reproduces_the_pilot_fit() -> None:
    pilot = 19.38787270528953 + 3.8366453526851596 * ((100 / 100) ** 2 - 0.7529254392590666)
    vehicle = make_vehicle(consumption_kwh_per_100km=19.38787270528953)
    energy = MODEL.leg_energy(leg("a", "b", 100, 100), vehicle)
    assert math.isclose(energy.net_kwh, pilot, rel_tol=1e-12)
    assert energy.peak_kwh == energy.net_kwh
    assert math.isclose(MODEL.rate_factor(PILOT_REFERENCE_SPEED_KMH), 1.0)
    assert 0.1 < PILOT_QUADRATIC_SHARE < 0.2


def test_slower_legs_use_less_energy_and_scale_with_the_safety_factor() -> None:
    vehicle = make_vehicle(energy_safety_factor=1.1)
    fast = MODEL.leg_energy(leg("a", "b", 100, 120), vehicle).net_kwh
    slow = MODEL.leg_energy(leg("a", "b", 100, 90), vehicle).net_kwh
    assert slow < fast
    plain = MODEL.leg_energy(leg("a", "b", 100, 120), make_vehicle()).net_kwh
    assert math.isclose(fast, plain * 1.1)


def test_network_offers_each_leg_at_every_factor() -> None:
    base_leg = leg("a", "b", 100, 100)
    network = SpeedChoiceNetwork(StaticRoadNetwork([base_leg]), (1.0, 0.8))
    variants = network.neighbors("a")
    assert [variant.duration_minutes for variant in variants] == [60.0, 75.0]
    assert all(variant.distance_km == 100 for variant in variants)
    assert variants[0] is base_leg
    assert [network.speed_factor(variant) for variant in variants] == [1.0, 0.8]
    with pytest.raises(ValueError, match="not offered"):
        network.speed_factor(leg("a", "b", 100, 50))


def test_slower_driving_turns_an_infeasible_trip_feasible() -> None:
    # 24 kWh above the reserve. 110 km needs 25.0 kWh at 120 km/h and 23.8 kWh at 108 km/h.
    vehicle = make_vehicle()
    base = StaticRoadNetwork([leg("origin", "destination", 110, 120)])
    optimizer = EVRouteOptimizer(consumption_model=MODEL)
    with pytest.raises(NoFeasibleRouteError):
        optimizer.optimize(
            origin_id="origin",
            destination_id="destination",
            vehicle=vehicle,
            stations={},
            road_network=base,
        )
    network = SpeedChoiceNetwork(base)
    plan = optimizer.optimize(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations={},
        road_network=network,
    )
    assert [network.speed_factor(item) for item in plan.legs] == [0.9]
    assert math.isclose(plan.driving_minutes, 55 / 0.9)
    verify_route_plan(plan, vehicle=vehicle, stations={}, consumption_model=MODEL)


@pytest.mark.parametrize("initial_soc", [30, 70])
@pytest.mark.parametrize("station_kw", [50, 150])
def test_speed_choice_is_never_slower_than_fixed_speed(initial_soc: int, station_kw: int) -> None:
    vehicle = make_vehicle(initial_soc_pct=initial_soc)
    stations = {"hub": Station("hub", "Hub", 39.0, 32.0, station_kw)}
    base = StaticRoadNetwork(
        [
            leg("origin", "hub", 40, 120),
            leg("hub", "destination", 140, 120),
            leg("origin", "destination", 230, 110),
        ]
    )
    optimizer = EVRouteOptimizer(consumption_model=MODEL)
    fixed = optimizer.optimize(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations=stations,
        road_network=base,
    )
    chosen = optimizer.optimize(
        origin_id="origin",
        destination_id="destination",
        vehicle=vehicle,
        stations=stations,
        road_network=SpeedChoiceNetwork(base),
    )
    assert chosen.total_minutes <= fixed.total_minutes + 1e-9
    for plan in (fixed, chosen):
        verify_route_plan(plan, vehicle=vehicle, stations=stations, consumption_model=MODEL)


@pytest.mark.parametrize(("start", "end"), [(10, 50), (10, 80), (60, 70), (65, 100), (0, 100)])
def test_pilot_curve_is_conservative_against_the_fitted_step(start: float, end: float) -> None:
    generic = replace(ID3_PILOT_PROFILE, charging_curve=None)
    fitted = charging_minutes(
        generic, 75.0, start, end, taper_start_pct=65.0, taper_factor=0.5, setup_minutes=0
    )
    curve = charging_minutes(ID3_PILOT_PROFILE, 150.0, start, end, setup_minutes=0)
    assert fitted - 1e-9 <= curve <= fitted + 0.05


def test_pilot_profile_plans_and_replays() -> None:
    assert ID3_PILOT_PROFILE.charging_curve == ID3_PILOT_CHARGING_CURVE
    stations = {"hub": Station("hub", "Hub", 39.0, 32.0, 150)}
    network = StaticRoadNetwork(
        [leg("origin", "hub", 150, 110), leg("hub", "destination", 200, 110)]
    )
    plan = EVRouteOptimizer().optimize(
        origin_id="origin",
        destination_id="destination",
        vehicle=ID3_PILOT_PROFILE,
        stations=stations,
        road_network=network,
    )
    assert len(plan.charging_stops) == 1
    verify_route_plan(plan, vehicle=ID3_PILOT_PROFILE, stations=stations)
