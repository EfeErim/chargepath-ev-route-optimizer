# Source Registry

Verified: 2026-08-09

This registry records what each external source supports. A link does not grant dataset
redistribution rights and is not treated as evidence of live availability.

| Source | Type | Project use | Verification and limits |
|---|---|---|---|
| [OSRM HTTP API v26.6.1](https://project-osrm.org/docs/v26.6.1/http) | Official documentation | Route/Table request, units, GeoJSON geometry, error, and `data_version` contracts | Verified 2026-08-05. Coordinates are `longitude,latitude`; the HTTP protocol path remains `v1`; Table values may be `null`; distances follow fastest routes. |
| [OSRM backend releases](https://github.com/Project-OSRM/osrm-backend/releases/latest) | Official release record | Separates the backend release from the HTTP protocol version | Verified 2026-08-09. Latest observed release was v26.7.3; the M1 fixtures pin that exact backend and do not treat HTTP `v1` as a server release number. |
| [OpenStreetMap tile policy](https://operations.osmfoundation.org/policies/tiles/) | Official policy | M4 attribution, identification, caching, configurable provider, and no-prefetch requirements | Verified 2026-08-05. Best-effort service with no SLA; policy may change. |
| [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/) | Official policy | Optional submit-only geocoding boundary | Verified 2026-08-05. Public client-side autocomplete is forbidden; identification, caching, attribution, and rate limits apply. |
| [EPDK web-service index](https://www.epdk.gov.tr/Detay/Icerik/3-0-226/web-servisler) | Official service index | Identifies the public charging-station endpoint | Verified 2026-08-09. The official index still lists the endpoint; existence does not establish quota, freshness, or reuse rights. |
| [EPDK charging-station Swagger](https://apigateway.epdk.gov.tr/sarjIstasyonlari?swagger) | Official machine-readable service description | M2 request/schema spike | Verified 2026-08-09 with HTTP 200. It declares a required GET body and filter example but no 200 response definition. |
| [EPDK Şarj@TR](https://www.epdk.gov.tr/Detay/Icerik/1-3428/serbest-erisim-platformu--sarj%40tr) | Official information surface | Confirms public station/socket information exists | Verified 2026-08-09. App-visible status, price, or availability is not assumed reusable or fresh in this planner. |
| [EPDK charging-market statistics](https://www.epdk.gov.tr/Detay/Icerik/3-0-222/sarj-hizmeti-piyasasi-istatistikler) | Official report index | Problem relevance only | Verified 2026-08-05. Not used as station-level input. |
| [Baum et al. (2019)](https://doi.org/10.1287/trsc.2018.0889) | Primary research | Charging-stop shortest-path framing and engineering trade-offs | Design reference; no implementation code or dataset copied. |
| [Merting, Schwan, and Strehler (2015)](https://drops.dagstuhl.de/entities/document/10.4230/OASIcs.ATMOS.2015.29) | Primary research | Resource-recovering-node formulation | Design reference; no implementation code or dataset copied. |
| [Artmeier et al. (2010)](https://www.isp.uni-luebeck.de/research/publications/shortest-path-problem-revisited-optimal-routing-electric-vehicles) | Primary research | Battery-constrained shortest-path framing | Design reference; no implementation code or dataset copied. |
| [Montoya et al. (2017)](https://doi.org/10.1016/j.trb.2017.02.004) | Primary research | Motivation for nonlinear/piecewise charging | The current curve remains a documented generic approximation. |
| [Sweda and Klabjan (2017)](https://doi.org/10.1287/trsc.2016.0724) | Primary research | Motivation for treating charger availability as uncertain | Static first-release data cannot support a guaranteed fallback recommendation. |
| [Schoenberg and Dressler (2021)](https://arxiv.org/abs/2102.06503) | Primary research | Waiting-time and adaptive-rerouting context | Queue and occupancy are future evidence-dependent inputs, not synthetic planner facts. |
| [Levin, Duell, and Waller (2014)](https://doi.org/10.3141/2427-03) | Primary research | Road-grade energy sensitivity | Supports documenting constant consumption as an uncalibrated baseline. |

## M2 live probe note

On 2026-08-09, a single read-only `{}` request returned HTTP 200 and a 15,444,078-byte response with
16,539 station rows. The raw response remains under ignored `data/raw/`; only its metadata/checksum and
a project-authored synthetic schema fixture are in the repository. See the
[access spike](epdk_access_spike.md). Quota, source freshness, cross-snapshot identifier behavior, and
redistribution permission remain explicitly unknown.
