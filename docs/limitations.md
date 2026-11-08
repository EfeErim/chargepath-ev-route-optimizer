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

- The 2026-08-09 EPDK spike verified access and one observed schema, and a 2026-11-05 response
  still matched it. A later request that day returned HTTP 429, so a rate limit exists, but quota,
  update cadence, cross-snapshot identifier behavior, and redistribution conditions remain
  undocumented.
- Static station presence does not prove availability, access, compatibility, price, or working status at arrival.
- The per-stop fallback note in the demo is structural only. It counts other compatible chargers in
  direct reach on the same candidate graph (nearest twelve by driving time) and the added minutes when
  the stop's charger is removed before planning. It knows nothing about occupancy, outages, or
  reliability, and it is not a probability that a fallback will work.
- A coordinate can represent the site rather than the correct road entrance or carriageway.
- The current public EPDK Swagger does not publish a response schema. The implemented ingestion layer
  uses a synthetic reviewed fixture and fails closed on unreviewed schema fields.

## Energy model

- Constant consumption ignores speed, grade, traffic, wind, temperature, precipitation, cabin load, payload, tire state, degradation, and driving style.
- The safety factor is not a substitute for calibration.
- An experimental terrain model (`chargepath.terrain`) adds climb and recuperation energy from
  caller-supplied per-leg ascent and descent. It failed its pre-registered held-out check (lower
  hillier-section error, higher whole-trip error) and is not the default. The project supplies no
  elevation data; a digital elevation model sampled along OSRM geometry would also misread bridges,
  tunnels, and cuttings.
- Regenerative braking is not modeled by the default constant model. The consumption interface accepts
  signed per-leg energy, but any such model needs its own calibration evidence.
- Discrete buckets conservatively round consumption and can reject narrowly feasible continuous solutions.
  The demo retries a coarse 5% infeasible search at 2% and labels the returned resolution, but it
  still cannot establish feasibility in a continuous or calibrated vehicle model.

## Charging model

- The piecewise taper is generic. A caller may supply a vehicle charging curve, but the project ships
  none, and a curve still ignores temperature, preconditioning, and power sharing.
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

The synthetic benchmark contains eight small, hand-audited synthetic cases. Its measured advantage for
partial charging, 0-minute pruning gap, and millisecond runtimes apply only to that fixture and one
recorded machine run. They do not establish national-scale performance, general pruning safety, or
real-world trip-time accuracy.

The real-route benchmark covers 24 selected routes on one OSM extract and one static EPDK snapshot. Its 624 scenario
outcomes include repeated routes at different settings. The evaluated solver assumes each compatible
candidate can be used. The fixed-80 comparison combines stop selection and charging amounts.
Benchmark runtime is measured per batch, separately from OSRM, and excludes browser latency.
Exact reproduction requires the frozen input bytes. See [real-route results](results.md#real-route-benchmark).

The separate [public telemetry pilots](results.md#field-pilot--volkswagen-id3) evaluate energy and
charging on a small number of vehicles using recorded speeds. VW charging type is inferred and
charger capabilities are unavailable. The BMW pilot includes only three hillier held-out sections.
Neither pilot provides ground truth for the real-route plans.

## Demo boundary

The default UI flow uses the bundled synthetic fixture. Optional OSRM, EPDK snapshot, tile, or
geocoding integrations may fail independently and do not turn the project into an offline navigation
product or a live charging service.
