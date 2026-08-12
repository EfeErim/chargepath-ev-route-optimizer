# Data Strategy

## Principles

- Prefer official or clearly licensed sources.
- Separate raw acquisition, normalization, and bundled samples.
- Preserve source URL, retrieval timestamp, checksum, and transformation version.
- Never label cached or static data as live.
- Never commit large road extracts, OSRM artifacts, credentials, or unrestricted third-party datasets.

## Road data

### Source

- OpenStreetMap road data.
- OSRM for provider-computed road route, duration, distance, and geometry.

### Repository policy

`.osm.pbf` and `.osrm*` files are ignored. A future setup script may download an extract directly from a documented provider and build local OSRM artifacts outside Git.

Recorded OSRM fixtures must retain the API version, routing profile, request options, retrieval time,
response checksum, and `data_version` when supplied. Table distances are the distances of OSRM's
fastest routes, not a claim that they are globally shortest-distance routes.

The M1 fixture under `tests/fixtures/osrm/v26.7.3/` was recorded from exact backend release `v26.7.3`
against a project-authored synthetic three-node OSM map. Its manifest pins the backend asset and
commit, `car.lua` checksum, CH algorithm, HTTP `v1` requests, supplied data version, retrieval time,
and every fixture-file checksum. It contains no real OpenStreetMap extract.

### Attribution

The M4 UI displays visible OpenStreetMap attribution, uses one configurable HTTPS Leaflet tile
template, and contains no prefetch or bulk-download path. Tile failure leaves route geometry and trip
explanation available. Public tile usage must follow the [OSM tile policy](https://operations.osmfoundation.org/policies/tiles/).

## Charging-station data

### Preferred source

EPDK's public charging-station services and Şarj@TR information surfaces:

- https://www.epdk.gov.tr/Detay/Icerik/3-0-226/web-servisler
- https://www.epdk.gov.tr/Detay/Icerik/1-3428/serbest-erisim-platformu--sarj%40tr

### M2 access result

The 2026-08-09 spike verified the official request and observed schema against one checksum-pinned
response. Its [metadata-only manifest](evidence/epdk_snapshot_manifest_2026-08-09.json) and
[access report](epdk_access_spike.md) record 16,539 source rows and 46,955 sockets. The normalizer and
candidate selector are implemented and offline-tested.

The following questions remain mandatory before a real snapshot is committed or distributed:

1. quota and retry expectations;
2. data-update and freshness semantics;
3. reuse and redistribution conditions; and
4. stable identifier deletion/update behavior across multiple snapshots.

The current public Swagger does not declare a response model. A small, project-authored synthetic
fixture mirrors the observed field/container shape and tests schema drift explicitly. The successful
row count is not, by itself, evidence of continuing access, quality, freshness, or reuse rights.

If these questions are unresolved, keep real raw data outside Git and publish only code plus a synthetic fixture.

### Normalized station grain

One station row:

- station identifier;
- name and operator;
- latitude and longitude;
- public/access classification;
- source timestamp and source identifier.

One connector row:

- station identifier;
- connector type;
- AC/DC classification;
- maximum power in kW;
- socket count if supplied;
- status/freshness only when supported by source evidence.

The first release projects only public compatible CCS2 DC sockets into optimizer station options. Raw
availability, reservation, price, and status fields are not planner inputs. Multiple sockets at one
site remain separate normalized records before the deterministic compatibility projection.

## Vehicle data

The first release avoids a large model catalogue. Users or fixtures supply:

- usable battery kWh;
- initial SOC;
- reserve SOC;
- kWh/100 km baseline;
- maximum DC charging power.

The first release also requires `supported_dc_connectors`, fixed to `CCS2` in bundled examples.

Any future preset must include its source and must distinguish nominal battery capacity from usable capacity.

## Bundled sample

`data/sample/synthetic_corridor.json` is synthetic and schema-versioned. Coordinates and travel
values are not real station claims. The production fixture loader is the single source for the CLI
demo and fixture tests; examples must not duplicate its values in code. Schema version 2 carries a
validated GeoJSON LineString for every road leg so the primary future UI can draw the synthetic route
without inventing geometry or contacting OSRM.

The same source file is installed by the wheel under `share/chargepath/synthetic_corridor.json`.
Source/editable execution prefers the repository path; an installed wheel resolves the copied build
artifact under its environment prefix. The canonical wheel check compares the packaged bytes,
installs the wheel non-editably, and proves the default fixture flow does not leak the repository path.

`tests/fixtures/epdk/observed_v1/synthetic_response.json` is also entirely project-authored and
synthetic. It records only the observed schema shape and deliberate invalid/duplicate cases; it does
not contain copied station rows.

## Freshness contract

Every real station snapshot should have a manifest similar to:

```json
{
  "manifest_version": 1,
  "snapshot_id": "epdk-<UTC date>-<short checksum>",
  "source": "EPDK",
  "source_url": "...",
  "request_fingerprint_sha256": "...",
  "retrieved_at": "<UTC ISO-8601 timestamp>",
  "response_sha256": "...",
  "source_record_count": 0,
  "source_schema_version": "observed-1",
  "transformation_version": "normalize-1",
  "reuse_status": "pending_verification",
  "reconciliation": {
    "input": 0,
    "accepted": 0,
    "rejected": 0,
    "duplicates": 0
  }
}
```

Unknown freshness is a first-class value, not silently replaced with the current time.

Every normalization run must additionally reconcile input, accepted, rejected, and duplicate counts
and write reasons for rejected coordinates, identifiers, connectors, and socket power values.

Candidate selection must record corridor width, candidate cap, ordered ranking keys, and the final
stable-identifier tie-break in configuration or the snapshot-derived manifest. Repeating selection
with the same route and snapshot must produce the same ordered station identifiers. The implementation
may precompute route-segment metrics and conservative corridor bounds, but those optimizations must not
change the recorded selection algorithm or its ordered output.

The current default must produce a prefix-monotonic route-progress coverage order: raising the cap
must preserve all smaller-cap candidates. A small declared progress-gap tolerance may let corridor
quality break near-ties. A launcher-specific candidate cap controls OSRM matrix size and consequently
the distinct station nodes available to the optimizer; it must not be presented as a separate hard-
coded charging-stop rule.
