"""MCE(미션 스케줄링) 서버 DB에서 미션 스케줄과 실제 카메라 ON/OFF 구간을 읽어온다.

HK DB(nstanl, core/loader 쪽이 다루는 위성 텔레메트리)와는 완전히 별개의 DB다 - 이 DB는
"언제 어떤 위성이 무엇을 촬영하도록 예약/실행됐는지"를 담은 미션 스케줄 테이블
(TB_Selected_Mission_Schedule)을 갖고 있다. EventStart/EventEnd는 스케줄링/패스
구간이라 실제 촬영 시간(보통 ~10초대)보다 훨씬 넓으므로, MissionParameterJson에
담긴 카메라 타이밍 파라미터로부터 실제 ON~OFF 구간을 별도 계산해야 한다
(compute_camera_window 참고) - DEM 서버 쪽 attitude-viewer/mce_db.py의 동일 로직을
그대로 포팅.

읽기 전용: 이 모듈은 SELECT만 수행한다 - 절대 이 DB에 쓰지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

TABLE = "TB_Selected_Mission_Schedule"


def _iso_utc(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    # timespec="milliseconds": JS Date는 소수점 자릿수가 정확히 3자리여야 파싱된다 -
    # 이 컬럼은 datetime(6)(마이크로초)이라 그대로 isoformat()하면 "...496143Z" 같은
    # 값이 나와 프론트엔드 Date 파싱이 깨진다(원본 DEM 서버 코드에서 확인된 문제).
    return dt.replace(tzinfo=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_float(value: Any, default: float = 0.0) -> float:
    """빈 값/문자열/None을 안전하게 float으로 변환(MissionParameterJson 필드는 전부 문자열)."""
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
    훨씬 넓다(예: 66초짜리 pass인데 실제 카메라 ON 시간은 10.4초뿐인 경우가 실측으로
    확인됨). 실제 구간은 3단계로 계산된다:
        scan_start = utcSec + utcMsec/1000             (임무 스캔 시작 시각)
        cam_start  = scan_start + camStartMsec/1000     (카메라 ON - 지연 후)
        cam_end    = cam_start + camDurationUsec/1e6    (카메라 OFF - 지속시간 경과 후)

    Returns:
        (scan_start_iso, cam_start_iso, cam_end_iso) - 파싱 실패/필드 누락 시 전부 None.
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
    import json

    def _parse_json_field(raw: str | None) -> dict | list | None:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None

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
        # 스케줄링 시점에 채워짐(의도한 촬영 대상의 주소+폴리곤) - 실제 촬영/처리 여부와 무관.
        "clientData": client_data,
        # 실제로 영상이 처리된 뒤에만 채워짐(zip/png/bounds 등).
        "results": results,
        # MissionParameterJson으로부터 계산한 실제 카메라 ON~OFF 구간 - eventStart~eventEnd
        # 보다 훨씬 좁은 진짜 촬영 순간. MissionParameterJson이 없는 미션(구형/GS 항목 등)은 None.
        "scanStart": scan_start,
        "camStart": cam_start,
        "camEnd": cam_end,
    }


def _to_mysql_datetime(iso: str) -> str:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _connect():
    import pymysql

    from config import get_mce_db_config

    cfg = get_mce_db_config()
    return pymysql.connect(
        host=cfg.db_host,
        port=cfg.db_port,
        user=cfg.db_user,
        password=cfg.db_password,
        database=cfg.db_name,
        connect_timeout=8,
        read_timeout=15,
        cursorclass=pymysql.cursors.DictCursor,
        charset="utf8mb4",
    )


def get_missions(satellite_id: str, start_iso: str, end_iso: str) -> list[dict]:
    """지정 위성/기간의 미션 스케줄 원본 행(실제 카메라 ON/OFF 구간 포함)을 MCE DB에서 읽는다."""
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
