# Experiment Plan

## M3 execution status

Completed on 2026-08-09 against the checksum-pinned, project-authored synthetic v1 fixture. The
versioned manifest, normalized correctness/comparison/SOC-grid/pruning artifact, and machine-specific
runtime artifact are under `data/benchmarks/m3/v1/` and `docs/evidence/m3/`. The reviewed tables and
claim boundaries are in [results](results.md).

Reproduction command:

```powershell
python scripts/run_m3_benchmark.py --warmups 3 --repetitions 20
```

## Decision question

Does joint charging-stop and partial-charge optimization produce valid, faster plans than explicitly
defined road-only and greedy charging baselines on a fixed, reviewable benchmark?

The experiment reports the answer even when a hypothesis is not supported. Passing software checks
is not treated as evidence of superiority.

## Algorithms

### Baseline A — Shortest-driving-time path only

Select the path with minimum OSRM/fixture driving duration, using lexicographic node sequence as the
final tie-break. Evaluate energy feasibility without inserting charging stops. OSRM Table distance is
the distance along that fastest route, not a shortest-distance objective.

### Baseline B — Greedy fixed-target charging

From the current node, compute the deterministic shortest-driving-time path to the destination using
Baseline A's lexicographic tie-break. Drive its next leg when reserve remains feasible. Otherwise, if
the current node is an unused compatible station and arrival SOC is below the fixed target, charge
there once to the smallest bucket at or above 80% and recompute. If that is unavailable or still
insufficient, enumerate unvisited compatible stations reachable by one candidate-graph leg at the
current energy and having a finite shortest-driving-time path to the destination. Exclude a candidate
when its arrival SOC is already at or above the fixed target because the policy would add no energy
there.

Rank the remaining candidates by lower shortest driving duration from the station to the destination,
then lower current-to-station driving duration, then stable station identifier. Drive to the first
candidate and charge once to the smallest SOC bucket at or above 80%; never select or charge at the
same station twice. Repeat
from that station. If the next planned leg is infeasible and no eligible station exists, return
explicit infeasible. This baseline deliberately does not raise its fixed target above 80%, even when
doing so would rescue a trip.

### Challenger A — ChargePath fastest

Dijkstra over `(location, SOC bucket)` with compatible CCS2 DC charging, partial charging, and
piecewise charge time, minimizing `(total minutes, charging sessions, distance)` lexicographically.

### Challenger B — ChargePath shortest distance

The same exact legal state graph, minimizing `(distance, total minutes, charging sessions)`
lexicographically. This is a route option, not a promise of lower energy consumption.

### Challenger C — ChargePath fewest charging sessions

The same exact legal state graph, minimizing `(charging sessions, total minutes, distance)`
lexicographically. Session count is not used as a proxy for charger reliability.

## Benchmark manifest

Every case must record:

- scenario identifier and topology class;
- fixture checksum and schema version;
- origin, destination, vehicle, SOC step, reserve, and station inputs;
- expected-feasibility source: a hand-audited witness path for feasible cases, or an independently
  reasoned exhaustion certificate/reference oracle for infeasible cases;
- algorithm version and normalized result serialization version.

Required topology classes are direct feasible, one-stop, multi-stop, detour choice, connector
incompatible, exact-reserve boundary, discretization-rejected, and infeasible. Each class needs at
least one fixture. One-factor sensitivity cases must change only the named input.

The algorithm under evaluation must never generate its own expected-feasibility label. Generated
cases are accepted only when their label is backed by the recorded witness or independent oracle.

## Sensitivity dimensions

- battery capacity: small, medium, large;
- initial SOC: low, medium, high;
- consumption: efficient, nominal, adverse;
- reserve: 5%, 10%, 20%;
- station power: mixed 50/90/150/250 kW;
- SOC step: 10%, 5%, 2%;
- candidate graph: unpruned and corridor-pruned on small audit cases.

These dimensions are not required to form a full Cartesian product. The generated manifest must show
which values and pairwise interactions are covered so coverage cannot be inferred from prose.

## Metrics

| Metric | Definition | Direction |
|---|---|---|
| Valid-plan rate | Returned plans passing independent replay / returned plans | Must be 100% |
| Feasibility rate | Feasible reference cases returning a valid plan / feasible reference cases | Higher |
| False-feasible count | Reference-infeasible cases returning a plan | Must be zero |
| Reserve violations | Replayed driven legs ending below rounded reserve | Must be zero |
| Total trip time | Driving + setup + charging minutes | Lower |
| Charging time | Sum of verified charging action minutes | Lower |
| Detour distance | Plan distance − shortest-driving-time path distance | Lower, contextual |
| Charging stops | Count of charging sessions | Contextual |
| Distinct options | Unique verified actionable plans after exact-plan deduplication | Contextual |
| Runtime | Median and p95 wall-clock time after declared warm-ups | Lower |
| SOC-grid gap | Difference across paired 10/5/2% cases | Lower, contextual |
| Pruning gap | Pruned-plan time − unpruned-plan time on the same small graph | Lower |

## Correctness gates

1. Every returned plan replays through `verify_route_plan`.
2. Every drive leg preserves reserve after conservative rounding.
3. Infeasible cases never return partial plans.
4. Direct feasible cases add no charging stop.
5. Incompatible connectors never create charge transitions.
6. Repeated runs are byte-for-byte stable after normalized serialization.
7. Benchmark input counts reconcile with generated result counts.
8. Strategy aliases remain attached when multiple algorithms select an identical actionable plan.
9. An unavailable heuristic is reported separately from an infeasible trip.

## Runtime protocol

- Declare warm-up and measured repetition counts before running the suite.
- Record Python version, platform, CPU, command, and timestamp.
- Use a monotonic high-resolution clock and run without network calls.
- Report median and p95; never promote the best single run.

## Evidence protocol

- Keep fixture, manifest, algorithm, and serialization versions with each result.
- Preserve the raw machine-readable artifact and its checksum.
- Separate synthetic benchmark claims from later real-route observations.
- Update `docs/results.md` only from generated, reviewed output.
