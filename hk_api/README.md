# SAT Simulation API

HK telemetry loader and API for querying spacecraft housekeeping (HK) packets from MySQL.

## Overview

Loads housekeeping data from the real DB layout:

- Schema `nstanl`, shared by O1A/O1B (**not** separate DB instances) - tables
  `tbl_obs1a_hk1..hk6`/`tbl_obs1b_hk1..hk6`, prefix picked by `satellite_id` (see [Schema map](#schema-map))
- Time column `timeUtc`, Unix epoch seconds (UTC)
- Position/velocity: raw DB is km(/s); converted to m(/s) at load time

Accepts KST or UTC timestamps in several formats (`2026-08-20`, `2026-08-20T15:00:00+09:00`,
`2026-08-20T00:00:00Z`, epoch seconds) and normalizes everything to UTC internally.

## Two calling modes

Pick one per caller/endpoint, depending on whether `core/coordinates.py`'s ECI-to-ECEF
composition should run on the data:

- **Standalone** - trust this project's coordinate math end-to-end (ECI-to-ECEF for
  position+attitude, Cesium-ready `FIXED`-frame CZML). `POST /telemetry/czml`, or embed
  `core/coordinates.py::build_cesium_track_czml()`. **This is DEM's current CZML setup.**
- **Embedded-in-a-Cesium-host** - caller runs its own coordinate pipeline (Cesium/Orekit
  conventions, e.g. a Java/Orekit/Rugged terrain-footprint pipeline). Get ECI data as-is via
  `POST /telemetry/mission-hk`, or embed `HKLoader.load(..., invert_quaternion_direction=False)`
  without routing through `core/coordinates.py` (double-transform otherwise).

Both read the same `HKLoader` output - see "Quaternion semantics" and "DEM server export format"
below for the field-level contract.

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

`core/loader/schema_map.py::get_hk_packet_schema(satellite_id)` picks the table prefix:

- `satellite_id="O1A"` (or `None`, default) -> `nstanl.tbl_obs1a_hk1..hk6`
- `satellite_id="O1B"` -> `nstanl.tbl_obs1b_hk1..hk6`

**Pass `satellite_id` to `HKLoader.load(..., satellite_id=...)` itself**, not just to
`HKLoader.for_satellite(...)` - the latter only picks DB *connection* info (identical for
O1A/O1B today), while `load()`'s own argument selects the *table prefix*. Missing one silently
queries O1A regardless of what was asked - a real bug here before, easy to reintroduce elsewhere.

Master packet is `hk1`; canonical column names (e.g. `qbody_wrt_eci1..4`) are identical between
O1A/O1B, only the table prefix differs. Time: DB field `timeUtc`, Unix epoch seconds (UTC);
output DataFrame has a standard `time` column (UTC-aware pandas timestamps).

**Quaternion semantics:** raw DB `qbody_wrt_eci1..4` is scalar-last (x,y,z,w), ECI-to-Body. This
project expects Body-to-ECI, scalar-first (`quaternion_body2eci`), so `HKLoader._fetch_packet()`
reorders (x,y,z,w -> w,x,y,z) then inverts (conjugate) at the loading boundary. Only
`qbody_wrt_eci1..4` is touched - `q_ecef_wrt_eci1..4`/`cmd_q_body_wrt_eci1..4` are unconfirmed/unused.

**The direction flip is this project's own convention, not universal** - DEM's Orekit/Rugged
pipeline needs the opposite direction (a Rotation-convention difference, confirmed via real
target-coordinate testing, not a bug). `HKLoader.load(..., invert_quaternion_direction=False)`
skips just the flip (reorder + meters conversion still apply) - see
["Two calling modes"](#two-calling-modes) and "DEM server export format" below.

**Position/velocity units:** raw DB is km(/s); `_convert_km_to_m` converts to m(/s) at the same
loading boundary, so `core/`/the API always see meters (WGS-84 constants assume meters throughout).

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

`hk_loader.py`/`schema_map.py`/`time_sync.py` have no `api/`/FastAPI dependency, and
`HKLoader(connection_url=...)` needs no `config.py` at all (only `from_env()`/`for_satellite()`
lazily import it) - drop just those three files into another codebase to use standalone:

```python
from core.loader import HKLoader

loader = HKLoader("mysql+pymysql://user:pass@host:3306/nstanl")
df = loader.load(start_time="2026-08-20", end_time="2026-08-21", satellite_id="O1B")
```

The rest of `core/` (`coordinates.py`, `geometry/footprint.py`, `math_utils/quat.py`,
`propagation.py`, `validator/ops_rules.py`) is equally self-contained (stdlib + pandas/numpy
only) for the coordinate/footprint/validator logic. Same source backs the HTTP API below.

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
| `POST /telemetry/mission-hk` | Same position/attitude, shaped as the DEM server's `mission_hk` dict (camelCase, column arrays) | `satellite_id` + time range (real DB) |
| `POST /telemetry/czml` | Satellite ground track + attitude for Cesium, fully composed into ECEF by this project (`core/coordinates.py`) - Standalone mode, see "Two calling modes" above. Not for a caller running its own coordinate math (use `/telemetry/mission-hk` for that) | `satellite_id` + time range (real DB) |
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

If a browser frontend (e.g. a Cesium viewer) fetches this API directly, set
`CORS_ALLOWED_ORIGINS` in `.env` to a comma-separated allowlist:

```env
CORS_ALLOWED_ORIGINS=https://dem.example.com,http://localhost:5173
```

Unset (default) blocks all cross-origin browser requests; server-to-server calls are unaffected
either way (CORS is browser-enforced, not server-side).

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

### DEM server export format (`mission_hk`)

HTTP form of [Embedded-in-a-Cesium-host mode](#two-calling-modes) - for a consumer running its
own coordinate math (e.g. DEM's Java/Orekit/Rugged pipeline; DEM's CZML *visualization* itself
now uses `/telemetry/czml` instead). Same position/attitude as `/telemetry/query`, shaped as
column arrays with DEM's `czml_generator.py` field names (`taiSeconds`, `posWrtEci1..3`,
`qbodyWrtEci1..4`) so an existing consumer needs no renaming:

```bash
curl -X POST "http://localhost:8000/telemetry/mission-hk" \
  -H "Content-Type: application/json" \
  -d '{
    "satellite_id": "O1A",
    "start_time": "2026-08-20T00:00:00Z",
    "end_time": "2026-08-20T01:00:00Z"
  }'
```

- `posWrtEci1..3` are **meters** - `czml_generator.py::generate_czml()` (via `build_mission_hk()`)
  still expects km and applies `* 1000.0`, so divide by 1000 first (or rely on
  `attitude-viewer/app.py::api_czml`'s existing km/m auto-detection guard).
- `qbodyWrtEci1..4` is scalar-last, **without** the Body-to-ECI flip (see "Quaternion semantics")
  - the direction DEM's Orekit/Rugged pipeline needs, so no further conjugate is needed
  downstream (a caller using `invert_quaternion_direction=False` gets the same result directly).

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

Returns a GeoJSON `FeatureCollection` (footprint polygon + boresight center point). Same body
works against `POST /footprint/czml` for a CZML packet list instead (polygon + boresight point)
- load alongside `/telemetry/czml` to show ground track and camera coverage together.

Both intersect against a smooth WGS-84 ellipsoid (no terrain). For a caller with its own
terrain/DEM model, use `POST /footprint/rays` instead - given a satellite/time range it returns,
per timestamp, the ECEF ray origin and boresight + 4 FOV-corner unit direction vectors, with no
intersection performed on this side:

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

For the ellipsoid-approximated footprint *polygon* itself, driven by real telemetry (no manual
per-point calls, no terrain model needed), use `POST /footprint/track` (GeoJSON, one Polygon+Point
feature per timestamp) or `POST /footprint/track/czml` (CZML, scoped by `availability` so Cesium
shows the right footprint as the timeline plays) - same request body as `/footprint/rays`:

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

### EOC camera mounting correction (`satellite_id` -> `data/sensor_calibration.json`)

The camera's real as-built mounting misalignment is stored per satellite in
`data/sensor_calibration.json` (shared with `footprint-backend`'s Java/Rugged pipeline - see
[`docs/fov-eoc-boresight.md`](../docs/fov-eoc-boresight.md)). All satellite-driven footprint
endpoints (`/footprint/rays`, `/footprint/track(/czml)`, `/footprint/line/track*`) apply it
automatically from `satellite_id` - the same minimal rotation the Java pipeline uses, applied to
boresight + FOV corners before ellipsoid intersection. No calibrated entry (e.g. `O1A`) means
no-op. `/footprint/compute`/`/footprint/czml` (manual mode) accept an optional `satellite_id` for
the same effect; omit it for uncorrected geometry.

The calibration file resolves next to the repo's `data/` folder by default; override with
`SENSOR_CALIBRATION_PATH` if embedded elsewhere (missing file/satellite/field = no correction).

### Push-broom line footprint (current scan line)

`/footprint/track` treats the camera as a frame sensor (full FOV rectangle per instant). A real
push-broom sensor instead scans one across-track *line* at a time as the satellite moves. `POST
/footprint/line/track` (+ `/geojson`/`/czml` variants) model that: given `fov_across_deg`
(along-track width treated as zero, same as DEM's `SensorConfig`), it returns the left/right
ground points of the line currently being scanned, per HK telemetry sample:

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

This draws "where the sensor plane's current line is" inside the DEM server's existing
rectangular-pyramid FOV visualization (same corner-ray geometry as `/footprint/rays`).

Sample spacing follows raw HK telemetry cadence (~1 Hz), not the camera's real `line_rate`
(hundreds-thousands of Hz) - that would need attitude/position interpolation this project doesn't
implement; the DEM server's Orekit/Rugged pipeline is the source of truth for line-accurate
simulation. This endpoint shows *where* the active line roughly is, at telemetry resolution.

### Orbit propagation (TLE / SGP4, no DB)

Independent of real telemetry - propagates a TLE over a time range via `sgp4` and returns
TEME(~=ECI) position/velocity per timestamp. Useful for a predicted trajectory or when no live
telemetry is available yet:

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

Everything above reads the HK telemetry DB. This endpoint reads a **completely different
database** - the MCE (mission scheduling) server's own DB (`TB_Selected_Mission_Schedule`), and
(via `core/mission/mce_db.py::compute_camera_window`) computes the real camera ON~OFF window from
`MissionParameterJson` (`scanStart`/`camStart`/`camEnd`) - much narrower than the schedule's whole
`eventStart`~`eventEnd` pass window. Requires `MCE_DB_*` in `.env`; unrelated to `MYSQL_*`.

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

For a persistent instance on a shared test server, see [`deploy/README.md`](deploy/README.md)
(one-command Docker deploy script + a systemd-based alternative).

## Running tests

```bash
python -m pytest tests/ -q
```
