# SAT Simulation API

HK telemetry loader and API for querying spacecraft housekeeping (HK) packets from MySQL.

## Overview

This project loads housekeeping data from the real DB layout used in the target system:

- Schema: `nstanl` (shared by O1A and O1B - they are **not** separate DB instances)
- Tables: `tbl_obs1a_hk1..hk6` for O1A, `tbl_obs1b_hk1..hk6` for O1B - table prefix only,
  selected by the `satellite_id` argument (see [Schema map](#schema-map))
- Time column: `timeUtc`
- Time unit: Unix epoch seconds (UTC)

Users can provide KST or UTC timestamps in friendly formats such as:

- `2026-08-20`
- `2026-08-20T15:00:00+09:00`
- `2026-08-20T00:00:00Z`
- `1787203236`

The loader converts those inputs into UTC epoch seconds for DB queries and normalizes the merged output to a standard `time` column in the DataFrame.

## Getting the code

```bash
git clone https://github.com/seoyoon-space/sat_footprint.git
cd sat_footprint
git checkout doeun-space
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Environment configuration

Copy `.env.example` to `.env` and update the values.

```bash
copy .env.example .env
```

Example `.env`:

```env
MYSQL_HOST=your_mysql_host_or_ip
MYSQL_PORT=3306
MYSQL_USER=your_mysql_user
MYSQL_PASSWORD=your_mysql_password
MYSQL_DB=nstanl
MYSQL_SCHEMA=nstanl
```

For the actual system, the DB name is usually `nstanl` and the HK tables live under that schema as `nstanl.tbl_obs1a_hk1`, `...hk2`, ...

### Per-satellite DB registry (optional)

If each satellite has its own DB instance (e.g. O1A, O1B), copy `config/satellites.example.toml` to `config/satellites.toml` and fill in the real per-satellite DB settings. `config/satellites.toml` must never be committed (it is git-ignored already).

```bash
copy config\satellites.example.toml config\satellites.toml
```

Optional direct URL:

```env
MYSQL_CONNECTION_URL=mysql+pymysql://nstanl:your_password@127.0.0.1:3306/nstanl
```

Important:

- `MYSQL_DB` must be the real database name, e.g. `nstanl`
- `O1A` or other satellite names are not DB names unless that is literally the database name
- `MYSQL_SCHEMA` is usually unnecessary for this MySQL layout

## Schema map

`core/loader/schema_map.py::get_hk_packet_schema(satellite_id)` picks the table prefix from
`satellite_id`:

- `satellite_id="O1A"` (or `None`, the default) -> `nstanl.tbl_obs1a_hk1..hk6`
- `satellite_id="O1B"` -> `nstanl.tbl_obs1b_hk1..hk6`

**`satellite_id` must be passed through to `HKLoader.load(..., satellite_id=...)` itself**, not
just to `HKLoader.for_satellite(...)`/the connection constructor - the latter only picks DB
*connection* info (currently identical for O1A/O1B, since they share one DB), while
`load()`'s own `satellite_id` argument is what selects the *table prefix*. Passing it to one but
not the other silently queries O1A's tables regardless of which satellite was asked for - this
was a real bug in this project earlier and is easy to reintroduce when copying the loader
elsewhere, so double-check both call sites agree.

The master packet is `hk1`. Column names (canonical, e.g. `qbody_wrt_eci1..4`) are identical
between O1A and O1B - only the table prefix differs.

Time semantics:

- DB field: `timeUtc`
- Unit: Unix epoch seconds (UTC)
- User-facing input: KST or UTC strings are accepted and internally normalized
- Output DataFrame: standard `time` column in UTC-aware pandas timestamps

Quaternion semantics: `qbody_wrt_eci1..4` is scalar-last (x,y,z,w) in the raw DB, but
`HKLoader._fetch_packet()` reorders it to this project's scalar-first (w,x,y,z) convention
before returning - so the DataFrame values are always scalar-first even though the column names
don't change.

## Python usage

### Direct DB connection via environment variables

```python
from core.loader import HKLoader

loader = HKLoader.from_env()
df = loader.load(
    start_time="2026-08-20",
    end_time="2026-08-21",
)

print(df.columns.tolist())
print(df.head())
```

### Direct SQLAlchemy URL

```python
from core.loader import HKLoader

loader = HKLoader(
    "mysql+pymysql://nstanl:your_password@127.0.0.1:3306/nstanl"
)

df = loader.load(
    start_time="2026-08-20T00:00:00+09:00",
    end_time="2026-08-20T23:59:59+09:00",
)

print(df.head())
```

### Satellite-specific loader

```python
from core.loader import HKLoader

loader = HKLoader.for_satellite("O1B")
df = loader.load(
    start_time="2026-08-20",
    end_time="2026-08-21",
    satellite_id="O1B",  # required here too - see the warning in "Schema map" above
)
```

### Embedding `core/` directly in another project (no HTTP, no `config.py` required)

`core/loader/hk_loader.py`, `schema_map.py`, and `time_sync.py` have no dependency on `api/` or
FastAPI, and `HKLoader(connection_url=...)` doesn't need `config.py` on the path at all - only
the `from_env()`/`for_satellite()` classmethods lazily import it, so a caller that already has
its own connection string (or its own settings module) can drop just those three files into
another codebase and use them standalone, e.g.:

```python
from core.loader import HKLoader

loader = HKLoader("mysql+pymysql://user:pass@host:3306/nstanl")
df = loader.load(start_time="2026-08-20", end_time="2026-08-21", satellite_id="O1B")
```

The rest of `core/` (`coordinates.py`, `geometry/footprint.py`, `math_utils/quat.py`,
`propagation.py`, `validator/ops_rules.py`) is equally self-contained (stdlib + pandas/numpy
only, no `api/` imports) if a consumer wants the coordinate/footprint/validator logic too
instead of just the loader. The same code is also available as the HTTP API below - both usage
modes read from the same source, so a fix in one mode is a fix in the other.

## CLI usage

### Basic HK extraction for a time window

```bash
python -m core.loader.hk_loader --start-time "2026-08-20" --end-time "2026-08-21" --max-rows 0
```

This writes a text file automatically, with a default name like:

```text
hk_20260820T000000_20260821T235959.txt
```

### Custom output filename

```bash
python -m core.loader.hk_loader \
  --start-time "2026-08-20" \
  --end-time "2026-08-21" \
  --max-rows 0 \
  --output "hk_20260820.txt"
```

### CSV export

```bash
python -m core.loader.hk_loader \
  --start-time "2026-08-20" \
  --end-time "2026-08-21" \
  --output "hk_20260820.csv" \
  --output-format csv \
  --max-rows 0
```

This writes a raw CSV export of the merged HK data with headers, without the human-readable text wrapper.

### Direct DB URL

```bash
python -m core.loader.hk_loader \
  --connection-url "mysql+pymysql://nstanl:your_password@127.0.0.1:3306/nstanl" \
  --start-time "2026-08-20" \
  --end-time "2026-08-21" \
  --output "hk_20260820.txt"
```

### Satellite config usage

```bash
python -m core.loader.hk_loader \
  --satellite-id "O1A" \
  --start-time "2026-08-20" \
  --end-time "2026-08-21"
```

## API usage

Start the API:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Health check (no auth required):

```bash
curl http://localhost:8000/health
```

### Interactive API docs

FastAPI auto-generates these from the code - no separate spec to maintain:

- `http://localhost:8000/docs` - Swagger UI (browse every endpoint's request/response schema, and call them directly with "Try it out")
- `http://localhost:8000/redoc` - ReDoc, read-only reference view
- `http://localhost:8000/openapi.json` - raw OpenAPI spec (for generating a client)

### Endpoint summary

| Endpoint | Purpose | Input driver |
|---|---|---|
| `POST /telemetry/query` | Raw merged HK telemetry records | `satellite_id` + time range (real DB) |
| `POST /telemetry/czml` | Satellite ground track + attitude for Cesium | `satellite_id` + time range (real DB) |
| `POST /footprint/rays` | Camera ray (ECEF origin + 5 unit directions), no terrain intersection - for a caller with its own DEM | `satellite_id` + time range (real DB) |
| `POST /footprint/track` | Footprint polygon per timestamp (GeoJSON), WGS-84 ellipsoid approximation | `satellite_id` + time range (real DB) |
| `POST /footprint/track/czml` | Same, as CZML scoped by `availability` so Cesium transitions it over time | `satellite_id` + time range (real DB) |
| `POST /footprint/compute` | One footprint polygon (GeoJSON) for a manually given position/attitude | manual position + quaternion |
| `POST /footprint/czml` | Same, as a single CZML packet | manual position + quaternion |
| `POST /footprint/line/track` | Left/right ground points of the push-broom sensor's current scan line, per HK sample | `satellite_id` + time range (real DB) |
| `POST /footprint/line/track/geojson` | Same, as a GeoJSON `LineString` per sample | `satellite_id` + time range (real DB) |
| `POST /footprint/line/track/czml` | Same, as a CZML `polyline` scoped by `availability` | `satellite_id` + time range (real DB) |
| `POST /propagation/track` | SGP4-predicted TEME(~=ECI) position/velocity per timestamp | TLE + time range (no DB) |
| `POST /propagation/track/czml` | Same, as a Cesium CZML position-only track | TLE + time range (no DB) |
| `POST /validator/ops-status` | Settling-time / wheel-saturation PASS/WARN/FAIL | `satellite_id` + time range (real DB) |
| `POST /mission/schedule` | Mission schedule rows + real camera ON/OFF window (`scanStart`/`camStart`/`camEnd`) | `satellite_id` + time range (separate MCE DB) |
| `GET /health` | Liveness check, no auth | - |

### Authentication

If `API_KEY` is set in `.env`, `/telemetry/*`, `/footprint/*`, and `/validator/*` require an
`X-API-Key` header matching that value. If `API_KEY` is unset (local development default), no
auth is enforced. `/health` never requires a key.

```bash
curl -H "X-API-Key: your_api_key" http://localhost:8000/telemetry/query ...
```

### CORS

If a frontend (e.g. a Cesium viewer) fetches this API directly from the browser, set
`CORS_ALLOWED_ORIGINS` in `.env` to a comma-separated list of allowed origins:

```env
CORS_ALLOWED_ORIGINS=https://dem.example.com,http://localhost:5173
```

If unset (default), no cross-origin browser request is allowed - server-to-server calls (e.g.
a DEM server proxying the request on the backend) are unaffected either way, since CORS is a
browser-enforced restriction, not a server-side one.

Query telemetry:

```bash
curl -X POST "http://localhost:8000/telemetry/query" \
  -H "Content-Type: application/json" \
  -d '{
    "satellite_id": "O1A",
    "start_time": "2026-08-20T00:00:00+09:00",
    "end_time": "2026-08-20T23:59:59+09:00",
    "merge_tolerance_sec": 1.0,
    "interpolate_gaps": true
  }'
```

Python example:

```python
import requests

payload = {
    "satellite_id": "O1A",
    "start_time": "2026-08-20T00:00:00+09:00",
    "end_time": "2026-08-20T23:59:59+09:00",
    "merge_tolerance_sec": 1.0,
    "interpolate_gaps": True,
}

resp = requests.post(
    "http://localhost:8000/telemetry/query",
    json=payload,
    timeout=60,
)

print(resp.status_code)
print(resp.json())
```

CZML ground track + attitude (loads directly into a `Cesium.CzmlDataSource`):

```bash
curl -X POST "http://localhost:8000/telemetry/czml?coordinate_frame=ecef" \
  -H "Content-Type: application/json" \
  -d '{
    "satellite_id": "O1A",
    "start_time": "2026-08-20T00:00:00Z",
    "end_time": "2026-08-20T01:00:00Z"
  }'
```

### Camera footprint (no DB, stateless)

```bash
curl -X POST "http://localhost:8000/footprint/compute" \
  -H "Content-Type: application/json" \
  -d '{
    "pos_eci_x": 7000000, "pos_eci_y": 0, "pos_eci_z": 0,
    "q_w": 1, "q_x": 0, "q_y": 0, "q_z": 0,
    "utc_datetime": "2026-08-20T00:00:00Z",
    "fov_x_deg": 10, "fov_y_deg": 10,
    "boresight_x": -1, "boresight_y": 0, "boresight_z": 0
  }'
```

Returns a GeoJSON `FeatureCollection` (footprint polygon + boresight center point).

Same request body works against `POST /footprint/czml` for a Cesium-loadable CZML packet
list instead (a `polygon` packet for the footprint + a `point` packet for the boresight
center) - load it alongside `/telemetry/czml`'s output to show ground track and camera
coverage in the same viewer.

Both `/footprint/compute` and `/footprint/czml` intersect the camera ray against a smooth
WGS-84 ellipsoid (no terrain). If the caller already has a precise terrain/DEM model and
just needs the ray itself, use `POST /footprint/rays` instead - given a satellite/time
range it loads real HK telemetry (position + attitude) and returns, per timestamp, the
ECEF ray origin and the boresight + 4 FOV-corner unit direction vectors, with no ellipsoid
or terrain intersection performed on this side:

```bash
curl -X POST "http://localhost:8000/footprint/rays" \
  -H "Content-Type: application/json" \
  -d '{
    "satellite_id": "O1A",
    "start_time": "2026-08-20T00:00:00Z",
    "end_time": "2026-08-20T00:10:00Z",
    "fov_x_deg": 10, "fov_y_deg": 10,
    "boresight_x": -1, "boresight_y": 0, "boresight_z": 0
  }'
```

For the common case of just wanting the ellipsoid-approximated footprint *polygon* itself, driven by
real telemetry over a time range (no manual per-point calls, no external terrain model needed), use
`POST /footprint/track` (GeoJSON, one Polygon+Point feature per timestamp with a `time` property) or
`POST /footprint/track/czml` (CZML, one polygon+point packet per timestamp scoped with `availability`
so Cesium shows the correct footprint as the timeline plays) - same request body as `/footprint/rays`:

```bash
curl -X POST "http://localhost:8000/footprint/track/czml" \
  -H "Content-Type: application/json" \
  -d '{
    "satellite_id": "O1A",
    "start_time": "2026-08-20T00:00:00Z",
    "end_time": "2026-08-20T00:10:00Z",
    "fov_x_deg": 10, "fov_y_deg": 10,
    "boresight_x": -1, "boresight_y": 0, "boresight_z": 0
  }'
```

### Push-broom line footprint (current scan line)

`/footprint/track` treats the camera as a frame sensor - a full FOV rectangle projected at
each instant. A real push-broom sensor instead scans one across-track *line* at a time as the
satellite moves, and the ground track is built up from many such lines. `POST
/footprint/line/track` (and its `/geojson` and `/czml` variants) model that: given
`fov_across_deg` (the sensor's across-track FOV - along-track width is treated as zero, same as
the DEM server's own `SensorConfig`, which likewise only carries a single FOV angle), it returns
the left/right ground points of the line currently being scanned, per HK telemetry sample:

```bash
curl -X POST "http://localhost:8000/footprint/line/track/czml" \
  -H "Content-Type: application/json" \
  -d '{
    "satellite_id": "O1A",
    "start_time": "2026-08-20T00:00:00Z",
    "end_time": "2026-08-20T00:10:00Z",
    "fov_across_deg": 1.6,
    "boresight_x": -1, "boresight_y": 0, "boresight_z": 0
  }'
```

This is the piece needed to draw "where the sensor plane's current line is" inside the
rectangular-pyramid FOV visualization (`cesium-viewer.js`'s `cornerDirsBody`/`_createFovFootprint`
on the DEM server side already renders that pyramid from the same corner-ray geometry as
`/footprint/rays` - this adds the line that sweeps inside it).

Sample spacing follows the raw HK telemetry cadence (~1 Hz), not the camera's real `line_rate`
(hundreds to thousands of Hz) - matching that would need attitude/position interpolation between
HK samples, which is a materially bigger feature this project doesn't implement; the DEM server's
own Orekit/Rugged pipeline is the source of truth for line-accurate push-broom simulation. This
endpoint is for showing *where* the active line roughly is, at telemetry resolution.

### Orbit propagation (TLE / SGP4, no DB)

Independent of real telemetry - given a TLE, propagates the orbit over a time range via the
`sgp4` package and returns TEME(~=ECI) position/velocity per timestamp. Useful for a predicted/
planned trajectory to compare against real HK position, or when no live telemetry is available yet:

```bash
curl -X POST "http://localhost:8000/propagation/track" \
  -H "Content-Type: application/json" \
  -d '{
    "tle_line1": "1 88888U          80275.98708465  .00073094  13844-3  66816-4 0    87",
    "tle_line2": "2 88888  72.8435 115.9689 0086731  52.6988 110.5714 16.05824518  1058",
    "start_time": "1980-10-01T23:41:24.113760Z",
    "end_time": "1980-10-01T23:51:24.113760Z",
    "step_sec": 300
  }'
```

(the TLE above is Vallado's canonical SGP4 verification case, satellite 88888 - the same one
`tests/test_propagation.py` checks against; swap in a real satellite's current TLE for actual use)

`POST /propagation/track/czml` returns the same track as a Cesium CZML `position` (no
`orientation` - SGP4 gives no attitude).

### Ops status (settling time / wheel saturation)

```bash
curl -X POST "http://localhost:8000/validator/ops-status" \
  -H "Content-Type: application/json" \
  -d '{
    "satellite_id": "O1A",
    "start_time": "2026-08-20T00:00:00Z",
    "end_time": "2026-08-20T01:00:00Z",
    "settling_tolerance_deg": 0.5,
    "settling_hold_duration_sec": 30,
    "wheel_max_rpm": 6000
  }'
```

`settling_tolerance_deg`/`wheel_max_rpm` are each optional; omit one to skip that evaluation.
Returns overall `PASS`/`WARN`/`FAIL` plus per-check detail (see `core/validator/ops_rules.py`).

### Mission schedule (real camera ON/OFF window, separate MCE DB)

Everything above reads the HK telemetry DB (`MYSQL_*`/`satellites.toml`). This endpoint reads a
**completely different database** - the MCE (mission scheduling) server's own DB, which holds
`TB_Selected_Mission_Schedule`: when a satellite was actually scheduled/commanded to shoot, and
(via `core/mission/mce_db.py::compute_camera_window`) the real camera ON~OFF window computed from
the mission's `MissionParameterJson` (`scanStart`/`camStart`/`camEnd`) - much narrower than the
schedule's `eventStart`~`eventEnd`, which is the whole pass/scheduling window, not the actual
shutter-open interval. Requires `MCE_DB_*` in `.env` (see `.env.example`); unrelated to
`MYSQL_*`/`satellites.toml`.

```bash
curl -X POST "http://localhost:8000/mission/schedule" \
  -H "Content-Type: application/json" \
  -d '{
    "satellite_id": "O1A",
    "start_time": "2026-04-01T00:00:00Z",
    "end_time": "2026-04-30T23:59:59Z"
  }'
```

Returns the mission rows for that satellite/window (`EventStart` in range), each including
`scanStart`/`camStart`/`camEnd` (`null` if the mission has no `MissionParameterJson`, e.g. older
or ground-station entries - see `core/mission/mce_db.py`).

## Notes / real DB validation

This project was aligned to the real observed DB naming convention:

- `timeUtc` is the real timestamp column name on the HK tables
- `timeUtcStr` is the human-readable datetime string column
- `time` is not used directly in the real tables
- epoch seconds are stored in UTC

If the live MySQL instance is available, the most direct validation is:

```sql
SHOW COLUMNS FROM nstanl.tbl_obs1a_hk1;
SELECT * FROM nstanl.tbl_obs1a_hk1 ORDER BY timeUtc DESC LIMIT 5;
```

Then validate the loader with:

```bash
python -m core.loader.hk_loader --start-time "2026-08-20" --end-time "2026-08-21" --max-rows 5
```

## Docker

Build and run the API in a container (reads `.env` for DB settings):

```bash
docker build -t sat-simulation-api .
docker run --rm -p 8000:8000 --env-file .env sat-simulation-api
```

For standing up a persistent instance on a shared test server (e.g. for another service like a
DEM/Cesium server to call), see [`deploy/README.md`](deploy/README.md) - it covers a
one-command Docker deploy script and a systemd-based alternative.

## Running tests

```bash
python -m pytest tests/ -q
```
