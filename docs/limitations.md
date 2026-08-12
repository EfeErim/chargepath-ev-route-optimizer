# Limitations

## Product boundary

ChargePath is a planning demonstration, not safety-certified navigation. It does not monitor a moving vehicle or guarantee that a selected charger will be usable.

## Road data

- OpenStreetMap completeness and turn restrictions vary by location.
- OSRM travel time is not automatically live traffic.
- Candidate graph pruning can omit a better route if configured too aggressively. Local OSRM mode
  widens an infeasible capped search only up to the configured local limit; it does not prove that a
  feasible capped result is globally best among every corridor station. Remote OSRM requests never
  auto-expand because the project has no third-party quota or SLA guarantee.
- OSRM Table distance describes the fastest route selected by its profile, not a globally
  shortest-distance route.

## Station data

- The 2026-08-09 EPDK spike verified access and one observed schema, but quota, update cadence,
  cross-snapshot identifier behavior, and redistribution conditions remain undocumented.
- Static station presence does not prove availability, access, compatibility, price, or working status at arrival.
- A coordinate can represent the site rather than the correct road entrance or carriageway.
- The current public EPDK Swagger does not publish a response schema. The implemented ingestion layer
  uses a synthetic reviewed fixture and fails closed on unreviewed schema fields.

## Energy model

- Constant consumption ignores speed, grade, traffic, wind, temperature, precipitation, cabin load, payload, tire state, degradation, and driving style.
- The safety factor is not a substitute for calibration.
- Regenerative braking is not modeled.
- Discrete buckets conservatively round consumption and can reject narrowly feasible continuous solutions.
  The demo retries a coarse 5% infeasible search at 2% and labels the returned resolution, but it
  still cannot establish feasibility in a continuous or calibrated vehicle model.

## Charging model

- The piecewise taper is generic.
- Vehicle and battery temperature, charger voltage/current limits, power sharing, handshake time, and thermal throttling are not modeled.
- Station power is a cap, not a promise of delivered power.
- The first release supports modeled CCS2 DC compatibility only. AC charging and other connectors are
  outside its optimization claim.

## Optimization claim

The foundation search is optimal only within the supplied candidate graph, discrete SOC grid, and model assumptions. It is not globally optimal for the real, time-dependent transportation system.

The fastest, shortest-distance, and fewest-session options are each exact only for their declared
lexicographic objective under those same boundaries. The greedy fixed-80 strategy is intentionally
heuristic and may fail even when exact search finds a feasible plan. More options do not imply that
one is operationally safer or more reliable with static station data.

## Validation boundary

Synthetic tests prove software invariants against synthetic inputs. Real-world SOC accuracy requires logged trips, a defined vehicle set, and a separate calibration protocol.

The M3 benchmark contains eight small, hand-audited synthetic cases. Its measured advantage for
partial charging, 0-minute pruning gap, and millisecond runtimes apply only to that fixture and one
recorded machine run. They do not establish national-scale performance, general pruning safety, or
real-world trip-time accuracy.

## Demo boundary

The default UI flow uses the bundled synthetic fixture. Optional OSRM, EPDK snapshot, tile, or
geocoding integrations may fail independently and do not turn the project into an offline navigation
product or a live charging service.
