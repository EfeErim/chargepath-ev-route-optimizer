# Local OSRM setup

ChargePath defaults to `http://127.0.0.1:5000`. It never selects the public OSRM demo endpoint by
default. A non-loopback endpoint must be configured explicitly with `allow_remote_endpoint=True` and
must not be treated as a production dependency.

## Pinned backend and protocol

The OSRM adapter was fixture-tested with OSRM backend `v26.7.3` at commit
`0844e3af77896d11998ef6db356a553056652c8e`. The URL path still uses `/v1/`: `v1` is the OSRM 5.x
HTTP protocol version, not the backend release number.

The recorded fixture manifest is at `tests/fixtures/osrm/v26.7.3/manifest.json`. It identifies the
backend asset, car-profile checksum, CH preparation algorithm, synthetic input map, request options,
data version, retrieval time, and response checksums. The small OSM XML input is project-authored
synthetic test data, not a real road extract.

## Native Windows setup

This path needs no container runtime. The paths are examples; everything stays outside Git.

The release asset contains the executables but no Lua profile, so `profiles/` comes from the source
archive of the pinned commit. The fixture manifest records the SHA-256 of `car.lua` with CRLF line
endings; the GitHub copy uses LF, so convert it before comparing.

```powershell
$Commit = "0844e3af77896d11998ef6db356a553056652c8e"
$Asset = "node_osrm-v26.7.3-8-win32-x64-Release.tar.gz"
New-Item -ItemType Directory -Force D:\osrm-data\bin, D:\osrm-data\profiles, D:\osrm-data\extract

curl.exe -fsSL -o D:\osrm-data\$Asset "https://github.com/Project-OSRM/osrm-backend/releases/download/v26.7.3/$Asset"
(Get-FileHash D:\osrm-data\$Asset -Algorithm SHA256).Hash  # 76A686B4...0705A
tar.exe -xzf D:\osrm-data\$Asset -C D:\osrm-data\bin

curl.exe -fsSL -o D:\osrm-data\src.tar.gz "https://github.com/Project-OSRM/osrm-backend/archive/$Commit.tar.gz"
tar.exe -xzf D:\osrm-data\src.tar.gz -C D:\osrm-data\profiles --strip-components=2 "osrm-backend-$Commit/profiles"

curl.exe -fsSL -o D:\osrm-data\extract\turkey-latest.osm.pbf https://download.geofabrik.de/europe/turkey-latest.osm.pbf

$Bin = "D:\osrm-data\bin\binding_napi_v8"
& $Bin\osrm-extract.exe -p D:\osrm-data\profiles\car.lua -d <data-version> -t 8 D:\osrm-data\extract\turkey-latest.osm.pbf
& $Bin\osrm-contract.exe -t 8 D:\osrm-data\extract\turkey-latest.osrm
& $Bin\osrm-routed.exe --algorithm CH --ip 127.0.0.1 --port 5000 --max-table-size 100 D:\osrm-data\extract\turkey-latest.osrm
```

Pass `--ip 127.0.0.1` explicitly: `osrm-routed` binds `0.0.0.0` by default.

Measured on the 652,637,317-byte Turkey extract, with 8 threads, below-normal priority, and other
desktop applications open:

| Step | Elapsed | Peak private memory | Peak working set | Lowest free system RAM |
|---|---|---|---|---|
| `osrm-extract` | 10.6 min | 11.56 GB | 7.34 GB | 0.08 GB |
| `osrm-contract` | 31.3 min | 3.33 GB | 2.63 GB | 3.07 GB |
| `osrm-routed` (serving) | not applicable | 2.57 GB | 2.56 GB | 3.18 GB |

Memory values are periodic samples (5 s for extract, 15 s for contract, 10 s for routed), because OSRM
reports `RAM: peak bytes used: <not implemented on Windows>`; true peaks can be somewhat higher. Page-file
use peaked at a sampled 3.44 GB during extract. `osrm-extract` left almost no free RAM on this 15.8 GB
machine, so close other applications first. Generated `.osrm*` files take 3.74 GB; with the extract, the
total is 4.39 GB.

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

`osrm-routed` rejects Table requests above 100 locations unless it is started with a higher
`--max-table-size`. ChargePath's automatic candidate widening therefore stops at 98 stations by
default, because each request also carries the origin and destination. To search further, raise both
limits together, for example `osrm-routed --max-table-size 202 ...` with
`python -m chargepath.demo --adaptive-candidate-cap-limit 200 ...`. If a widened request is still
refused with `TooBig`, ChargePath reports the infeasible result from the last search it completed.

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
