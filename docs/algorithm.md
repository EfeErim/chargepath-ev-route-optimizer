# Algorithm and code layout

## Input graph

`G = (V, E)` is a directed graph whose nodes are the origin, the destination and the candidate
charging stations. Each edge is the route OSRM computed between two of those locations, with road
distance `d(e)`, driving time `t(e)` and optional geometry. The planner never sees individual roads.

A vehicle has usable capacity `B` (kWh), initial and reserve SOC, consumption in kWh/100 km, a
maximum DC power, supported connectors and an energy safety factor. The battery is split into
buckets of `ΔSOC` percentage points and the search state is `(location_id, energy_bucket)`.

## Drive transition

```text
energy_kwh       = distance_km × consumption_kwh_per_100km / 100 × safety_factor
required_buckets = ceil(energy_kwh / bucket_kwh)
```

A drive is legal only if the remaining bucket count is at least the reserve bucket count. Its cost
is the leg's driving time.

The constant formula is the default `ConstantConsumption`. Any object with
`leg_energy(leg, vehicle) -> LegEnergy` can replace it in every strategy and in replay. `LegEnergy`
has a signed `net_kwh` (negative when recuperation recovers more than the leg uses) and a
`peak_kwh`, the largest cumulative draw on the leg:

```text
net_buckets  = ceil(net_kwh / bucket_kwh)      # draws round up, recovered energy rounds down
peak_buckets = ceil(peak_kwh / bucket_kwh)
legal when     bucket - peak_buckets >= reserve_bucket
next bucket  = min(maximum_bucket, bucket - net_buckets)
```

The peak check stops a climb followed by a descent from hiding a reserve violation inside a leg.
Energy is part of the state, not the cost, so negative energy does not break Dijkstra. For the
constant model `net = peak`.

## Charge transition

At a station with a connector the vehicle supports, the search may move to any higher bucket. The
cost is

```text
session_setup_minutes + piecewise_charge_minutes(from_soc, to_soc)
effective_kw = min(station_power_kw, vehicle_max_dc_kw)
```

Above 80% SOC the effective power is multiplied by 0.45, and each session adds 2 setup minutes.
A vehicle may instead carry a `charging_curve` of `(soc_pct, power_kw)` breakpoints from 0 to 100%.
Accepted power is then `min(curve(soc), station_power_kw, vehicle_max_dc_kw)`, which is linear
between breakpoints and cap crossings, so the time is integrated exactly on each interval:

```text
minutes = 60 × Σ (B / 100) × Δsoc × ln(P_end / P_start) / (P_end − P_start)   (Δsoc / P when flat)
```

The project ships no measured vehicle curves; see [research](research.md#open-gaps).

## Objectives and search

Three exact preferences run over the same transitions, each a lexicographic priority:

```text
fastest:               (total_minutes, charging_sessions, distance_km, -arrival_bucket)
shortest_distance:     (distance_km, total_minutes, charging_sessions, -arrival_bucket)
fewest_charging_stops: (charging_sessions, total_minutes, distance_km, -arrival_bucket)
```

Minutes and kilometres are compared after rounding to nine decimal places, so floating-point noise
cannot decide a tie. Dijkstra is exact on the expanded graph: every leg has positive time and
distance and every charge adds time and a session, so an earlier field always rises before the
arrival-bucket field can fall. A popped state is skipped when a higher bucket at the same node was
settled earlier; from a higher bucket every continuation of the lower one is available at no
greater cost, so the objective is unchanged.

The first destination state popped is optimal only within the candidate graph, the SOC grid, the
energy model and the charging approximation. A missing destination state raises
`NoFeasibleRouteError` rather than returning a partial route.

With `|V|` nodes and `K` buckets there are `|V| × K` states, each with its outgoing legs plus up to
`K` charge actions.

## Heuristics and baselines

**Greedy fixed-80.** Follows the shortest-driving-time path while the next leg keeps the reserve.
When blocked it charges once at the current compatible station to the smallest bucket at or above
80%, or moves to the directly reachable unvisited compatible station with the lowest remaining
driving time (then lowest leg time, then station id). It may fail where exact search succeeds;
that is reported as an unavailable strategy.

**Lookahead heuristic** (`chargepath.lookahead`, evaluation only, never a route option). From node
and bucket `b` it scores each direct leg to the destination or to an unvisited compatible station
with strictly lower remaining driving time, and each departure bucket `d ≥ b` when the current node
is a compatible station:

```text
charge_minutes(b → d) + leg_minutes + remaining_minutes(next)
  + [setup + 60 × missing_kwh / effective_kw(next)]   when energy is missing on arrival
```

The missing-energy estimate ignores taper and is optimistic. Ties go to the lower departure bucket,
then the lower node id. It commits to the best pair and never backtracks.

**Road-only baseline.** Dijkstra on driving time with the node sequence as final tie-break, then
an SOC check with no charging. A reserve violation makes it infeasible.

`CompetitiveRoutePlanner` runs the three exact preferences and greedy fixed-80, replays each plan,
and merges value-identical plans into one option that keeps every strategy alias.

## Independent replay

`verify_route_plan` ignores the search state. It replays the stops and legs from the floored
initial SOC, recomputes energy and charging time, and enforces the rounded-up reserve after every
leg. Given the road network and endpoints, it also checks that the plan starts and ends there and
that every leg exists in the network with the same distance and duration. Route options, the
synthetic benchmark and the real-route evaluation all use the full check.

The replay shares `charging_minutes` and the bucket functions with the optimizer, so it catches
search and reconstruction bugs but not model bugs. `tests/test_properties.py` covers the model with
an exact-rational reference solver, and `tests/test_verification.py` checks that tampered plans are
rejected.

Invariants: every drive has enough discretized energy and ends at or above reserve; energy never
exceeds capacity; charging happens only at declared, compatible stations; total time equals the
reconstructed actions; identical inputs give identical plans; every returned option passes replay.

## Code layout

All under `src/chargepath/`. The core imports no web framework, map library or HTTP client.

| Module | Role |
|---|---|
| `models.py`, `validation.py` | Immutable vehicle, station, leg and plan types; shared input validation |
| `energy.py`, `speed.py`, `terrain.py` | Energy buckets, consumption models (speed and terrain are opt-in), charging time |
| `optimizer.py`, `alternatives.py`, `baselines.py`, `lookahead.py` | Exact search, option planner, road-only baseline, lookahead heuristic |
| `verification.py`, `redundancy.py` | Independent replay; structural per-stop fallback note for the demo |
| `providers/` | In-memory road network and the OSRM Route/Table adapter behind an injectable transport |
| `station_data.py`, `corridor.py` | EPDK normalization, CCS2 DC projection, snapshot loading; capped corridor selection |
| `fixtures.py`, `evaluation.py` | Synthetic fixture loading and the synthetic benchmark |
| `real_route_benchmark.py`, `real_route_collection.py`, `real_route_evaluation.py` | Route selection, OSRM collection, offline evaluation |
| `demo/`, `web/` | Request schema, planning services, response formatting, loopback server, Pyodide entry point; Leaflet UI that never computes feasibility |

Invalid domain data raises `ValueError` at construction. OSRM timeouts and malformed responses stay
provider errors, and `null` Table cells become unreachable pairs, never zero-cost legs.
