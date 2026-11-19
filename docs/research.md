# Background

The project solves the single-vehicle electric vehicle shortest path problem with charging stops:
one car, one origin and destination, battery energy as a constrained resource that stations
replenish, and travel plus charging time as the objective. It is not the fleet EVRP.

## Sources the design uses

| Source | What the project takes from it |
|---|---|
| Artmeier et al. (2010), [The Shortest Path Problem Revisited](https://doi.org/10.1007/978-3-642-16111-7_35) | SOC must be part of the routing state. |
| Merting, Schwan and Strehler (2015), [Resource Recovering Nodes](https://drops.dagstuhl.de/entities/document/10.4230/OASIcs.ATMOS.2015.29) | Charging is an action at nodes during the search, not post-processing. |
| Baum et al. (2020), [Shortest Feasible Paths with Charging Stops](https://doi.org/10.1287/trsc.2018.0889) | Exact methods carry continuous SOC functions; this project uses a discrete grid and measures its gap (E2). |
| Baum, Sauer, Wagner and Zündorf (2017), [Consumption Profiles in Route Planning for EVs](https://doi.org/10.4230/LIPIcs.SEA.2017.19) | Speedups exist for continental scale; at about 0.3 s per real-route trip none are needed. |
| Strehler, Merting and Schwan (2017), [Energy-Efficient Shortest Routes for Electric and Hybrid Vehicles](https://ideas.repec.org/a/eee/transb/v103y2017icp111-135.html) | A recuperating model must cap energy at full and keep costs non-negative so label setting stays valid. |
| Montoya et al. (2017), [EVRP with Nonlinear Charging Function](https://doi.org/10.1016/j.trb.2017.02.004) | Partial charging with a piecewise taper instead of linear full charges. |
| Levin, Duell and Waller (2014), [Road Grade and Energy Consumption](https://doi.org/10.3141/2427-03); Yuksel and Michalek (2015), [Regional Temperature and EV Efficiency](https://doi.org/10.1021/es505621s) | Grade and temperature matter; the safety factor does not replace them. |
| Sweda, Dolinskaya and Klabjan (2017), [Adaptive Routing and Recharging Policies](https://doi.org/10.1287/trsc.2016.0724); Dastpak et al. (2024), [EVSPP with Occupancy Information](https://doi.org/10.1016/j.trc.2023.104411) | Availability and occupancy need live data the static snapshot lacks, so plans make no availability claim. |

Data and protocol sources: the [OSRM HTTP API](https://project-osrm.org/docs/v26.6.1/http)
(coordinates are `longitude,latitude`, Table cells may be `null`), the
[EPDK charging-station Swagger](https://apigateway.epdk.gov.tr/sarjIstasyonlari?swagger), the
[OSM tile policy](https://operations.osmfoundation.org/policies/tiles/) and
[Nominatim policy](https://operations.osmfoundation.org/policies/nominatim/), and
[Hypothesis](https://doi.org/10.21105/joss.01891) for the property tests.

## Related open-source planners

[evnav](https://github.com/giraldeau/evnav) runs Dijkstra over a complete charger graph built with
libosrm, with constant Wh/km and an average charging power up to a maximum SOC.
[TheMarex/charge](https://github.com/TheMarex/charge) plans time-optimal routes on OSM with speed-
and slope-dependent consumption. Commercial planners such as ABRP state per-vehicle consumption,
weather, elevation and live data, so they model the car better than this project. Neither group
publishes replay of every returned plan or a frozen benchmark, and no plan-quality comparison with
them was run.

## Open gaps

| Gap | Status |
|---|---|
| Vehicle-specific charging curves | No redistributable measured source found. [open-ev-data](https://github.com/open-ev-data/open-ev-data-dataset) v1.24.0 defines `charging.dc_charge_curve`, but none of its 1,189 vehicles has one. |
| Speed, grade, weather and payload in energy | The field pilots were mixed (see [results](results.md#field-pilot--volkswagen-id3)); the default stays constant. |
| Queues, outages, fallback reliability, live replanning | Need occupancy, failure or live vehicle data; out of scope. |
