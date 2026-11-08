# Research Review

Review date: 2026-08-05. Literature additions, related projects, and the gap review were updated on
2026-11-07.

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
| Sweda, Dolinskaya, and Klabjan (2017), [Adaptive Routing and Recharging Policies](https://doi.org/10.1287/trsc.2016.0724) | Treats route viability as sensitive to charging-infrastructure availability and studies adaptive decisions. | Static-data plans must not imply live certainty. A fallback recommendation would require fresh reliability or availability evidence and is outside the static first release. |
| Schoenberg and Dressler (2023), [Reducing Waiting Times at Charging Stations](https://doi.org/10.1109/TIV.2022.3140894) ([preprint](https://arxiv.org/abs/2102.06503)) | Adds waiting-time information and adaptive rerouting to reduce total trip time. | Queue and occupancy are future risk terms, not invented inputs in the static MVP. |
| Montoya et al. (2017), [EVRP with Nonlinear Charging Function](https://doi.org/10.1016/j.trb.2017.02.004) | Demonstrates that linear full-charge assumptions miss the nonlinear shape of real charging and uses piecewise linear approximation. | Use partial charging and an explicit taper approximation; document that it is not vehicle-specific calibration. |
| Levin, Duell, and Waller (2014), [Effect of Road Grade on Energy Consumption](https://doi.org/10.3141/2427-03) | Shows that road grade can materially affect energy and eco-routing. | Constant consumption is a baseline; elevation belongs in a later calibrated model. |
| Baum, Sauer, Wagner, and Zündorf (2017), [Consumption Profiles in Route Planning for Electric Vehicles](https://doi.org/10.4230/LIPIcs.SEA.2017.19) | Shows that a consumption profile is at most linear in graph size and gives a polynomial-time energy-optimal search with partial recharging; a Contraction Hierarchies and A* variant computes long routes with charging stops in under 300 ms. | Continuous-SOC profiles and speedup techniques exist. At M6 scale, exact fastest took a median 21.3 s for a 72-trip pass (about 0.3 s per trip), so ChargePath keeps the discrete grid and measures its gap (E2). |
| Strehler, Merting, and Schwan (2017), [Energy-Efficient Shortest Routes for Electric and Hybrid Vehicles](https://ideas.repec.org/a/eee/transb/v103y2017icp111-135.html) | Models EV routing as a constrained shortest path with convertible resources and charging stations; energy recovery can make optimal routes walks with cycles. | A signed (recuperating) consumption model must cap stored energy at full and keep time non-negative so label-setting search stays valid. |
| Fiori, Ahn, and Rakha (2016), [Power-Based EV Energy Consumption Model](https://doi.org/10.1016/j.apenergy.2016.01.097) | Computes instantaneous EV energy from speed, acceleration, and grade, with regenerative-braking efficiency that depends on the operating state. | A physics-based leg model needs speed and grade inputs the planner does not have from OSRM alone; ChargePath keeps such models opt-in and experimental. |
| Guillet, Hiermann, Kröller, and Schiffer (2022), [EV Charging Station Search in Stochastic Environments](https://doi.org/10.1287/trsc.2021.1102) | Formulates the search for a free charger as a finite-horizon Markov decision process with availability probabilities. | Availability probabilities are inputs ChargePath does not have; it reports only structural alternatives, without reliability meaning. |
| Yuksel and Michalek (2015), [Effects of Regional Temperature on EV Efficiency](https://doi.org/10.1021/es505621s) | Links ambient temperature to BEV energy use and range across US regions. | Temperature is a material, uncalibrated effect; the safety factor is not a substitute for it. |
| Dastpak, Errico, Jabali, and Malucelli (2024), [Dynamic Routing for the EVSPP with Charging Station Occupancy Information](https://doi.org/10.1016/j.trc.2023.104411) | Treats charging-station occupancy as dynamic information for routing a single EV. | Confirms that occupancy belongs to the same single-vehicle problem class. Without occupancy data, ChargePath states that every snapshot station is assumed usable. |
| Weng, Hu, Fan, Zhang, and Yun (2025), [A Reliability Model for EVRP under Charging Failure Risk](https://doi.org/10.1080/23302674.2025.2541923) | As its title states, a reliability model for EV routing under charging-failure risk. The abstract was not accessible for this review. | Charging-failure risk is an active research topic. A static snapshot gives no failure probabilities, so ChargePath does not model it. |
| Gutiérrez-Moreno et al. (2026), [EV Route Optimization: An End-to-End Learning Approach](https://doi.org/10.3390/wevj17010041) | Predicts SOC use with an LSTM from speed, temperature, inclination, and distance on one Nissan Leaf, then uses an enhanced A* search for energy-minimal routes. | Learned consumption needs logged trips for each vehicle. ChargePath's public-data pilots point the same way, but they are too small to change the planner's model. |

## Related projects

Reviewed on 2026-11-07. Each entry records only what the project's own repository, paper, store page,
or a dated news report states. "Not stated" means the reviewed page does not say; it is not a claim
that the feature is missing.

### Open-source and research planners

| Project | Stated method | Stated data | Stated evaluation |
|---|---|---|---|
| [evnav](https://github.com/giraldeau/evnav) (MIT per README; C++ with Qt) | Dijkstra over a complete digraph of chargers, origin, and destination built with libosrm. Edge cost is drive time plus charging time plus a stop overhead. Constant Wh/km and an average fast-charging power up to a maximum SOC. The README says it ignores elevation, weather, and battery wear. | User-supplied charger JSON (example: Circuit Électrique, Québec) | Not stated; the README says the prototype will not scale to many chargers. |
| [TheMarex/charge](https://github.com/TheMarex/charge) (BSD-2-Clause; C++17) | Experimental OSM-based planner for time-optimal routes, with consumption based on driving speed and slope (SRTM elevation) and a "realistic charging model" whose form the README does not describe. | GeoJSON chargers with per-station power; a script downloads Tesla station data | Not stated. |
| [EVRoute](https://doi.org/10.3390/automation7050159) (Cruz et al., Automation, 2026) | A*-based state-space search over driving and charging time with SOC constraints. | Open Charge Map stations, Valhalla roads, Traccar GPS | Exploratory user study; scenarios in Northeastern Brazil, the longest 685 km with three charging stops. |
| [Pumperly](https://github.com/GeiserX/pumperly) (AGPL-3.0-or-later) | Fuel-price and charger map whose planner chooses the cheapest fuel stops. No EV energy model is described. | Official registries for Spain and Germany, Open Charge Map elsewhere; optional Valhalla routing | Not stated. |

### Commercial and Turkish planners

These products publish features, not methods or evaluations, so they are listed for context only.

| Product | Stated planning inputs |
|---|---|
| [ABRP](https://abetterrouteplanner.com/home) | More than 1,000 vehicle models, charge behavior, temperature, wind, elevation, accessories, live vehicle data, and in its premium tier traffic and live charger availability. |
| [Chargetrip API](https://chargetrip.com/api/) | Per-vehicle consumption models, live vehicle data such as SOC, and preferred charging operators. |
| Togg Trumore, smart route ([DonanımHaber, 2024-06-14](https://www.donanimhaber.com/togg-trumore-uygulamasina-elektrikli-araclar-icin-akilli-rota-oze--178447)) | Brand and model, current and desired arrival battery level, battery capacity, and charging speed. It reports charging minutes at each station. |
| Eşarj, trip planning ([DHA, 2025-02-10](https://www.dha.com.tr/ekonomi/esarj-uygulamasina-seyahat-planlama-ozelligini-ekledi-2580648)) | Brand and model, range, and start and arrival charge, with toll-road and ferry filters. |
| [ChargeIQ](https://apps.apple.com/app/id1609390820) | Vehicle and current battery level, with stops planned across many Turkish networks and charging time and cost estimated per vehicle. |

### What this comparison supports

ChargePath solves the same single-trip problem with less vehicle realism than the commercial products,
which state per-vehicle consumption, weather, elevation, and live data. None of the reviewed public
pages describes independent replay of every returned plan, a frozen real-route benchmark, or
checksum-pinned evidence. These are what ChargePath contributes. No comparison of plan quality against
these products was run, and none is claimed.

## Official platform and data findings

### Road network

[OSRM](https://project-osrm.org/) is an open-source routing engine over OpenStreetMap data. It provides route, nearest, table, match, and trip services. Its route and table APIs match the project's boundary: obtain graph/profile-dependent road legs and pairwise travel costs, then solve the EV-specific state problem in Python.

The public OSRM demo is useful for manual development but is not a project-owned service. A
user-configured local or self-hosted instance with a pinned backend release, routing profile, and
regional OpenStreetMap extract is the reproducible integration target. The OSRM setup notes may choose a
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

| Topic | Initial choice | Why |
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

## Gap review at closure

The 2026-08-05 review left seven gaps for later work. At closure on 2026-11-07 they stand as
follows.

| Gap | Status | Reason |
|---|---|---|
| Vehicle-specific charging curves | Future work; closure Phase B stopped at its data gate | [open-ev-data](https://github.com/open-ev-data/open-ev-data-dataset) is CDLA-Permissive-2.0 and its schema defines `charging.dc_charge_curve`. In release v1.24.0, however, none of the 1,189 vehicles has a curve, and only 22 carry isolated DC charging-time measurements. Community forks with curves derive them from Fastned charts, which the plan excludes. No redistributable, measured curve source was found. |
| Speed-, grade-, weather-, temperature-, and payload-dependent energy | Out of scope | The public telemetry pilots gave mixed results. A speed model improved held-out ID.3 error, while the BMW terrain model helped on three hillier sections but raised whole-trip error. The evidence does not justify changing the planner's model. |
| Uncertain queues and charger outages | Out of scope | The static snapshot has no occupancy or failure data. The literature above needs both as inputs. |
| Evidence-backed fallback stations | Out of scope | Requires reliability data. The real-route stress case that removes the first charging site only measures sensitivity. |
| Real-time replanning | Out of scope | Requires a live vehicle and live station data, outside the product boundary. |
| Empirical field calibration | Partly addressed | The field pilots evaluate energy and charging models on public data from a few vehicles; they are not ground truth for the real-route plans. |
| Speedups for continental-scale state spaces | Not needed | About 0.3 s per trip at real-route benchmark scale. Profile and Contraction Hierarchies methods remain available if the scale changes. |
