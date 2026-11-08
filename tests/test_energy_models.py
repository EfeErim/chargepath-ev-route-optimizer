from __future__ import annotations

import math
from dataclasses import dataclass, replace

import pytest

from chargepath import (
    CompetitiveRoutePlanner,
    EVRouteOptimizer,
    NoFeasibleRouteError,
    PlanVerificationError,
    RoadLeg,
    Station,
    VehicleProfile,
    verify_route_plan,
)
from chargepath.energy import (
    ConstantConsumption,
    LegEnergy,
    charging_minutes,
    drive_bucket,
    energy_for_distance,
    leg_buckets,
    required_energy_buckets,
)
from chargepath.providers import StaticRoadNetwork


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


@dataclass(frozen=True, slots=True)
class TableConsumption:
    """Per-leg (net, peak) kWh from a fixed table; any other leg uses the constant model."""

    table: tuple[tuple[tuple[str, str], float, float], ...]

    def leg_energy(self, leg: RoadLeg, vehicle: VehicleProfile) -> LegEnergy:
        for key, net, peak in self.table:
            if key == (leg.origin_id, leg.destination_id):
                return LegEnergy(net_kwh=net, peak_kwh=peak)
        return ConstantConsumption().leg_energy(leg, vehicle)


# ---- consumption model ---------------------------------------------------------------------


@pytest.mark.parametrize("distance", [0.1, 1, 33.3, 99.99, 150, 412.7])
def test_constant_model_matches_the_original_bucket_rounding(distance: float) -> None:
    vehicle = make_vehicle(energy_safety_factor=1.1)
    bucket_kwh = 60 * 5 / 100
    leg = RoadLeg("a", "b", distance, 10)
    expected = required_energy_buckets(
        energy_for_distance(distance, 20, 1.1),
        bucket_kwh,
    )
    assert leg_buckets(ConstantConsumption().leg_energy(leg, vehicle), bucket_kwh) == (
        expected,
        expected,
    )


def test_recovered_energy_rounds_down_and_draws_round_up() -> None:
    assert leg_buckets(LegEnergy(net_kwh=-5.9, peak_kwh=0.1), 3.0) == (-1, 1)
    assert leg_buckets(LegEnergy(net_kwh=6.1, peak_kwh=6.1), 3.0) == (3, 3)


def test_drive_bucket_caps_at_full_and_checks_the_peak() -> None:
    assert drive_bucket(19, (-4, 0), reserve_bucket=2, maximum_bucket=20) == 20
    assert drive_bucket(5, (1, 4), reserve_bucket=2, maximum_bucket=20) is None
    assert drive_bucket(6, (1, 4), reserve_bucket=2, maximum_bucket=20) == 5


def test_leg_energy_rejects_a_peak_below_net() -> None:
    with pytest.raises(ValueError, match="peak_kwh"):
        LegEnergy(net_kwh=5, peak_kwh=4)


def test_recuperation_makes_a_downhill_trip_feasible_and_replays() -> None:
    # 50% of 60 kWh is 30 kWh; the two legs need 2 x 18 kWh at the constant rate.
    network = StaticRoadNetwork([RoadLeg("a", "b", 90, 60), RoadLeg("b", "c", 90, 60)])
    vehicle = make_vehicle()
    with pytest.raises(NoFeasibleRouteError):
        EVRouteOptimizer().optimize(
            origin_id="a", destination_id="c", vehicle=vehicle, stations={}, road_network=network
        )
    downhill = TableConsumption(((("b", "c"), -3.0, 2.0),))
    plan = EVRouteOptimizer(consumption_model=downhill).optimize(
        origin_id="a", destination_id="c", vehicle=vehicle, stations={}, road_network=network
    )
    # 30 kWh = 10 buckets of 3 kWh; -6 buckets, then +1 (3 kWh recovered, rounded down).
    assert plan.arrival_soc_pct == 25
    verify_route_plan(plan, vehicle=vehicle, stations={}, consumption_model=downhill)
    with pytest.raises(PlanVerificationError):
        verify_route_plan(plan, vehicle=vehicle, stations={})


def test_a_mid_leg_peak_below_reserve_is_rejected() -> None:
    network = StaticRoadNetwork([RoadLeg("a", "b", 10, 10)])
    # Net use is small but the leg climbs 26 kWh before descending: the reserve breaks mid-leg.
    pass_road = TableConsumption(((("a", "b"), 1.0, 26.0),))
    with pytest.raises(NoFeasibleRouteError):
        EVRouteOptimizer(consumption_model=pass_road).optimize(
            origin_id="a",
            destination_id="b",
            vehicle=make_vehicle(),
            stations={},
            road_network=network,
        )


def test_lower_consumption_never_slows_the_fastest_plan() -> None:
    stations = {"s": Station("s", "Hub", 39, 32, 150)}
    legs = [RoadLeg("a", "s", 120, 70), RoadLeg("s", "c", 160, 95), RoadLeg("a", "c", 260, 170)]
    network = StaticRoadNetwork(legs)
    vehicle = make_vehicle(initial_soc_pct=60)
    baseline = EVRouteOptimizer().optimize(
        origin_id="a", destination_id="c", vehicle=vehicle, stations=stations, road_network=network
    )
    lighter = TableConsumption(
        tuple(
            ((leg.origin_id, leg.destination_id), 0.14 * leg.distance_km, 0.14 * leg.distance_km)
            for leg in legs
        )
    )
    improved = CompetitiveRoutePlanner(consumption_model=lighter).plan(
        origin_id="a", destination_id="c", vehicle=vehicle, stations=stations, road_network=network
    )
    fastest = improved.options[0].plan
    assert fastest.total_minutes <= baseline.total_minutes


# ---- charging curve ------------------------------------------------------------------------


def test_flat_curve_matches_the_generic_model_below_the_taper() -> None:
    generic = make_vehicle()
    flat = make_vehicle(charging_curve=((0, 150), (100, 150)))
    for low, high in ((10, 40), (0, 80), (35, 75)):
        assert charging_minutes(flat, 400, low, high) == pytest.approx(
            charging_minutes(generic, 400, low, high), rel=1e-12
        )


def test_station_power_caps_the_curve() -> None:
    vehicle = make_vehicle(charging_curve=((0, 50), (50, 250), (100, 50)))
    capped = charging_minutes(vehicle, 40, 10, 90, setup_minutes=0)
    assert capped == pytest.approx(60 * 60 * 0.8 / 40, rel=1e-12)


def test_curve_integral_matches_numeric_integration() -> None:
    curve = ((0.0, 60.0), (20.0, 140.0), (60.0, 120.0), (85.0, 45.0), (100.0, 20.0))
    vehicle = make_vehicle(charging_curve=curve, max_dc_power_kw=130)
    station_kw = 125.0
    low, high = 7.5, 93.0

    def power(soc: float) -> float:
        for (a, pa), (b, pb) in zip(curve, curve[1:], strict=False):
            if a <= soc <= b:
                return min(pa + (pb - pa) * (soc - a) / (b - a), 125.0, 130.0)
        raise AssertionError

    steps = 200_000
    width = (high - low) / steps
    hours = sum(0.6 * width / power(low + (index + 0.5) * width) for index in range(steps))
    assert charging_minutes(vehicle, station_kw, low, high, setup_minutes=0) == pytest.approx(
        60 * hours, rel=1e-6
    )


def test_curve_ignores_the_generic_taper_arguments() -> None:
    vehicle = make_vehicle(charging_curve=((0, 100), (100, 100)))
    first = charging_minutes(vehicle, 150, 20, 95, taper_start_pct=50, taper_factor=0.2)
    second = charging_minutes(vehicle, 150, 20, 95)
    assert first == second == pytest.approx(2 + 60 * 0.6 * 75 / 100)


@pytest.mark.parametrize(
    "curve",
    [
        ((0, 100),),
        ((5, 100), (100, 50)),
        ((0, 100), (90, 50)),
        ((0, 100), (50, 80), (50, 60), (100, 20)),
        ((0, 100), (100, 0)),
        ((0, 100), (100, math.nan)),
        [(0, 100), (100, 50)],
    ],
)
def test_invalid_curves_are_rejected(curve: object) -> None:
    with pytest.raises(ValueError, match="charging_curve"):
        make_vehicle(charging_curve=curve)


def test_curve_plans_replay_and_tampering_is_caught() -> None:
    stations = {"s": Station("s", "Hub", 39, 32, 150)}
    network = StaticRoadNetwork([RoadLeg("a", "s", 100, 60), RoadLeg("s", "c", 200, 120)])
    vehicle = make_vehicle(charging_curve=((0, 120), (60, 140), (80, 70), (100, 20)))
    plan = EVRouteOptimizer().optimize(
        origin_id="a", destination_id="c", vehicle=vehicle, stations=stations, road_network=network
    )
    assert plan.charging_stops
    verify_route_plan(plan, vehicle=vehicle, stations=stations)
    with pytest.raises(PlanVerificationError, match="charging minutes"):
        verify_route_plan(plan, vehicle=replace(vehicle, charging_curve=None), stations=stations)
