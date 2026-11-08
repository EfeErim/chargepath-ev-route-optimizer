from __future__ import annotations

import pytest

from chargepath import (
    EVRouteOptimizer,
    NoFeasibleRouteError,
    RoadLeg,
    VehicleProfile,
    verify_route_plan,
)
from chargepath.providers import StaticRoadNetwork
from chargepath.terrain import (
    GRAVITY_M_PER_S2,
    GradeAwareConsumption,
    LegElevation,
    elevation_from_profile,
)

VEHICLE = VehicleProfile(
    name="Terrain EV",
    usable_battery_kwh=60,
    initial_soc_pct=50,
    consumption_kwh_per_100km=18,
    max_dc_power_kw=150,
    reserve_soc_pct=10,
    energy_safety_factor=1.0,
)


def test_profile_hysteresis_ignores_small_reversals() -> None:
    assert elevation_from_profile([100, 100.4, 99.8, 100.3, 100]) == LegElevation(0, 0)
    assert elevation_from_profile([100, 150, 149.5, 120, 180], noise_m=1) == LegElevation(110, 30)
    with pytest.raises(ValueError):
        elevation_from_profile([100])
    with pytest.raises(ValueError):
        LegElevation(-1, 0)


def test_flat_leg_matches_the_constant_model() -> None:
    model = GradeAwareConsumption(((("a", "b"), LegElevation(0, 0)),), mass_kg=1500)
    energy = model.leg_energy(RoadLeg("a", "b", 100, 60), VEHICLE)
    assert energy.net_kwh == energy.peak_kwh == pytest.approx(18)


def test_climb_and_descent_follow_potential_energy() -> None:
    model = GradeAwareConsumption(
        ((("a", "b"), LegElevation(500, 300)),),
        mass_kg=1800,
        drive_efficiency=0.9,
        regen_efficiency=0.5,
    )
    energy = model.leg_energy(RoadLeg("a", "b", 50, 40), VEHICLE)
    weight_kwh_per_m = 1800 * GRAVITY_M_PER_S2 / 3.6e6
    assert energy.peak_kwh == pytest.approx(9 + weight_kwh_per_m * 500 / 0.9)
    assert energy.net_kwh == pytest.approx(energy.peak_kwh - weight_kwh_per_m * 300 * 0.5)


def test_missing_elevation_is_an_error_not_a_flat_assumption() -> None:
    model = GradeAwareConsumption((), mass_kg=1500)
    with pytest.raises(ValueError, match="no elevation"):
        model.leg_energy(RoadLeg("a", "b", 10, 10), VEHICLE)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mass_kg", 0),
        ("drive_efficiency", 0),
        ("drive_efficiency", 1.1),
        ("regen_efficiency", -0.1),
    ],
)
def test_invalid_constants_are_rejected(field: str, value: float) -> None:
    arguments: dict[str, object] = {"elevations": (), "mass_kg": 1500, field: value}
    with pytest.raises(ValueError):
        GradeAwareConsumption(**arguments)  # type: ignore[arg-type]


def test_planner_uses_terrain_for_feasibility_and_replay() -> None:
    network = StaticRoadNetwork([RoadLeg("a", "b", 60, 40), RoadLeg("b", "c", 60, 40)])
    flat = GradeAwareConsumption(
        ((("a", "b"), LegElevation(0, 0)), (("b", "c"), LegElevation(0, 0))), mass_kg=1800
    )
    mountain = GradeAwareConsumption(
        ((("a", "b"), LegElevation(1500, 0)), (("b", "c"), LegElevation(0, 1500))), mass_kg=1800
    )
    plan = EVRouteOptimizer(consumption_model=flat).optimize(
        origin_id="a", destination_id="c", vehicle=VEHICLE, stations={}, road_network=network
    )
    verify_route_plan(plan, vehicle=VEHICLE, stations={}, consumption_model=flat)
    with pytest.raises(NoFeasibleRouteError):
        EVRouteOptimizer(consumption_model=mountain).optimize(
            origin_id="a", destination_id="c", vehicle=VEHICLE, stations={}, road_network=network
        )
