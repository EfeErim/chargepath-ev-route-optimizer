# EPDK access, schema, and reuse spike

Verified: 2026-08-09

## Outcome

The official EPDK charging-station service was reproducibly reachable. A `GET` request with
`Content-Type: application/json`, `Accept: application/json`, and the required body `{}` returned
HTTP 200. The response was captured once under ignored `data/raw/`, inspected offline, and was not
added to the repository.

The metadata-only [snapshot manifest](evidence/epdk_snapshot_manifest_2026-08-09.json) records the
request fingerprint, UTC retrieval time, response checksum and byte length, source row/socket counts,
schema version, validation reconciliation, reuse decision, and default candidate-selection contract.

## Reproducible offline inspection

After separately retrieving the response into the ignored raw-data directory:

```powershell
python scripts/inspect_epdk_snapshot.py `
  data/raw/epdk/2026-08-09/response.json `
  --retrieved-at 2026-08-09T14:13:33Z `
  --response-status 200 `
  --manifest-out data/processed/epdk/2026-08-09/manifest.json `
  --report-out data/processed/epdk/2026-08-09/validation_report.json
```

The command performs no network call. For snapshot `epdk-20260809-2850e5e20827`, it reconciled
16,539 input sites to 16,539 accepted, zero rejected, and zero duplicates; 46,955 input sockets
reconciled to 46,955 accepted, zero rejected, and zero duplicates. It projected 7,640 public sites
with at least one compatible `DC_CCS` socket after the public-access and CCS2 DC filters. These counts
describe only the checksum-pinned response and are not a continuing EPDK availability or quality
claim.

## Observed schema boundary

The public Swagger declares a required GET body but no 200 response definition. The 2026-08-09
response used a JSON envelope containing status fields, `columnNames`, `numRows`, and `data`.
Each data row exposed a station identifier, name, operator, access classification, coordinates, and a
`soketler` list. Each socket exposed:

- `soketNo`: source socket identifier;
- `soketTipi`: `AC` or `DC`;
- `soketTuru`: observed `AC_TYPE2`, `DC_CCS`, or `DC_CHADEMO` connector code; and
- `soketGucu`: positive power in kW encoded as a string.

`tests/fixtures/epdk/observed_v1/synthetic_response.json` mirrors this shape with invented data. The
normalizer fails closed on unreviewed fields and tests invalid coordinates, invalid socket power,
duplicate site/socket identifiers, reconciliation, provenance retention, public CCS2 DC projection,
and schema drift without internet access.

## Access and reuse decisions

| Question | 2026-08-09 result | Project decision |
|---|---|---|
| Official access | HTTP 200 from the endpoint listed by EPDK | Acquisition remains an explicit operational step, never a unit-test dependency. |
| Request/filter behavior | `{}` returned one 16,539-row response; Swagger advertises filter fields | Pagination is not declared. Filters were not exhaustively probed to avoid unnecessary requests. |
| Response schema | Observed and checksum-pinned; Swagger has no response model | Exact envelope and row/socket fields are fixture-tested and unreviewed drift fails closed. |
| Quota/retry | No official quota or retry contract was found | No automatic retries or bulk polling are implemented. `quota_status` remains unknown. |
| Freshness/update cadence | No source timestamp was present in the response | Freshness remains `unknown_not_supplied_by_response`; retrieval time is not substituted for source freshness. |
| Reuse/redistribution | Public access was confirmed, but an explicit redistribution licence was not found | `reuse_status` remains `pending_verification`; no real raw or normalized row is committed. |
| Stable identifiers | Station and socket identifier fields were observed in one snapshot | They are retained verbatim, but cross-snapshot update/deletion stability remains unproven. |
| Status, price, availability | Not present in the observed station/socket rows | Excluded from normalization and planning. Station data is presented only as static. |

## Candidate-selection contract

The default corridor is 25 km wide and capped at 50 candidates. Eligible public CCS2 DC options use a
prefix-monotonic farthest-progress ordering: every next option closes the largest uncovered route-
progress gap, and candidates whose gap differs by at most 0.02 are compared by corridor distance,
maximum compatible power, progress, and stable planner identifier. This prevents endpoint clustering
without allowing a trivial progress difference to select a large road detour. V3, its tolerance,
coverage policy, and ranking fields are serialized by `CandidateSelectionConfig.to_manifest()` and
tested against reversed input and increasing caps. The cap bounds OSRM Table work and thus available
distinct station nodes; the optimizer has no separate fixed stop count.

This selector narrows station candidates geometrically; OSRM still owns road-network reachability and
the final directed road-leg costs.
