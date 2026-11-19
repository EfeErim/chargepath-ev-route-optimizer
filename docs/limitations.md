# Limitations

ChargePath is a planning demonstration, not navigation. It does not follow a moving vehicle and
cannot promise that a charger will work.

## Model

- Each exact option is optimal only on the candidate graph, the discrete SOC grid and the models
  below, not for the real, time-dependent road and charging system.
- Default consumption is constant with a safety factor. It ignores speed, grade, traffic, wind,
  temperature, payload, tires, degradation and driving style. The opt-in speed and terrain models
  are fitted to one car each; the terrain model failed its held-out check and needs caller-supplied
  elevation, which a DEM would misread at bridges and tunnels.
- Rounding energy up to buckets can reject a narrowly feasible trip. The demo retries at 2% after a
  5% failure, which is still a discrete model.
- Charging uses a generic two-segment taper unless a curve is supplied, and the project ships no
  measured curves. Battery temperature, charger limits, power sharing and handshake time are not
  modeled. Station power is a cap, not delivered power. Only CCS2 DC charging is modeled.

## Roads and stations

- OSRM times come from one OpenStreetMap extract and a car profile, not live traffic. Table
  distances follow the fastest route, so "shortest distance" compares those legs only.
- The candidate cap bounds the OSRM Table request and can hide a better charger. Local mode widens
  only an infeasible search, only up to its limit; remote mode never widens.
- The EPDK snapshot shows presence, not availability, access, price or working status. Coordinates
  may mark a site rather than its entrance. Quota, update cadence, identifier stability and
  redistribution terms are undocumented, and the service has returned HTTP 429.
- The demo's per-stop fallback note is graph structure only: other compatible chargers in direct
  reach (nearest twelve by driving time) and the added minutes without that stop.

## Evidence

- The synthetic suite is eight hand-audited cases; its timings and 0-minute pruning gap say nothing
  about scale or other graphs.
- The real-route benchmark is 24 routes on one extract and one snapshot, assumes every compatible
  candidate is usable, and needs the frozen inputs, which are not in Git, to rerun. Runtime is per
  batch, separate from OSRM and the browser. See [results](results.md#real-route-benchmark).
- The [field pilots](results.md#field-pilot--volkswagen-id3) use a few cars and recorded speeds;
  the VW charging type is inferred and the BMW data has three hillier held-out sections. They are
  not ground truth for the real-route plans.
