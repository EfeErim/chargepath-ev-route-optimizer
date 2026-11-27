from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from chargepath import (
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
)
from chargepath.providers import StaticRoadNetwork


@pytest.fixture
def vehicle() -> VehicleProfile:
    return VehicleProfile(
        name="Test EV",
        usable_battery_kwh=60,
        initial_soc_pct=80,
        consumption_kwh_per_100km=20,
        max_dc_power_kw=150,
    )


def test_energy_rounds_draws_up_and_recovered_energy_down() -> None:
    assert energy_for_distance(100, 20, 1.1) == pytest.approx(22)
    assert leg_buckets(LegEnergy(6.01, 6.01), 3) == (3, 3)
    assert leg_buckets(LegEnergy(6, 6), 3) == (2, 2)
    assert leg_buckets(LegEnergy(1e-15, 1e-15), 3) == (1, 1)
    assert leg_buckets(LegEnergy(net_kwh=-5.9, peak_kwh=0.1), 3.0) == (-1, 1)


def test_drive_bucket_caps_at_full_and_checks_the_peak() -> None:
    assert drive_bucket(19, (-4, 0), reserve_bucket=2, maximum_bucket=20) == 20
    assert drive_bucket(5, (1, 4), reserve_bucket=2, maximum_bucket=20) is None
    assert drive_bucket(6, (1, 4), reserve_bucket=2, maximum_bucket=20) == 5


def test_charging_above_taper_threshold_is_slower(vehicle: VehicleProfile) -> None:
    below_taper = charging_minutes(vehicle, 150, 60, 80, setup_minutes=0)
    above_taper = charging_minutes(vehicle, 150, 80, 100, setup_minutes=0)
    assert above_taper > below_taper
    assert charging_minutes(vehicle, 150, 50, 50, setup_minutes=2) == 0


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


@pytest.mark.parametrize(
    "curve", [((5, 100), (100, 50)), ((0, 100), (50, 80), (50, 60), (100, 20))]
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


def test_drive_bucket_matches_the_reserve_definition_on_every_small_case() -> None:
    """Every point on the leg stays at or above the reserve, over all small integer cases."""
    maximum = 10
    for start in range(maximum + 1):
        for reserve in range(5):
            for peak in range(13):
                for net in range(-5, peak + 1):
                    end = min(maximum, start - net)
                    lowest_point = min(start, start - peak, end)
                    expected = end if lowest_point >= reserve else None
                    assert (
                        drive_bucket(
                            start, (net, peak), reserve_bucket=reserve, maximum_bucket=maximum
                        )
                        == expected
                    ), (start, reserve, peak, net)
