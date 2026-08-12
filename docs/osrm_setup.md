# Local OSRM setup

ChargePath defaults to `http://127.0.0.1:5000`. It never selects the public OSRM demo endpoint by
default. A non-loopback endpoint must be configured explicitly with `allow_remote_endpoint=True` and
must not be treated as a production dependency.

## Pinned backend and protocol

The M1 adapter was fixture-tested with OSRM backend `v26.7.3` at commit
`0844e3af77896d11998ef6db356a553056652c8e`. The URL path still uses `/v1/`: `v1` is the OSRM 5.x
HTTP protocol version, not the backend release number.

The recorded fixture manifest is at `tests/fixtures/osrm/v26.7.3/manifest.json`. It identifies the
backend asset, car-profile checksum, CH preparation algorithm, synthetic input map, request options,
data version, retrieval time, and response checksums. The small OSM XML input is project-authored
synthetic test data, not a real road extract.

## Self-hosted Docker example

Use a legally obtained OpenStreetMap extract and keep the `.osm.pbf` plus generated `.osrm*` files
outside Git. The commands below pin the tested backend independently from the HTTP protocol path.

```powershell
$OsrmImage = "ghcr.io/project-osrm/osrm-backend:v26.7.3"
$OsrmData = "C:\path\to\osrm-data"
$Extract = "region-latest.osm.pbf"

docker run --rm -t -v "${OsrmData}:/data" $OsrmImage `
  osrm-extract -p /opt/car.lua "/data/$Extract"
docker run --rm -t -v "${OsrmData}:/data" $OsrmImage `
  osrm-partition "/data/region-latest.osrm"
docker run --rm -t -v "${OsrmData}:/data" $OsrmImage `
  osrm-customize "/data/region-latest.osrm"
docker run --rm -t -p 127.0.0.1:5000:5000 -v "${OsrmData}:/data" $OsrmImage `
  osrm-routed --algorithm mld --ip 0.0.0.0 --port 5000 "/data/region-latest.osrm"
```

The host-side bind is loopback-only. Check the instance with an explicit `longitude,latitude`
request:

```powershell
curl.exe "http://127.0.0.1:5000/route/v1/driving/29.0,41.0;29.1,41.1?overview=false"
```

The profile and processed OSM snapshot determine travel times and routes. OSRM output is not live
traffic, and a self-hosted instance has only the freshness and operational guarantees supplied by
its operator.

## Adapter configuration

```python
from chargepath.providers import OsrmHttpClient

client = OsrmHttpClient(
    endpoint="http://127.0.0.1:5000",
    profile="driving",
    timeout_seconds=10,
)
```

For a deliberately selected remote endpoint, pass its full base URL and
`allow_remote_endpoint=True`. Unit tests inject an offline transport and never need OSRM or internet
access.
