# SAT Simulation API

HK telemetry loader and API for querying spacecraft housekeeping (HK) packets from MySQL.

## Overview

This project loads housekeeping data from the real DB layout used in the target system:

- Schema: `nstanl`
- Tables: `tbl_obs1a_hk1`, `tbl_obs1a_hk2`, ..., `tbl_obs1a_hk6`
- Time column: `timeUtc`
- Time unit: Unix epoch seconds (UTC)

Users can provide KST or UTC timestamps in friendly formats such as:

- `2026-08-20`
- `2026-08-20T15:00:00+09:00`
- `2026-08-20T00:00:00Z`
- `1787203236`

The loader converts those inputs into UTC epoch seconds for DB queries and normalizes the merged output to a standard `time` column in the DataFrame.

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

The canonical mapping used by the loader is:

- `hk1` -> `nstanl.tbl_obs1a_hk1`
- `hk2` -> `nstanl.tbl_obs1a_hk2`
- `hk3` -> `nstanl.tbl_obs1a_hk3`
- `hk4` -> `nstanl.tbl_obs1a_hk4`
- `hk5` -> `nstanl.tbl_obs1a_hk5`
- `hk6` -> `nstanl.tbl_obs1a_hk6`

The master packet is `hk1`.

Time semantics:

- DB field: `timeUtc`
- Unit: Unix epoch seconds (UTC)
- User-facing input: KST or UTC strings are accepted and internally normalized
- Output DataFrame: standard `time` column in UTC-aware pandas timestamps

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

loader = HKLoader.for_satellite("O1A")
df = loader.load(
    start_time="2026-08-20",
    end_time="2026-08-21",
)
```

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

### Authentication

If `API_KEY` is set in `.env`, `/telemetry/*`, `/footprint/*`, and `/validator/*` require an
`X-API-Key` header matching that value. If `API_KEY` is unset (local development default), no
auth is enforced. `/health` never requires a key.

```bash
curl -H "X-API-Key: your_api_key" http://localhost:8000/telemetry/query ...
```

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

## Running tests

```bash
python -m pytest tests/ -q
```
