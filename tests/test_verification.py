"""The replay checker must reject plans that break a modeled contract.

Every published "zero reserve violations" count depends on these checks, so each one is tested
with a plan that is valid except for one tampered field.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

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
from chargepath.energy import ConstantConsumption, LegEnergy, charging_minutes
from chargepath.models import ChargingStop
from chargepath.providers import StaticRoadNetwork
from chargepath.verification import replay_route_plan

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


def _tampered_stop(plan: RoutePlan, **changes: Any) -> RoutePlan:
    return replace(plan, charging_stops=(replace(plan.charging_stops[0], **changes),))


TAMPERING: list[tuple[str, Callable[[RoutePlan], dict[str, Any]], str]] = [
    (
        "incompatible-connector",
        lambda plan: {"stations": {"s": replace(STATIONS["s"], connector_type="CHADEMO")}},
        "connector is incompatible",
    ),
    ("unknown-station", lambda plan: {"stations": {}}, "no matching station"),
    (
        "leg-below-reserve",
        lambda plan: {"vehicle": replace(VEHICLE, consumption_kwh_per_100km=25)},
        "violates reserve",
    ),
    # Net use on a -> s leaves 10%, exactly the reserve; the 15 kWh peak dips to 5%.
    (
        "peak-below-reserve",
        lambda plan: {"consumption_model": PeakOnFirstLeg()},
        "violates reserve",
    ),
    (
        "arrival-soc",
        lambda plan: {"plan": replace(plan, arrival_soc_pct=plan.arrival_soc_pct + 5)},
        "arrival SOC",
    ),
    (
        "charging-energy",
        lambda plan: {
            "plan": _tampered_stop(
                plan, energy_added_kwh=plan.charging_stops[0].energy_added_kwh + 1
            )
        },
        "charging energy",
    ),
    (
        "stop-off-path",
        lambda plan: {"plan": _tampered_stop(plan, station_id="elsewhere")},
        "not at its plan node",
    ),
    (
        "stop-at-another-visit",
        lambda plan: {"plan": _tampered_stop(plan, node_index=0)},
        "not at its plan node",
    ),
    ("other-origin", lambda plan: {"origin_id": "s"}, "requested origin"),
    ("other-destination", lambda plan: {"destination_id": "s"}, "requested destination"),
]


def test_untampered_plan_passes_against_its_network(plan: RoutePlan) -> None:
    verify_route_plan(
        plan,
        vehicle=VEHICLE,
        stations=STATIONS,
        road_network=NETWORK,
        origin_id="a",
        destination_id="c",
    )


@pytest.mark.parametrize(
    ("tamper", "match"), [(t, m) for _, t, m in TAMPERING], ids=[i for i, _, _ in TAMPERING]
)
def test_one_tampered_field_is_rejected(
    plan: RoutePlan, tamper: Callable[[RoutePlan], dict[str, Any]], match: str
) -> None:
    arguments: dict[str, Any] = {"plan": plan, "vehicle": VEHICLE, "stations": STATIONS}
    arguments.update(tamper(plan))
    with pytest.raises(PlanVerificationError, match=match):
        verify_route_plan(arguments.pop("plan"), **arguments)


def test_shortened_road_leg_is_rejected_against_the_network(plan: RoutePlan) -> None:
    # Halving s -> c keeps every other contract intact, so only the network check catches it.
    shorter = replace(plan.legs[1], distance_km=plan.legs[1].distance_km / 2)
    tampered = RoutePlan.assemble(
        node_ids=plan.node_ids,
        legs=(plan.legs[0], shorter),
        charging_stops=plan.charging_stops,
        arrival_soc_pct=plan.arrival_soc_pct + 20,
    )
    verify_route_plan(tampered, vehicle=VEHICLE, stations=STATIONS)
    with pytest.raises(PlanVerificationError, match="not offered by the road network"):
        verify_route_plan(tampered, vehicle=VEHICLE, stations=STATIONS, road_network=NETWORK)


def test_a_stop_is_replayed_at_the_visit_it_names() -> None:
    # The plan passes "s" twice and charges on the second pass, when the battery is lower.
    network = StaticRoadNetwork(
        [RoadLeg("a", "s", 60, 45), RoadLeg("s", "b", 30, 20), RoadLeg("b", "s", 30, 20)]
        + [RoadLeg("s", "c", 120, 90)]
    )
    legs = tuple(
        next(leg for leg in network.neighbors(origin) if leg.destination_id == destination)
        for origin, destination in (("a", "s"), ("s", "b"), ("b", "s"), ("s", "c"))
    )
    stop = ChargingStop(
        station_id="s",
        node_index=3,
        arrival_soc_pct=0.0,
        departure_soc_pct=60.0,
        energy_added_kwh=36.0,
        charging_minutes=charging_minutes(VEHICLE, 150, 0.0, 60.0),
    )
    revisit = RoutePlan.assemble(
        node_ids=("a", "s", "b", "s", "c"),
        legs=legs,
        charging_stops=(stop,),
        arrival_soc_pct=20.0,
    )
    vehicle = replace(VEHICLE, initial_soc_pct=40, reserve_soc_pct=0)
    events = verify_route_plan(revisit, vehicle=vehicle, stations=STATIONS, road_network=network)
    assert [(event.kind, event.node_id) for event in events][3:5] == [
        ("arrive", "s"),
        ("charge", "s"),
    ]
    first_visit = replace(revisit, charging_stops=(replace(stop, node_index=1),))
    with pytest.raises(PlanVerificationError, match="charging arrival SOC"):
        verify_route_plan(first_visit, vehicle=vehicle, stations=STATIONS)


def test_replay_counts_reserve_violations_instead_of_raising(plan: RoutePlan) -> None:
    # The peak model dips below the reserve on a -> s only; the stated SOC values still hold.
    replay = replay_route_plan(
        plan, vehicle=VEHICLE, stations=STATIONS, consumption_model=PeakOnFirstLeg()
    )
    assert replay.reserve_violations == 1
    assert replay_route_plan(plan, vehicle=VEHICLE, stations=STATIONS).reserve_violations == 0
