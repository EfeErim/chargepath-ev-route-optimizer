"""Differential, property-based, and metamorphic checks for the exact planner.

The reference below is written from ``docs/algorithm.md`` and deliberately shares no arithmetic with
``chargepath.energy``: every energy, bucket, and charge-time value is computed with exact rational
numbers from the decimal inputs. Agreement therefore checks the optimizer's search *and* its
floating-point rounding, not only consistency between the optimizer and the replay helper.
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from decimal import Decimal
from fractions import Fraction

import pytest
from hypothesis import HealthCheck, event, given, settings
from hypothesis import strategies as st

from chargepath import (
    EVRouteOptimizer,
    GreedyFixedTargetOptimizer,
    NoFeasibleRouteError,
    RoadLeg,
    RoutePlan,
    RoutePreference,
    ShortestDrivingTimeBaseline,
    Station,
    VehicleProfile,
    verify_route_plan,
)
from chargepath.providers import StaticRoadNetwork

# CI runs a fixed, derandomized sample so the gate stays deterministic. Set
# CHARGEPATH_PROPERTY_EXAMPLES (for example to 5000) for a deeper randomized search.
_STRESS_EXAMPLES = int(os.environ.get("CHARGEPATH_PROPERTY_EXAMPLES", "0"))
PROPERTY_SETTINGS = settings(
    max_examples=_STRESS_EXAMPLES or 150,
    deadline=None,
    derandomize=not _STRESS_EXAMPLES,
    database=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)

TAPER_START = Fraction(80)
TAPER_FACTOR = Fraction(45, 100)
SETUP_MINUTES = Fraction(2)

Objective = tuple[Fraction, Fraction, Fraction]


@dataclass(frozen=True)
class Case:
    """A small trip described by decimal text so the reference can read exact values."""

    nodes: tuple[str, ...]
    legs: tuple[tuple[str, str, str, str], ...]
    stations: tuple[tuple[str, str], ...]
    battery_kwh: str
    initial_soc: str
    consumption: str
    max_dc_kw: str
    reserve_soc: str
    safety: str
    soc_step: int

    @property
    def origin(self) -> str:
        return self.nodes[0]

    @property
    def destination(self) -> str:
        return self.nodes[-1]

    def vehicle(self) -> VehicleProfile:
        return VehicleProfile(
            name="Property EV",
            usable_battery_kwh=float(self.battery_kwh),
            initial_soc_pct=float(self.initial_soc),
            consumption_kwh_per_100km=float(self.consumption),
            max_dc_power_kw=float(self.max_dc_kw),
            reserve_soc_pct=float(self.reserve_soc),
            energy_safety_factor=float(self.safety),
        )

    def station_map(self) -> dict[str, Station]:
        return {
            node_id: Station(node_id, f"Station {node_id}", 39.0, 32.0, float(power))
            for node_id, power in self.stations
        }

    def network(self) -> StaticRoadNetwork:
        return StaticRoadNetwork(
            RoadLeg(origin, destination, float(distance), float(duration))
            for origin, destination, distance, duration in self.legs
        )


def _decimal(low: int, high: int, places: int = 1) -> st.SearchStrategy[str]:
    scale = 10**places
    return st.integers(low * scale, high * scale).map(
        lambda value: str(Decimal(value).scaleb(-places))
    )


def _doubled(text: str) -> str:
    return str(Decimal(text) * 2)


@st.composite
def cases(draw: st.DrawFn) -> Case:
    node_count = draw(st.integers(2, 6))
    nodes = tuple(f"n{index}" for index in range(node_count))
    # A forward chain keeps most trips reachable; extra legs (including backward ones) add choices
    # and cycles.
    chain = [(nodes[index], nodes[index + 1]) for index in range(node_count - 1)]
    extras = [(a, b) for a in nodes for b in nodes if a != b and (a, b) not in chain]
    chosen = list(chain)
    if extras:
        chosen += draw(
            st.lists(st.sampled_from(extras), max_size=min(len(extras), 10), unique=True)
        )
    legs = tuple((a, b, draw(_decimal(5, 260)), draw(_decimal(4, 180))) for a, b in sorted(chosen))
    interior = nodes[1:-1]
    station_nodes = draw(st.lists(st.sampled_from(nodes), unique=True, max_size=len(nodes)))
    every_interior_node_charges = draw(st.booleans())
    if interior and every_interior_node_charges:
        station_nodes = sorted(set(station_nodes) | set(interior))
    stations = tuple(
        (node_id, draw(st.sampled_from(("22", "50", "75.5", "150", "350"))))
        for node_id in sorted(station_nodes)
    )
    reserve = draw(st.integers(0, 30))
    initial = draw(st.integers(reserve, 100))
    return Case(
        nodes=nodes,
        legs=legs,
        stations=stations,
        battery_kwh=draw(_decimal(20, 110)),
        initial_soc=str(initial),
        consumption=draw(_decimal(10, 30)),
        max_dc_kw=draw(st.sampled_from(("40", "100", "150.5", "270"))),
        reserve_soc=str(reserve),
        safety=draw(st.sampled_from(("1", "1.05", "1.1", "1.25"))),
        soc_step=draw(st.sampled_from((5, 10, 20, 25))),
    )


class ExactModel:
    """Exact rational restatement of the documented energy and charging model."""

    def __init__(self, case: Case) -> None:
        self.case = case
        self.step = case.soc_step
        self.battery = Fraction(case.battery_kwh)
        self.bucket_kwh = self.battery * self.step / 100
        self.max_bucket = 100 // self.step
        self.initial_bucket = math.floor(Fraction(case.initial_soc) / self.step)
        self.reserve_bucket = math.ceil(Fraction(case.reserve_soc) / self.step)
        self.per_km = Fraction(case.consumption) / 100 * Fraction(case.safety)
        self.max_dc = Fraction(case.max_dc_kw)
        self.station_power = {node: Fraction(power) for node, power in case.stations}

    def leg_buckets(self, distance: str) -> int:
        return math.ceil(Fraction(distance) * self.per_km / self.bucket_kwh)

    def charge_minutes(self, node: str, from_bucket: int, to_bucket: int) -> Fraction:
        low = Fraction(from_bucket * self.step)
        high = Fraction(to_bucket * self.step)
        power = min(self.station_power[node], self.max_dc)
        below = max(Fraction(0), min(high, TAPER_START) - min(low, TAPER_START))
        above = max(Fraction(0), high - max(low, TAPER_START))
        kwh_below = self.battery * below / 100
        kwh_above = self.battery * above / 100
        return SETUP_MINUTES + 60 * (kwh_below / power + kwh_above / (power * TAPER_FACTOR))


def _priority(
    minutes: Fraction, distance: Fraction, stops: int, pref: RoutePreference
) -> Objective:
    sessions = Fraction(stops)
    if pref is RoutePreference.FASTEST:
        return (minutes, sessions, distance)
    if pref is RoutePreference.SHORTEST_DISTANCE:
        return (distance, minutes, sessions)
    return (sessions, minutes, distance)


def reference_optimum(case: Case, pref: RoutePreference) -> Objective | None:
    """Exhaustive label-correcting search over the expanded state graph, in exact arithmetic."""

    model = ExactModel(case)
    start = (case.origin, model.initial_bucket)
    labels: dict[tuple[str, int], tuple[Fraction, Fraction, int]] = {
        start: (Fraction(0), Fraction(0), 0)
    }
    outgoing: dict[str, list[tuple[str, str, str]]] = {}
    for leg_origin, leg_destination, leg_km_text, leg_min_text in case.legs:
        outgoing.setdefault(leg_origin, []).append((leg_destination, leg_km_text, leg_min_text))

    changed = True
    while changed:
        changed = False
        for (node, bucket), (minutes, distance, stops) in list(labels.items()):
            moves: list[tuple[tuple[str, int], tuple[Fraction, Fraction, int]]] = []
            for destination, leg_km, leg_min in outgoing.get(node, ()):
                remaining = bucket - model.leg_buckets(leg_km)
                if remaining >= model.reserve_bucket:
                    moves.append(
                        (
                            (destination, remaining),
                            (minutes + Fraction(leg_min), distance + Fraction(leg_km), stops),
                        )
                    )
            if node in model.station_power:
                for target in range(bucket + 1, model.max_bucket + 1):
                    added = model.charge_minutes(node, bucket, target)
                    moves.append(((node, target), (minutes + added, distance, stops + 1)))
            for state, label in moves:
                current = labels.get(state)
                if current is None or _priority(*label, pref) < _priority(*current, pref):
                    labels[state] = label
                    changed = True

    finals = [
        _priority(*label, pref) for (node, _), label in labels.items() if node == case.destination
    ]
    return min(finals) if finals else None


def exact_replay(case: Case, plan: RoutePlan) -> None:
    """Replay a returned plan with exact arithmetic and the reserve invariant."""

    model = ExactModel(case)
    distances = {(o, d): km for o, d, km, _ in case.legs}
    bucket = model.initial_bucket
    stops = list(plan.charging_stops)
    for index, leg in enumerate(plan.legs):
        node = plan.node_ids[index]
        if stops and stops[0].station_id == node:
            stop = stops.pop(0)
            assert node in model.station_power
            assert stop.arrival_soc_pct == bucket * model.step
            target = round(stop.departure_soc_pct / model.step)
            assert bucket < target <= model.max_bucket
            assert float(model.charge_minutes(node, bucket, target)) == pytest.approx(
                stop.charging_minutes, rel=1e-9, abs=1e-9
            )
            bucket = target
        bucket -= model.leg_buckets(distances[(leg.origin_id, leg.destination_id)])
        assert bucket >= model.reserve_bucket, "reserve violated under exact arithmetic"
    assert not stops
    assert plan.arrival_soc_pct == bucket * model.step


def _plan_priority(plan: RoutePlan, pref: RoutePreference) -> tuple[float, float, float]:
    sessions = float(len(plan.charging_stops))
    if pref is RoutePreference.FASTEST:
        return (plan.total_minutes, sessions, plan.total_distance_km)
    if pref is RoutePreference.SHORTEST_DISTANCE:
        return (plan.total_distance_km, plan.total_minutes, sessions)
    return (sessions, plan.total_minutes, plan.total_distance_km)


def _solve(case: Case, pref: RoutePreference = RoutePreference.FASTEST) -> RoutePlan | None:
    try:
        return EVRouteOptimizer(preference=pref, soc_step_pct=case.soc_step).optimize(
            origin_id=case.origin,
            destination_id=case.destination,
            vehicle=case.vehicle(),
            stations=case.station_map(),
            road_network=case.network(),
        )
    except NoFeasibleRouteError:
        return None


def _approx_objective(actual: tuple[float, float, float], expected: Objective) -> None:
    for got, want in zip(actual, expected, strict=True):
        assert got == pytest.approx(float(want), rel=1e-9, abs=1e-9)


@PROPERTY_SETTINGS
@given(cases(), st.sampled_from(list(RoutePreference)))
def test_optimizer_matches_exact_reference(case: Case, pref: RoutePreference) -> None:
    expected = reference_optimum(case, pref)
    plan = _solve(case, pref)
    event("feasible" if expected is not None else "infeasible")
    if plan is not None:
        event(f"charging stops: {min(len(plan.charging_stops), 3)}")
    if expected is None:
        assert plan is None
        return
    assert plan is not None
    _approx_objective(_plan_priority(plan, pref), expected)
    verify_route_plan(
        plan, vehicle=case.vehicle(), stations=case.station_map(), soc_step_pct=case.soc_step
    )
    exact_replay(case, plan)


@PROPERTY_SETTINGS
@given(cases())
def test_fastest_is_never_slower_than_any_other_strategy(case: Case) -> None:
    fastest = _solve(case)
    others: list[RoutePlan | None] = [_solve(case, pref) for pref in RoutePreference]
    try:
        others.append(
            GreedyFixedTargetOptimizer(soc_step_pct=case.soc_step).optimize(
                origin_id=case.origin,
                destination_id=case.destination,
                vehicle=case.vehicle(),
                stations=case.station_map(),
                road_network=case.network(),
            )
        )
    except NoFeasibleRouteError:
        others.append(None)
    try:
        others.append(
            ShortestDrivingTimeBaseline(soc_step_pct=case.soc_step).optimize(
                origin_id=case.origin,
                destination_id=case.destination,
                vehicle=case.vehicle(),
                road_network=case.network(),
            )
        )
    except NoFeasibleRouteError:
        others.append(None)
    feasible = [plan for plan in others if plan is not None]
    if fastest is None:
        assert not feasible
        return
    for plan in feasible:
        verify_route_plan(
            plan, vehicle=case.vehicle(), stations=case.station_map(), soc_step_pct=case.soc_step
        )
        assert fastest.total_minutes <= plan.total_minutes + 1e-9


def _metamorphic(
    transform: Callable[[Case], Case | None],
) -> Callable[[Case], None]:
    """The transformed case must be at least as feasible and never slower."""

    def check(case: Case) -> None:
        changed = transform(case)
        if changed is None:
            return
        before = _solve(case)
        after = _solve(changed)
        if before is None:
            return
        assert after is not None, "a relaxing change made the trip infeasible"
        assert after.total_minutes <= before.total_minutes + 1e-9

    return check


def _more_initial_soc(case: Case) -> Case | None:
    initial = int(case.initial_soc)
    return None if initial >= 100 else replace(case, initial_soc=str(min(100, initial + 17)))


def _lower_reserve(case: Case) -> Case | None:
    reserve = int(case.reserve_soc)
    return None if reserve == 0 else replace(case, reserve_soc=str(reserve // 2))


def _extra_station(case: Case) -> Case | None:
    existing = {node for node, _ in case.stations}
    free = [node for node in case.nodes if node not in existing]
    if not free:
        return None
    return replace(case, stations=tuple(sorted((*case.stations, (free[0], "150")))))


def _more_power(case: Case) -> Case | None:
    if not case.stations:
        return None
    boosted = tuple((node, _doubled(power)) for node, power in case.stations)
    return replace(case, stations=boosted, max_dc_kw=_doubled(case.max_dc_kw))


def _extra_leg(case: Case) -> Case | None:
    present = {(o, d) for o, d, _, _ in case.legs}
    if (case.origin, case.destination) in present:
        return None
    return replace(case, legs=(*case.legs, (case.origin, case.destination, "30", "20")))


@PROPERTY_SETTINGS
@given(
    cases(),
    st.sampled_from([_more_initial_soc, _lower_reserve, _extra_station, _more_power, _extra_leg]),
)
def test_relaxing_changes_never_hurt(case: Case, transform: Callable[[Case], Case | None]) -> None:
    _metamorphic(transform)(case)


@PROPERTY_SETTINGS
@given(cases())
def test_relabeling_nodes_preserves_the_optimum(case: Case) -> None:
    mapping = {node: f"z{len(case.nodes) - index}_{node}" for index, node in enumerate(case.nodes)}
    renamed = replace(
        case,
        nodes=tuple(mapping[node] for node in case.nodes),
        legs=tuple((mapping[o], mapping[d], km, mins) for o, d, km, mins in case.legs),
        stations=tuple(sorted((mapping[node], power) for node, power in case.stations)),
    )
    for pref in RoutePreference:
        before = _solve(case, pref)
        after = _solve(renamed, pref)
        assert (before is None) == (after is None)
        if before is not None and after is not None:
            for got, want in zip(
                _plan_priority(after, pref), _plan_priority(before, pref), strict=True
            ):
                assert got == pytest.approx(want, rel=1e-9, abs=1e-9)


@PROPERTY_SETTINGS
@given(cases())
def test_identical_inputs_give_identical_plans(case: Case) -> None:
    assert _solve(case) == _solve(case)
