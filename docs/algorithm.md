# Algorithm design

## Input graph

Let `G = (V, E)` be a directed graph whose nodes are the origin, destination, and candidate charging stations. A road leg `e = (u, v)` has:

- road distance `d(e)`;
- driving time `t(e)`; and
- optional geometry supplied by the road provider.

The candidate graph is not the raw OpenStreetMap road graph. Each edge represents the route computed
by the configured OSRM graph and profile between two relevant locations; it is not claimed as
real-world ground truth.

## Vehicle state

A vehicle defines:

- usable battery capacity `B` in kWh;
- initial SOC;
- minimum reserve SOC;
- consumption in kWh/100 km;
- maximum DC charging power;
- supported DC connector types; and
- an energy safety factor.

The battery is discretized into buckets of `ΔSOC` percentage points. The search state is:

```text
(location_id, energy_bucket)
```

## Drive transition

Baseline energy for a leg:

```text
energy_kwh = distance_km × consumption_kwh_per_100km / 100 × safety_factor
```

Required buckets are rounded upward:

```text
required_buckets = ceil(energy_kwh / bucket_kwh)
```

The upward rounding is intentional. A drive transition is legal only when the remaining bucket count is at least the reserve bucket count.

### Pluggable consumption models

The constant formula above is the default `ConstantConsumption` model. Any object with
`leg_energy(leg, vehicle) -> LegEnergy` can replace it in every strategy and in replay. A
`LegEnergy` has a signed `net_kwh` (negative when recuperation recovers more than the leg uses) and
a `peak_kwh`, the largest cumulative draw anywhere on the leg:

```text
net_buckets  = ceil(net_kwh / bucket_kwh)      # draws round up, recovered energy rounds down
peak_buckets = ceil(peak_kwh / bucket_kwh)
legal when     bucket - peak_buckets >= reserve_bucket
next bucket  = min(maximum_bucket, bucket - net_buckets)
```

The peak check keeps a climb followed by a descent from hiding a reserve violation inside the leg,
and recovered energy never raises the battery above full. Search remains Dijkstra because the
priority metrics (time, distance, sessions) stay non-negative; energy is part of the state, not of
the cost, so negative energy does not affect label-setting correctness. For the constant model
`net = peak`, and every result is identical to the formula above.

Transition cost:

```text
drive_cost = road_leg.duration_minutes
```

## Charge transition

At a station whose normalized connector is supported by the vehicle, the optimizer may move from
the current bucket to any higher bucket. An incompatible station creates no charge transition. The
charge action cost is:

```text
session_setup_minutes + piecewise_charge_minutes(from_soc, to_soc)
```

Effective charging power is limited by both station and vehicle:

```text
effective_kw = min(station_power_kw, vehicle_max_dc_kw)
```

Above the taper threshold, effective power is multiplied by a conservative taper factor. The model is an auditable approximation, not a physical battery simulator.

A vehicle may instead carry a `charging_curve`: `(soc_pct, power_kw)` breakpoints from 0 to 100%
SOC, strictly increasing in SOC with positive power. Accepted power is then
`min(curve(soc), station_power_kw, vehicle_max_dc_kw)`. That function is linear between the curve
breakpoints and the points where the curve crosses the cap, so the charge time is integrated exactly
on each such interval:

```text
minutes = 60 × Σ (B / 100) × Δsoc × ln(P_end / P_start) / (P_end − P_start)   (Δsoc / P when flat)
```

The generic taper arguments are ignored for a vehicle with a curve. The project ships no
vehicle-specific curves; see [research](research.md#open-gaps) for the data gate.

## Exact objective variants

The authoritative search uses additive non-negative metrics and a lexicographic priority. Three
preferences compete over the same legal drive and charge transitions:

```text
fastest:               (total_minutes, charging_sessions, distance_km)
shortest_distance:     (distance_km, total_minutes, charging_sessions)
fewest_charging_stops: (charging_sessions, total_minutes, distance_km)
```

Secondary fields are explicit deterministic tie-break objectives, not hidden weighted sums. The
default remains `fastest`, preserving the foundation solver's modeled objective.

Future objective terms may include station reliability, waiting time, price, or uncertainty only when evidence exists.

## Search

Dijkstra's label-setting algorithm is applied to the expanded state graph because all components of
each lexicographic transition cost are non-negative.

The first popped destination state is optimal within:

- the supplied candidate-road graph;
- the discrete SOC grid;
- the baseline energy model; and
- the piecewise charging approximation.

It is not claimed to be globally optimal in a continuous, dynamic, real-world road and charging system.

## Greedy fixed-target competitor

The separate greedy algorithm repeatedly follows the deterministic shortest-driving-time road path
while its next leg preserves reserve. When blocked, it first uses the current compatible station, if
available and unused, to charge once to the smallest bucket at or above 80%. Otherwise it selects a
directly reachable, unvisited compatible station by:

1. lower remaining shortest-driving-time duration to the destination;
2. lower current-to-station duration; and
3. stable station identifier.

It never raises its target above 80% and may therefore fail on a case solved by exact search. That
failure is reported as an unavailable strategy, not converted into a partial plan.

## Shortest-driving-time baseline

The road-only baseline applies Dijkstra to road-leg duration, using the full lexicographic node
sequence as its final tie-break. It then evaluates the selected path against the same conservative
SOC buckets without inserting any charging action. A reserve violation therefore makes the baseline
explicitly infeasible even when a charging-aware strategy can solve the trip.

## Option construction

`CompetitiveRoutePlanner` runs all four strategies in a fixed order. Every feasible candidate is
independently replayed. Value-identical actionable `RoutePlan` objects are merged into one option while
retaining all strategy aliases; different charging amounts on the same road sequence remain distinct
because they are different trip actions.

## Plan reconstruction

Each predecessor stores either:

- a drive action with a `RoadLeg`; or
- a charge action with arrival/departure buckets and added energy.

Reconstruction yields ordered road legs, charging stops, SOC values, and time totals. A missing destination state raises `NoFeasibleRouteError` instead of returning a misleading partial route.

## Independent replay

`verify_route_plan` does not inspect Dijkstra distances or predecessor state. It replays the returned
charging stops and legs from the conservatively floored initial SOC, recomputes charge duration and
energy consumption, and enforces the rounded-up reserve after every leg. When given the road
network and the requested endpoints, it also checks that the plan starts and ends at those nodes and
that every leg is one the network offers with the same distance and duration, so a plan cannot
shorten or invent a road. Route options, the synthetic benchmark and the real-route evaluation pass
all three. Benchmark and demo output must pass this replay before being treated as valid evidence.

The replay calls the same `charging_minutes` and leg-bucket functions as the optimizer, so it
catches search and reconstruction errors but not errors in those functions. The property tests in
`tests/test_properties.py` cover that side with a separate exact-arithmetic reference, and
`tests/test_verification.py` checks that the replay rejects tampered plans.

## Expected complexity

With `|V|` candidate nodes, `K` energy buckets, `|E|` road legs, and charge actions to higher buckets,
one exact search remains intentionally small and readable. The option planner currently runs three
exact preferences plus one greedy traversal. It shares only the deterministic per-leg energy-bucket
calculation across those runs; search state, objectives, predecessor maps, and independent replay stay
separate. On a 52-node complete synthetic graph this reduced the measured composed planner mean from
941 ms to 606 ms without changing any returned option.

## Correctness invariants

1. Every returned drive action has enough discretized energy.
2. Remaining energy after every drive is at or above reserve.
3. Energy never exceeds usable capacity.
4. Charging occurs only at declared stations.
5. Charging occurs only through a connector supported by the vehicle.
6. Total time equals reconstructed drive plus charge actions.
7. Identical inputs produce identical plans.
8. Every public route option passes independent replay before it is returned.
