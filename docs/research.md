# Research Review

Review date: 2026-08-05

URLs, access notes, and their role in the project are indexed in
[the source registry](source_registry.md).

## Research question

How can a portfolio-sized system compute a feasible and explainable intercity trip for one battery-electric vehicle, choosing charging stops and charging amounts while reusing a mature road-network router for provider-computed road legs?

## Problem classification

The project is **not** the classical Electric Vehicle Routing Problem (EVRP), which normally schedules a fleet across many customers and constraints. It is closer to the **Electric Vehicle Shortest Path Problem**:

- one vehicle;
- one origin and destination;
- battery energy as a constrained resource;
- charging stations as resource-recovering nodes; and
- travel plus charging time as the primary objective.

This distinction keeps the mathematical model aligned with the intended product and avoids importing fleet-routing complexity that the interface never needs.

## Literature synthesis

| Source | Main contribution | Design implication for ChargePath |
|---|---|---|
| Artmeier et al. (2010), [The Shortest Path Problem Revisited](https://www.isp.uni-luebeck.de/research/publications/shortest-path-problem-revisited-optimal-routing-electric-vehicles) | Frames EV routing as a shortest-path problem with battery constraints and possible recuperation. | SOC must be part of the routing state; a road-shortest path alone is insufficient. |
| Merting, Schwan, and Strehler (2015), [Resource Recovering Nodes](https://drops.dagstuhl.de/entities/document/10.4230/OASIcs.ATMOS.2015.29) | Extends constrained shortest paths with nodes that replenish the constrained resource. | Charging is modeled as an action at selected nodes rather than as a post-processing step. |
| Baum et al. (2019), [Shortest Feasible Paths with Charging Stops](https://doi.org/10.1287/trsc.2018.0889) | Models varying charger power and charging stops; shows that the realistic problem is theoretically hard but practically solvable with engineering techniques. | The MVP can use a small state-space search; continental speedups are deferred to a measured need. |
| Sweda and Klabjan (2017), [Adaptive Routing and Recharging Policies](https://doi.org/10.1287/trsc.2016.0724) | Treats route viability as sensitive to charging-infrastructure availability and studies adaptive decisions. | Static-data plans must not imply live certainty. A fallback recommendation would require fresh reliability or availability evidence and is outside the static first release. |
| Schoenberg and Dressler (2021), [Reducing Waiting Times at Charging Stations](https://arxiv.org/abs/2102.06503) | Adds waiting-time information and adaptive rerouting to reduce total trip time. | Queue and occupancy are future risk terms, not invented inputs in the static MVP. |
| Montoya et al. (2017), [EVRP with Nonlinear Charging Function](https://doi.org/10.1016/j.trb.2017.02.004) | Demonstrates that linear full-charge assumptions miss the nonlinear shape of real charging and uses piecewise linear approximation. | Use partial charging and an explicit taper approximation; document that it is not vehicle-specific calibration. |
| Levin, Duell, and Waller (2014), [Effect of Road Grade on Energy Consumption](https://doi.org/10.3141/2427-03) | Shows that road grade can materially affect energy and eco-routing. | Constant consumption is a baseline; elevation belongs in a later calibrated model. |

## Official platform and data findings

### Road network

[OSRM](https://project-osrm.org/) is an open-source routing engine over OpenStreetMap data. It provides route, nearest, table, match, and trip services. Its route and table APIs match the project's boundary: obtain graph/profile-dependent road legs and pairwise travel costs, then solve the EV-specific state problem in Python.

The public OSRM demo is useful for manual development but is not a project-owned service. A
user-configured local or self-hosted instance with a pinned backend release, routing profile, and
regional OpenStreetMap extract is the reproducible integration target. M1 setup notes may choose a
deployment method; the plan does not commit the product to a container runtime.

### Map display

[Leaflet](https://leafletjs.com/) is sufficient for a local interactive map. OpenStreetMap data is open, but the foundation's public tile servers are donation-funded and best-effort. The [tile policy](https://operations.osmfoundation.org/policies/tiles/) requires attribution, identification, and caching, and forbids bulk tile downloading.

### Address search

The public [Nominatim service policy](https://operations.osmfoundation.org/policies/nominatim/) limits use to one request per second and explicitly forbids client-side autocomplete. Therefore the planned demo uses map clicks first. A submit-only search may be added later with caching and identification; autocomplete is not part of the public-service design.

### Turkish charging data

EPDK states that all public charging stations are available through its public information surfaces and system-to-system web services:

- [EPDK web services](https://www.epdk.gov.tr/Detay/Icerik/3-0-226/web-servisler)
- [EPDK charging-market statistics](https://www.epdk.gov.tr/Detay/Icerik/3-0-222/sarj-hizmeti-piyasasi-istatistikler)
- [Şarj@TR information](https://www.epdk.gov.tr/Detay/Icerik/1-3428/serbest-erisim-platformu--sarj%40tr)

EPDK publishes monthly market statistics covering electric vehicles and charging infrastructure.
Those reports support the relevance of the problem, but they do not prove the schema, reuse rights,
or availability of a particular station API response.

## Chosen simplifications

| Topic | M0/MVP choice | Why |
|---|---|---|
| Road routing | OSRM provider | Reimplementing national road restrictions is not the portfolio decision being demonstrated. |
| Energy use | Constant kWh/100 km × safety factor | Transparent baseline that can be tested before adding grade/weather. |
| SOC | Discrete buckets | Small, deterministic, explainable state space. |
| Charging | Compatible CCS2 DC plus piecewise power with taper | Better than linear/full-charge-only while remaining auditable and explicit about first-release connector scope. |
| Objective | Driving + charging + setup minutes | Directly interpretable and aligned with single-trip planning. |
| Station status | Static/unknown | No unsupported live-data claim. |
| UI input | Map click first | Avoids public geocoder autocomplete restrictions and unnecessary keys. |

## Research-derived hypotheses

These are to be tested, not stated as results:

1. Joint route-and-charge optimization will produce feasible plans on cases where the
   shortest-driving-time road path is energy-infeasible.
2. Partial charging will reduce total time relative to a fixed “charge to 80%” policy on at least some multi-stop cases.
3. Conservative energy rounding will eliminate reserve violations at the cost of rejecting some narrowly feasible continuous solutions.
4. Candidate-station corridor pruning can bound OSRM matrix size without materially degrading the best plan on the benchmark set.

## Gaps deliberately left for later milestones

- vehicle-specific charging curves;
- speed-, grade-, traffic-, weather-, temperature-, and payload-dependent energy;
- uncertain queues and charger outages;
- evidence-backed fallback-station recommendations;
- real-time replanning;
- empirical field calibration;
- algorithmic speedups for continental-scale state spaces.
