"""Read-only access to the EP/MCE server's own backing database (o1b_mce_server).

The EP HTTP API (http://192.168.0.82:8080) was found to return stale/wrong
latitude/longitude/status for several scheduleIds — e.g. O1A_15379_GGD via HTTP
gives (37.3753, 128.3973) with status "None", while this DB's
TB_Selected_Mission_Schedule row for the same scheduleId has (37.2957, 127.6312)
with a real address ("Yeoju...") and status 4 — confirmed correct by the user
independently via Google Maps and the SatOps mission-history page. So this module
reads the DB directly instead of going through the HTTP API.

SECURITY: read-only. Only ever issue SELECT here — never write to this DB.
"""
from __future__ import annotations

import json
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from datetime import datetime, timezone
from pathlib import Path

import pymysql

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SECRETS_PATH = PROJECT_ROOT / "secrets.toml"

TABLE = "TB_Selected_Mission_Schedule"


def _load_config() -> dict:
    with open(SECRETS_PATH, "rb") as f:
        secrets = tomllib.load(f)
    return secrets["mce_db"]


def _connect():
    cfg = _load_config()
    return pymysql.connect(
        host=cfg["db_host"],
        port=int(cfg.get("db_port", 3306)),
        user=cfg["db_user"],
        password=cfg["db_password"],
        database=cfg["db_name"],
        connect_timeout=8,
        read_timeout=15,
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
    )


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # timespec="milliseconds": JS Date only accepts exactly 3 fractional digits —
    # this column is datetime(6) (microseconds), which produced e.g. "...496143Z"
    # and broke the frontend's Date parsing (addSeconds -> toISOString threw).
    return dt.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_json_field(raw: str | None) -> dict | list | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _safe_float(value, default: float = 0.0) -> float:
    """빈 값/문자열/None 을 안전하게 float으로 변환 (MissionParameterJson 필드는 전부 문자열)."""
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _unix_to_iso(unix_ts: float) -> str:
    dt = datetime.fromtimestamp(unix_ts, tz=timezone.utc)
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def compute_camera_window(mission_params: dict | None) -> tuple[str | None, str | None, str | None]:
    """미션의 MissionParameterJson으로부터 실제 카메라 ON~OFF 구간을 계산.

    EventStart/EventEnd는 스케줄링/패스 구간이라 실제 촬영 시간(보통 ~10초대)보다
    훨씬 넓다 (예: 66초짜리 pass인데 실제 카메라 ON 시간은 10.4초뿐인 경우 확인됨).
    실제 구간은 3단계로 계산된다:
        scan_start = utcSec + utcMsec/1000            (임무 스캔 시작 시각)
        cam_start  = scan_start + camStartMsec/1000    (카메라 ON — 지연 후)
        cam_end    = cam_start + camDurationUsec/1e6   (카메라 OFF — 지속시간 경과 후)

    Returns:
        (scan_start_iso, cam_start_iso, cam_end_iso) — 파싱 실패/필드 누락 시 전부 None.
    """
    if not mission_params:
        return None, None, None

    settings = mission_params.get("missionSettings") or {}
    start_time = settings.get("startTime") or {}
    camera = settings.get("cameraSettings") or {}

    utc_sec = _safe_float(start_time.get("utcSec"), default=0.0)
    if not utc_sec:
        return None, None, None

    utc_msec = _safe_float(start_time.get("utcMsec"))
    cam_start_msec = _safe_float(camera.get("camStartMsec"))
    cam_duration_usec = _safe_float(camera.get("camDurationUsec"), default=10_000_000)

    scan_start_unix = utc_sec + utc_msec / 1_000
    cam_start_unix = scan_start_unix + cam_start_msec / 1_000
    cam_end_unix = cam_start_unix + cam_duration_usec / 1_000_000

    return _unix_to_iso(scan_start_unix), _unix_to_iso(cam_start_unix), _unix_to_iso(cam_end_unix)


def _row_to_mission(row: dict) -> dict:
    client_data = _parse_json_field(row.get("ClientData"))
    results = _parse_json_field(row.get("Results"))
    mission_params = _parse_json_field(row.get("MissionParameterJson"))
    scan_start, cam_start, cam_end = compute_camera_window(mission_params)

    return {
        "id": row["Id"],
        "scheduleId": row["ScheduleId"],
        "satelliteId": row.get("SatelliteId"),
        "operationId": row.get("OperationId"),
        "location": row.get("LocationName"),
        "aoiId": row.get("AoiId"),
        "latitude": row.get("Latitude"),
        "longitude": row.get("Longitude"),
        "eventStart": _iso_utc(row.get("EventStart")),
        "eventEnd": _iso_utc(row.get("EventEnd")),
        "eventStartKST": row.get("EventStartLocal").isoformat() if row.get("EventStartLocal") else None,
        "eventEndKST": row.get("EventEndLocal").isoformat() if row.get("EventEndLocal") else None,
        "duration": row.get("Duration"),
        "maxEl": row.get("MaxEl"),
        "cloudAmount": row.get("CloudAmount"),
        "requestedScanTime": row.get("RequestedScanTime"),
        "note": row.get("Note"),
        "missionStatus": row.get("MissionStatus"),
        "imageStatus": row.get("ImageStatus"),
        # Populated at scheduling time (address + polygon of the intended target area),
        # independent of whether the image has ever been processed.
        "clientData": client_data,
        # Populated only once the image has actually been processed (zip/png/bounds).
        "results": results,
        # The actual camera ON~OFF window, computed from MissionParameterJson (scan
        # start + camStartMsec delay + camDurationUsec duration) — this is the real
        # capture instant, much narrower than eventStart~eventEnd. None if the mission
        # has no MissionParameterJson (e.g. older/GS entries).
        "scanStart": scan_start,
        "camStart": cam_start,
        "camEnd": cam_end,
    }


def _to_mysql_datetime(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def get_missions(satellite_id: str, start_iso: str, end_iso: str) -> list[dict]:
    """Mission rows straight from the EP/MCE server's own DB (ground truth)."""
    start_sql = _to_mysql_datetime(start_iso)
    end_sql = _to_mysql_datetime(end_iso)

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT Id, ScheduleId, OperationId, SatelliteId, MissionStatus, ImageStatus,
                       AoiId, LocationName, Latitude, Longitude, EventStart, EventEnd,
                       Duration, MaxEl, CloudAmount, RequestedScanTime,
                       EventStartLocal, EventEndLocal, Note, ClientData, Results,
                       MissionParameterJson
                FROM {TABLE}
                WHERE SatelliteId = %s AND EventStart BETWEEN %s AND %s
                ORDER BY EventStart
                """,
                (satellite_id, start_sql, end_sql),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return [_row_to_mission(r) for r in rows]
