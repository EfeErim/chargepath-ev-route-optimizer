"""The replay checker must reject plans that break a modeled contract.

Every published "zero reserve violations" count depends on these checks, so each one is tested
with a plan that is valid except for one tampered field.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from chargepath import (
    EVRouteOptimizer,
    PlanVerificationError,
    RoadLeg,
    RoutePlan,
    Station,
    VehicleProfile,
    verify_route_plan,
)
from chargepath.energy import ConstantConsumption, LegEnergy
from chargepath.providers import StaticRoadNetwork

VEHICLE = VehicleProfile(
    name="Test EV",
    usable_battery_kwh=60,
    initial_soc_pct=30,
    consumption_kwh_per_100km=20,
    max_dc_power_kw=150,
    reserve_soc_pct=10,
    energy_safety_factor=1.0,
)
STATIONS = {"s": Station("s", "Hub", 39.0, 32.0, 150)}
# a -> s uses 12 kWh (20%), s -> c uses 24 kWh (40%), so the car must charge at s.
NETWORK = StaticRoadNetwork([RoadLeg("a", "s", 60, 45), RoadLeg("s", "c", 120, 90)])


@dataclass(frozen=True, slots=True)
class PeakOnFirstLeg:
    """Same net energy as the constant model, but a 15 kWh peak on a -> s."""

    def leg_energy(self, leg: RoadLeg, vehicle: VehicleProfile) -> LegEnergy:
        energy = ConstantConsumption().leg_energy(leg, vehicle)
        if (leg.origin_id, leg.destination_id) == ("a", "s"):
            return LegEnergy(net_kwh=energy.net_kwh, peak_kwh=15.0)
        return energy


@pytest.fixture(scope="module")
def plan() -> RoutePlan:
    result = EVRouteOptimizer().optimize(
        origin_id="a",
        destination_id="c",
        vehicle=VEHICLE,
        stations=STATIONS,
        road_network=NETWORK,
    )
    assert [stop.station_id for stop in result.charging_stops] == ["s"]
    return result


def test_untampered_plan_passes(plan: RoutePlan) -> None:
    verify_route_plan(plan, vehicle=VEHICLE, stations=STATIONS)


def test_incompatible_connector_is_rejected(plan: RoutePlan) -> None:
    stations = {"s": replace(STATIONS["s"], connector_type="CHADEMO")}
    with pytest.raises(PlanVerificationError, match="connector is incompatible"):
        verify_route_plan(plan, vehicle=VEHICLE, stations=stations)


def test_unknown_charging_station_is_rejected(plan: RoutePlan) -> None:
    with pytest.raises(PlanVerificationError, match="no matching station"):
        verify_route_plan(plan, vehicle=VEHICLE, stations={})


def test_leg_that_ends_below_reserve_is_rejected(plan: RoutePlan) -> None:
    thirstier = replace(VEHICLE, consumption_kwh_per_100km=25)
    with pytest.raises(PlanVerificationError, match="violates reserve"):
        verify_route_plan(plan, vehicle=thirstier, stations=STATIONS)


def test_peak_below_reserve_inside_a_leg_is_rejected(plan: RoutePlan) -> None:
    # Net use on a -> s leaves 10%, exactly the reserve; the 15 kWh peak dips to 5%.
    with pytest.raises(PlanVerificationError, match="violates reserve"):
        verify_route_plan(
            plan, vehicle=VEHICLE, stations=STATIONS, consumption_model=PeakOnFirstLeg()
        )


def test_tampered_arrival_soc_is_rejected(plan: RoutePlan) -> None:
    with pytest.raises(PlanVerificationError, match="arrival SOC"):
        verify_route_plan(
            replace(plan, arrival_soc_pct=plan.arrival_soc_pct + 5),
            vehicle=VEHICLE,
            stations=STATIONS,
        )


def test_tampered_charging_energy_is_rejected(plan: RoutePlan) -> None:
    stop = replace(
        plan.charging_stops[0], energy_added_kwh=plan.charging_stops[0].energy_added_kwh + 1
    )
    with pytest.raises(PlanVerificationError, match="charging energy"):
        verify_route_plan(
            replace(plan, charging_stops=(stop,)),
            vehicle=VEHICLE,
            stations=STATIONS,
        )


def test_charging_stop_off_the_path_is_rejected(plan: RoutePlan) -> None:
    stop = replace(plan.charging_stops[0], station_id="elsewhere")
    with pytest.raises(PlanVerificationError):
        verify_route_plan(
            replace(plan, charging_stops=(stop,)),
            vehicle=VEHICLE,
            stations=STATIONS,
        )
