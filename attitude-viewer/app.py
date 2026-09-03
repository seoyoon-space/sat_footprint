"""Satellite attitude visualization server.

Flask app that serves a landing page (satellite select) and a Cesium 3D viewer
with attitude data loaded from the HK database — currently O1A and O1B
(HK_ENABLED_SATELLITES), both stored in the same DB under per-satellite tables
(tbl_obs1a_hk* / tbl_obs1b_hk*).

Usage:
    pip install flask numpy pandas python-dotenv
    set CESIUM_ION_TOKEN=your-token-here
    python app.py

    Open http://localhost:5050 in browser.

Query parameters for /api/czml:
    satellite — O1A or O1B, default: O1A
    start  — UTC start time (ISO8601), default: 2026-08-08T03:03:00Z
    end    — UTC end time (ISO8601),   default: 2026-08-08T03:15:00Z
    fov    — FOV half-angle in degrees, default: 1.6 (MultiScape200)
    axes   — show body axes (true/false), default: true
    show_fov — show FOV cone (true/false), default: true
"""

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, render_template, request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "python" / "hk_loader"))
sys.path.insert(0, str(PROJECT_ROOT / "python"))

from dotenv import load_dotenv, dotenv_values
# Load DB connection settings first (hk_loader import triggers pydantic Settings)
load_dotenv(PROJECT_ROOT / "python" / "hk_loader" / ".env")

from czml_generator import generate_czml, build_mission_hk
from core.loader.hk_loader import HKLoader, extract_attitude_columns

import ep_client
import mce_db
from footprint.dem_tiles import ensure_dem_tiles
from footprint.io_adapter import from_dataframe, find_gap_in_range
from footprint.pipeline import PipelineConfig, compute_footprint_to_dataframe
from footprint.response import (
    capture_events,
    footprint_dataframe_to_response,
    footprint_to_geojson,
    load_footprint_rows,
)

# Load Cesium token from .env.cesium (separate from .env to avoid pydantic conflict)
_viewer_env = dotenv_values(Path(__file__).resolve().parent / ".env.cesium")
CESIUM_TOKEN = _viewer_env.get("CESIUM_ION_TOKEN", os.environ.get("CESIUM_ION_TOKEN", ""))

app = Flask(__name__)


class PrefixMiddleware:
    """Lets the app be served under a URL prefix, e.g. so it can be shared on the
    LAN as http://<host>:8080/sat_footprint/ instead of needing its own port."""

    def __init__(self, wsgi_app, prefix=""):
        self.wsgi_app = wsgi_app
        self.prefix = prefix.rstrip("/")

    def __call__(self, environ, start_response):
        if not self.prefix:
            return self.wsgi_app(environ, start_response)

        path = environ.get("PATH_INFO", "")
        if path == self.prefix or path.startswith(self.prefix + "/"):
            environ["PATH_INFO"] = path[len(self.prefix):] or "/"
            environ["SCRIPT_NAME"] = self.prefix
            return self.wsgi_app(environ, start_response)

        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [f"Not found. This app is served under {self.prefix}/".encode()]


URL_PREFIX = os.environ.get("URL_PREFIX", "/sat_footprint")
app.wsgi_app = PrefixMiddleware(app.wsgi_app, prefix=URL_PREFIX)

PAJU_TARGET = {"name": "Paju", "lat": 37.7369, "lon": 126.788}

# HK 텔레메트리 DB 연동이 실제로 구축된 위성 목록 (nstanl DB 안에 위성별 hk1~6
# 테이블이 존재 — schema_map.HK_PACKET_SCHEMA_BY_SATELLITE 참고). 이 목록 밖의
# 위성은 미션 조회/선택은 가능하지만 궤도(CZML)·footprint 실측 계산은 지원하지 않는다.
HK_ENABLED_SATELLITES = {"O1A", "O1B"}
SELECTABLE_SATELLITES = ["O1A", "O1B"]

FOOTPRINT_CSV = PROJECT_ROOT / "data" / "footprint_paju_20260808.csv"

DEFAULT_START = "2026-08-08T03:03:00+00:00"
DEFAULT_END = "2026-08-08T03:15:00+00:00"

# ── Java footprint pipeline (전세계 AOI/미션 footprint on-demand 계산용) ──
JAVA_HOME = os.environ.get("JAVA_HOME", r"C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot")
MAVEN_HOME = os.environ.get("MAVEN_HOME", r"C:\Users\NST_SYLEE\AppData\Local\Programs\apache-maven-3.9.16")
JAVA_PROJECT_DIR = PROJECT_ROOT / "java"
TILES_DIR = PROJECT_ROOT / "data" / "tiles"
TILE_INDEX_PATH = TILES_DIR / "tile_index.json"
OREKIT_DATA_PATH = PROJECT_ROOT / "data" / "orekit-data-master"
SENSOR_CALIBRATION_PATH = PROJECT_ROOT / "data" / "sensor_calibration.json"
DEM_BBOX_BUFFER_DEG = 3.0


def _load_attitude_or_error(satellite, start, end):
    """HK 텔레메트리를 로드하고 자세 컬럼을 추출.

    api_czml()과 api_footprint_compute() 둘 다 똑같은 로드→빈 확인→추출 시퀀스를
    반복하던 걸 통합한 것 — 성공하면 (att_dataframe, None), 실패하면
    (None, error_message)를 반환한다. Flask 응답 형태(czml: [] vs lines: [])는
    라우트마다 다르므로 여기서는 jsonify하지 않는다.
    """
    loader = HKLoader.from_env()
    try:
        df = loader.load(start_time=start, end_time=end, satellite_id=satellite, packets=["hk1", "hk2"])
    except ValueError as exc:
        return None, f"HK 데이터 조회 실패: {exc}"

    if df.empty:
        return None, "해당 구간에 HK 데이터가 없습니다."

    try:
        att = extract_attitude_columns(df, verbose=False)
    except ValueError as exc:
        return None, f"자세 데이터 추출 실패: {exc}"

    return att, None


@app.get("/")
def select():
    """첫 화면: 지구 위에 O1A/O1B가 도는 모습을 보여주고 위성을 선택하게 하는 랜딩 페이지."""
    return render_template("select.html", satellites=SELECTABLE_SATELLITES)


@app.get("/viewer")
def index():
    satellite = (request.args.get("satellite") or "O1A").upper()
    if satellite not in SELECTABLE_SATELLITES:
        satellite = "O1A"
    return render_template(
        "index.html",
        satellite=satellite,
        hk_enabled=satellite in HK_ENABLED_SATELLITES,
    )


@app.get("/cesium-token")
def cesium_token():
    return jsonify({"token": CESIUM_TOKEN})


@app.get("/api/tle/<satellite_id>")
def api_tle(satellite_id):
    """Proxy: EP 서버 TLE 조회 (랜딩 페이지의 O1A/O1B 궤도 애니메이션용)."""
    try:
        data = ep_client.get_tle(satellite_id.upper())
    except Exception as exc:
        return jsonify({"error": f"TLE 조회 실패: {exc}"}), 502
    return jsonify(data)


@app.get("/api/czml")
def api_czml():
    """Generate CZML from HK telemetry data."""
    satellite = (request.args.get("satellite") or "O1A").upper()
    if satellite not in HK_ENABLED_SATELLITES:
        return jsonify({"czml": [], "error": f"{satellite}는 아직 HK DB 연동이 없습니다 (지원: {', '.join(sorted(HK_ENABLED_SATELLITES))})."})

    start = request.args.get("start", DEFAULT_START)
    end = request.args.get("end", DEFAULT_END)
    fov_angle = request.args.get("fov", 1.6, type=float)
    show_axes = request.args.get("axes", "true").lower() == "true"
    show_fov = request.args.get("show_fov", "true").lower() == "true"

    att, error = _load_attitude_or_error(satellite, start, end)
    if error:
        return jsonify({"czml": [], "error": error})

    import pandas as pd
    timestamps_unix = pd.to_datetime(att["timestamp"], utc=True).astype("int64") / 1e9

    pos_km = att[["px", "py", "pz"]].values.astype(float)
    if abs(pos_km[0, 0]) > 100_000:
        pos_km = pos_km / 1000.0

    # extract_attitude_columns outputs scalar-first: q0=w, q1=x, q2=y, q3=z
    # Our HK quaternion is body->ECI, but czml_generator expects ECI->body
    # (it conjugates internally to get body->ECI for Cesium).
    # Pre-conjugate: negate x,y,z to convert body->ECI to ECI->body.
    q_scalar_last = att[["q1", "q2", "q3", "q0"]].values.astype(float)
    q_scalar_last[:, :3] *= -1

    mission_hk = build_mission_hk(timestamps_unix, pos_km, q_scalar_last)

    # The target marker itself is drawn client-side (a standalone Cesium entity the
    # sidebar moves instantly), so it isn't baked into the CZML here — that would leave
    # a stale marker on screen from the previously loaded CZML whenever the user picks
    # a new AOI/mission without reloading the orbit.
    # CZML pyramid uses body -Z for boresight, but O1A body +Z = nadir.
    # Disable CZML pyramid; the JS viewer draws its own footprint using body +Z.
    czml = generate_czml(
        mission_hk,
        fov_angle=fov_angle,
        show_axes=show_axes,
        show_fov=False,
    )

    return jsonify({"czml": czml, "time_range": {"start": start, "end": end}})


@app.get("/api/footprint")
def api_footprint():
    """Serve precomputed footprint CSV as JSON for the 2D map."""
    csv_path = request.args.get("csv", str(FOOTPRINT_CSV))
    if not Path(csv_path).exists():
        return jsonify({"error": f"Footprint CSV not found: {csv_path}", "lines": []})

    df = load_footprint_rows(csv_path)
    return jsonify(footprint_dataframe_to_response(df, PAJU_TARGET))


@app.get("/api/capture-events")
def api_capture_events():
    """Return the footprint scan lines that cross the configured target."""
    csv_path = request.args.get("csv", str(FOOTPRINT_CSV))
    if not Path(csv_path).exists():
        return jsonify({"events": [], "error": f"Footprint CSV not found: {csv_path}"})

    df = load_footprint_rows(csv_path)
    return jsonify({"events": capture_events(df, PAJU_TARGET)})


@app.get("/api/ep/aoi")
def api_ep_aoi():
    """Proxy: EP 서버 AOI 목록 (전세계 촬영 후보지)."""
    try:
        items = ep_client.get_aoi_list()
    except Exception as exc:
        return jsonify({"items": [], "error": f"EP 서버 연결 실패: {exc}"}), 502
    return jsonify({"items": items})


@app.get("/api/ep/missions")
def api_ep_missions():
    """미션 히스토리 (SatelliteId/기간별) — EP 서버의 HTTP API가 아니라 그 백엔드 DB
    (o1b_mce_server.TB_Selected_Mission_Schedule)를 직접 조회한다.

    EP HTTP API(`Mission/selected`)는 이 DB와 데이터가 어긋나는 경우가 확인됐다
    (예: O1A_15379_GGD — API는 좌표 37.3753/128.3973·상태 None을 주지만, DB에는
    37.2957/127.6312·실제 주소("여주...")·상태 4가 들어있고 이쪽이 맞다고 확인됨).
    그래서 API를 거치지 않고 DB에서 직접 읽는다.
    """
    from datetime import datetime, timezone

    satellite = (request.args.get("satellite") or "O1A").upper()
    now = datetime.now(timezone.utc)
    start = request.args.get("start") or f"{now.year - 1}-01-01T00:00:00Z"
    end = request.args.get("end") or now.strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        missions = mce_db.get_missions(satellite, start, end)
    except Exception as exc:
        return jsonify({"missions": [], "error": f"mission DB 연결 실패: {exc}"}), 502
    return jsonify({"missions": missions})


@app.get("/api/footprint/compute")
def api_footprint_compute():
    """선택한 AOI/미션의 시간대 HK 데이터로 실시간 footprint를 계산 (전세계 대응).

    Query params:
        start, end          ISO8601 UTC — HK 조회 구간 (Orekit 궤도보간에 필요한
                             넉넉한 구간, 보통 실제 촬영시간 앞뒤로 몇 분 패딩됨)
        line_start, line_end ISO8601 UTC — 실제로 화면에 그릴 footprint 라인 구간
                             (보통 실제 촬영 시작~끝). 생략하면 start/end를 그대로 씀 —
                             그러면 패딩 때문에 스트립이 실제 촬영 구간보다 훨씬 길게
                             (경우에 따라 수천 km) 그려진다.
        geojson_start/end    ISO8601 UTC — 미션의 EventStart~EventEnd(스케줄/패스 구간)
                             기준 GeoJSON. 지도에 핑크 오버레이로 표시됨. 생략하면
                             지도 스트립(line_start~line_end)과 동일한 범위를 쓴다.
        capture_start/end    ISO8601 UTC — 미션의 실제 카메라 ON~OFF 구간
                             (camStart~camEnd, mce_db.py compute_camera_window 참고).
                             지도에 보라색 오버레이로 표시됨. 생략하면 응답의
                             geojson_capture는 null.
        target_lat/lon       확인할 지점 좌표
        target_name          지점 이름 (표시용)
        satellite            위성 ID (기본 O1A; HK_ENABLED_SATELLITES에 등록된 위성만 지원)
    """
    import pandas as pd

    start = request.args.get("start")
    end = request.args.get("end")
    line_start = request.args.get("line_start") or start
    line_end = request.args.get("line_end") or end
    geojson_start = request.args.get("geojson_start")
    geojson_end = request.args.get("geojson_end")
    capture_start = request.args.get("capture_start")
    capture_end = request.args.get("capture_end")
    target_lat = request.args.get("target_lat", type=float)
    target_lon = request.args.get("target_lon", type=float)
    target_name = request.args.get("target_name", "TARGET")
    satellite = (request.args.get("satellite") or "O1A").upper()

    if not start or not end or target_lat is None or target_lon is None:
        return jsonify({"lines": [], "error": "start/end/target_lat/target_lon이 필요합니다."}), 400

    if satellite not in HK_ENABLED_SATELLITES:
        return jsonify({"lines": [], "error": f"{satellite}는 아직 HK DB 연동이 없습니다 (지원: {', '.join(sorted(HK_ENABLED_SATELLITES))})."}), 400

    target = {"name": target_name, "lat": target_lat, "lon": target_lon}

    att, error = _load_attitude_or_error(satellite, start, end)
    if error:
        return jsonify({"lines": [], "error": error})
    states = from_dataframe(att)

    if len(states) < 6:
        return jsonify({
            "lines": [],
            "error": f"유효한 HK 샘플이 {len(states)}개뿐입니다 (Orekit 보간에 최소 6개 필요). "
                     "start/end 구간을 더 넓게 잡아주세요.",
        })

    # HK 공백(GPS dropout 등) 구간을 걸치는 footprint 계산은 Java/Rugged를 아예 부르지
    # 않고 여기서 빠르게 에러를 낸다 — 그대로 넘기면 무리한 보간 궤적을 DEM과
    # 교차시키려다 수 분/수 GB로 폭주하는 게 확인됨 (find_gap_in_range 참고).
    line_start_dt = pd.to_datetime(line_start, utc=True)
    line_end_dt = pd.to_datetime(line_end, utc=True)
    gap = find_gap_in_range(states, line_start_dt.to_pydatetime(), line_end_dt.to_pydatetime())
    if gap:
        gap_start, gap_end = gap
        return jsonify({
            "lines": [],
            "error": f"HK 텔레메트리에 공백이 있어 이 구간은 계산할 수 없습니다 "
                     f"({gap_start.isoformat()} ~ {gap_end.isoformat()}, "
                     f"{int((gap_end - gap_start).total_seconds())}초 공백). "
                     "GPS dropout 등으로 실제 데이터가 비어있는 구간입니다 — "
                     "시간대를 조금 옮겨서 다시 시도해주세요.",
        })

    dem_info = ensure_dem_tiles(
        target_lat - DEM_BBOX_BUFFER_DEG, target_lat + DEM_BBOX_BUFFER_DEG,
        target_lon - DEM_BBOX_BUFFER_DEG, target_lon + DEM_BBOX_BUFFER_DEG,
        tiles_dir=TILES_DIR, index_path=TILE_INDEX_PATH,
    )

    config = PipelineConfig(
        java_home=JAVA_HOME,
        maven_home=MAVEN_HOME,
        java_project_dir=str(JAVA_PROJECT_DIR),
        tile_index_path=str(TILE_INDEX_PATH),
        orekit_data_path=str(OREKIT_DATA_PATH),
        sensor_calibration_path=str(SENSOR_CALIBRATION_PATH),
    )

    # %f(마이크로초) 포함 — pipeline._default_line_step()가 이 문자열을 다시 파싱해
    # 창 길이를 계산하므로, 초 단위로 잘라버리면 line_step 계산이 부정확해진다.
    start_utc = line_start_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    end_utc = line_end_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")

    try:
        # line_step is left unset — compute_footprint() auto-scales it to the
        # start_utc~end_utc window (see pipeline._default_line_step) so this safety
        # margin lives with the Java-calling code, not duplicated at every call site.
        result_df = compute_footprint_to_dataframe(
            states, config, start_utc=start_utc, end_utc=end_utc,
            satellite_id=satellite,
        )
    except RuntimeError as exc:
        return jsonify({"lines": [], "error": str(exc)}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"lines": [], "error": "Footprint 계산이 시간 초과됐습니다 (5분). 구간을 좁혀서 다시 시도해주세요."}), 504

    geojson_df = None
    if geojson_start and geojson_end and result_df is not None and not result_df.empty:
        geojson_df = result_df.copy()
        geojson_df["time_utc"] = pd.to_datetime(geojson_df["time_utc"], utc=True)
        gj_start = pd.to_datetime(geojson_start, utc=True)
        gj_end = pd.to_datetime(geojson_end, utc=True)
        geojson_df = geojson_df[(geojson_df["time_utc"] >= gj_start) & (geojson_df["time_utc"] <= gj_end)]

    capture_df = None
    if capture_start and capture_end and result_df is not None and not result_df.empty:
        capture_df = result_df.copy()
        capture_df["time_utc"] = pd.to_datetime(capture_df["time_utc"], utc=True)
        cap_start = pd.to_datetime(capture_start, utc=True)
        cap_end = pd.to_datetime(capture_end, utc=True)
        capture_df = capture_df[(capture_df["time_utc"] >= cap_start) & (capture_df["time_utc"] <= cap_end)]

    response = footprint_dataframe_to_response(result_df, target, geojson_df=geojson_df)
    response["geojson_capture"] = footprint_to_geojson(capture_df, target) if capture_df is not None else None
    response["dem"] = dem_info
    return jsonify(response)


if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))
    if not CESIUM_TOKEN:
        print("WARNING: CESIUM_ION_TOKEN not set. Globe imagery may not load.")
        print("  Set it: set CESIUM_ION_TOKEN=your-token-here")
        print()
    print(f"Loading O1A HK data from DB (default: {DEFAULT_START} ~ {DEFAULT_END})")
    print(f"Starting attitude visualization server on http://localhost:{PORT}{URL_PREFIX}/")
    # threaded=True: without it, the dev server handles one request at a time — a slow
    # /api/footprint/compute (Java subprocess, can take 1min+ on a brand-new DEM region)
    # would otherwise block every other request (TLE polling, mission list, live tracking)
    # for its whole duration, making the whole app look frozen instead of just that call.
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
