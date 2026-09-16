r"""Satellite attitude visualization server — entry point for the whole app.

Flask app that serves a landing page (satellite select) and a Cesium 3D viewer
with attitude data loaded from the HK database — currently O1A and O1B
(HK_ENABLED_SATELLITES), both stored in the same DB under per-satellite tables
(tbl_obs1a_hk* / tbl_obs1b_hk*).

This is the only thing a user talks to. It does no heavy computation itself —
it calls out to three other pieces and combines their results into HTML/JSON:
  - hk_api (separate FastAPI process, port 8001) — HK telemetry + mission schedule,
    reached either via direct HTTP calls (_load_attitude_or_error,
    _load_attitude_ecef_or_error) or transparently proxied under /api/hk/<subpath>
  - footprint-backend (Java/Orekit/Rugged, footprint-backend/) — run as a subprocess
    from /api/footprint/compute for real DEM-based footprint computation
  - EP server / mission DBs (ep_client.py, mce_db.py, mps_db.py) — AOI/mission lookups

Routes fall into three groups: page routes (/, /viewer, /old, /old/viewer — render
templates only), data API routes (/api/czml, /api/footprint/compute, /api/ep/*, etc.
— the actual orchestration), and the /api/hk/* reverse proxy to hk_api.

Run (hk_api first, then this — two separate terminals, both stay in the foreground):

    # terminal 1 — hk_api (port 8001)
    cd hk_api
    .\.venv\Scripts\Activate.ps1
    python -m uvicorn main:app --host 127.0.0.1 --port 8001

    # terminal 2 — this file (port 8080)
    cd attitude-viewer
    python app.py

    Open http://localhost:8080/sat_footprint/ in browser.

Query parameters for /api/czml:
    satellite   — O1A or O1B, default: O1A
    start       — UTC start time (ISO8601), default: 2026-08-08T03:03:00Z
    end         — UTC end time (ISO8601),   default: 2026-08-08T03:15:00Z
    axes        — show body axes (true/false), default: true
    coord_model — which ECI->ECEF model converts the orbit for rendering:
        "cesium" (default) — raw ECI position/orientation from hk_api's
            /telemetry/query, tagged referenceFrame: INERTIAL; Cesium itself
            converts to the fixed frame client-side at render time.
        "hkapi" — pre-converted ECEF position/orientation from hk_api's own
            /telemetry/czml?coordinate_frame=ecef (its IAU-76/FK5 model).
            Empirically the two agree to ~5m / ~0.002deg (see conversation),
            so this is mainly a debug/comparison knob, not a correctness fix.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import requests
from flask import Flask, Response, jsonify, render_template, request

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FOOTPRINT_BACKEND_ROOT = PROJECT_ROOT / "footprint-backend"
sys.path.insert(0, str(FOOTPRINT_BACKEND_ROOT / "python"))

from dotenv import dotenv_values

from czml_generator import generate_czml, build_mission_hk

import ep_client
import mce_db
import mps_db
from footprint.dem_tiles import ensure_dem_tiles
from footprint.io_adapter import from_dataframe, find_gap_in_range
from footprint.pipeline import PipelineConfig, compute_footprint_to_dataframe
from footprint.response import footprint_dataframe_to_response, footprint_to_geojson

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

# HK 텔레메트리 DB 연동이 실제로 구축된 위성 목록 (nstanl DB 안에 위성별 hk1~6
# 테이블이 존재 — schema_map.HK_PACKET_SCHEMA_BY_SATELLITE 참고). 이 목록 밖의
# 위성은 미션 조회/선택은 가능하지만 궤도(CZML)·footprint 실측 계산은 지원하지 않는다.
HK_ENABLED_SATELLITES = {"O1A", "O1B"}
SELECTABLE_SATELLITES = ["O1A", "O1B"]

# hk_api (doeun-space's standalone FastAPI app, vendored under hk_api/) runs on its own
# port/venv — this reverse-proxies it under this app's single port/prefix instead of
# making callers reach a second port directly. Kept under /api/hk/ specifically (not
# flattened into /api/) because this app already has its own /api/footprint/compute
# (Java/Orekit/Rugged-based) — a different implementation of the same-sounding path.
HK_API_BASE_URL = os.environ.get("HK_API_BASE_URL", "http://localhost:8001")
_HK_API_PROXY_EXCLUDED_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}


@app.route("/api/hk/<path:subpath>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
def proxy_hk_api(subpath):
    upstream = requests.request(
        method=request.method,
        url=f"{HK_API_BASE_URL}/{subpath}",
        params=request.args,
        data=request.get_data(),
        headers={k: v for k, v in request.headers if k.lower() != "host"},
        timeout=60,
    )
    headers = [(k, v) for k, v in upstream.headers.items() if k.lower() not in _HK_API_PROXY_EXCLUDED_HEADERS]
    return Response(upstream.content, status=upstream.status_code, headers=headers)

DEFAULT_START = "2026-08-08T03:03:00+00:00"
DEFAULT_END = "2026-08-08T03:15:00+00:00"

# ── Java footprint pipeline (전세계 AOI/미션 footprint on-demand 계산용) ──
JAVA_HOME = os.environ.get("JAVA_HOME", r"C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot")
MAVEN_HOME = os.environ.get("MAVEN_HOME", r"C:\Users\NST_SYLEE\AppData\Local\Programs\apache-maven-3.9.16")
JAVA_PROJECT_DIR = FOOTPRINT_BACKEND_ROOT / "java"
TILES_DIR = PROJECT_ROOT / "data" / "tiles"
TILE_INDEX_PATH = TILES_DIR / "tile_index.json"
OREKIT_DATA_PATH = PROJECT_ROOT / "data" / "orekit-data-master"
SENSOR_CALIBRATION_PATH = PROJECT_ROOT / "data" / "sensor_calibration.json"
DEM_BBOX_BUFFER_DEG = 3.0

# 실시간 진행률 표시용 — Java가 계산 도중 {"done":N,"total":M}을 이 디렉토리 밑에 job_id별로
# 써주고, /api/footprint/progress가 그 파일을 폴링해서 읽는다 (api_footprint_compute 참고).
PROGRESS_DIR = PROJECT_ROOT / "data" / "progress"


def _load_attitude_or_error(satellite, start, end):
    """hk_api(/telemetry/query — doeun-space의 FastAPI 서비스, 이 앱 안에선 hk_api/로
    벤더링해 8001에서 띄우고 /api/hk/*로 프록시함)에서 HK 텔레메트리를 가져와 자세
    컬럼 DataFrame(timestamp,px,py,pz,vx,vy,vz,q0,q1,q2,q3)으로 변환.


    api_czml()과 api_footprint_compute() 둘 다 똑같은 로드→빈 확인→추출 시퀀스를
    반복하던 걸 통합한 것 — 성공하면 (att_dataframe, None), 실패하면
    (None, error_message)를 반환한다. Flask 응답 형태(czml: [] vs lines: [])는
    라우트마다 다르므로 여기서는 jsonify하지 않는다.
    """
    import pandas as pd

    try:
        resp = requests.post(
            f"{HK_API_BASE_URL}/telemetry/query",
            json={"satellite_id": satellite, "start_time": start, "end_time": end},
            timeout=60,
        )
    except requests.RequestException as exc:
        return None, f"hk_api 연결 실패: {exc}"

    if resp.status_code != 200:
        return None, f"HK 데이터 조회 실패 (hk_api {resp.status_code}): {resp.text[:300]}"

    records = resp.json().get("records", [])
    if not records:
        return None, "해당 구간에 HK 데이터가 없습니다."

    raw = pd.DataFrame(records)
    required_cols = [
        "time", "pos_wrt_eci1", "pos_wrt_eci2", "pos_wrt_eci3",
        "vel_wrt_eci1", "vel_wrt_eci2", "vel_wrt_eci3",
        "qbody_wrt_eci1", "qbody_wrt_eci2", "qbody_wrt_eci3", "qbody_wrt_eci4",
    ]
    missing = [c for c in required_cols if c not in raw.columns]
    if missing:
        return None, f"hk_api 응답에 필요한 컬럼이 없습니다: {missing}"

    # hk_api의 core/loader/hk_loader.py(_fetch_packet)는 qbody_wrt_eci1..4를 scalar-first로
    # 재정렬만 하고(방향은 뒤집지 않음) 내보낸다 — 그 상태가 이미 Body->ECI라는 게 실측
    # 확인됨(A/B 테스트: Java/Rugged 파이프라인 + hk_api 자체 ECI->ECEF 합성 둘 다, 뒤집으면
    # 지구조차 안 보는 방향이 나오고 안 뒤집어야 실제 타겟 근처로 나옴 — hk_api 쪽
    # _invert_quaternion_rotation_direction() 호출 자체를 빼서 고침, core/loader/hk_loader.py
    # 참고). 그래서 여기서는 추가 반전 없이 그대로 쓴다.
    att = pd.DataFrame({
        "timestamp": pd.to_datetime(raw["time"], utc=True),
        "px": raw["pos_wrt_eci1"].astype(float),
        "py": raw["pos_wrt_eci2"].astype(float),
        "pz": raw["pos_wrt_eci3"].astype(float),
        "vx": raw["vel_wrt_eci1"].astype(float),
        "vy": raw["vel_wrt_eci2"].astype(float),
        "vz": raw["vel_wrt_eci3"].astype(float),
        "q0": raw["qbody_wrt_eci1"].astype(float),
        "q1": raw["qbody_wrt_eci2"].astype(float),
        "q2": raw["qbody_wrt_eci3"].astype(float),
        "q3": raw["qbody_wrt_eci4"].astype(float),
    })

    return att, None


def _load_attitude_ecef_or_error(satellite, start, end):
    """hk_api의 자체 ECI->ECEF 모델(/telemetry/czml?coordinate_frame=ecef, IAU-76/FK5
    세차+장동+GMST, hk_api/core/coordinates.py)로 이미 변환된 position/orientation을
    가져온다. coord_model=hkapi일 때만 쓰이는 경로 — _load_attitude_or_error(raw ECI,
    기본 경로)와 실측 비교 결과 위치 ~5m, 자세 ~0.002deg 차이로 사실상 동일함을
    확인했으므로 정확도 목적이 아니라 비교/디버그 목적의 경로다.

    반환: 성공 시 (timestamps_unix, pos_km(N,3), q_scalar_last(N,4) x,y,z,w), None
          실패 시 None, error_message
    """
    import pandas as pd

    try:
        resp = requests.post(
            f"{HK_API_BASE_URL}/telemetry/czml",
            params={"coordinate_frame": "ecef", "include_pointing": "false"},
            json={"satellite_id": satellite, "start_time": start, "end_time": end},
            timeout=60,
        )
    except requests.RequestException as exc:
        return None, f"hk_api 연결 실패: {exc}"

    if resp.status_code != 200:
        return None, f"HK 데이터 조회 실패 (hk_api {resp.status_code}): {resp.text[:300]}"

    packets = resp.json()
    sat = next((p for p in packets if p.get("id") not in (None, "document")), None)
    if not sat or "position" not in sat or "orientation" not in sat:
        return None, "hk_api CZML 응답에 position/orientation이 없습니다."

    epoch = pd.Timestamp(sat["position"]["epoch"])
    pos = sat["position"]["cartesian"]           # [dt, x, y, z, ...] meters, ECEF
    quat = sat["orientation"]["unitQuaternion"]  # [dt, x, y, z, w, ...] body->ECEF

    n = len(pos) // 4
    if n == 0 or len(quat) // 5 != n:
        return None, "hk_api CZML 응답의 position/orientation 샘플 수가 일치하지 않습니다."

    timestamps_unix = np.array([(epoch + pd.Timedelta(seconds=pos[i * 4])).timestamp() for i in range(n)])
    pos_km = np.array([[pos[i * 4 + 1], pos[i * 4 + 2], pos[i * 4 + 3]] for i in range(n)]) / 1000.0
    q_scalar_last = np.array([[quat[i * 5 + 1], quat[i * 5 + 2], quat[i * 5 + 3], quat[i * 5 + 4]] for i in range(n)])

    return (timestamps_unix, pos_km, q_scalar_last), None


@app.get("/")
def select():
    """첫 화면: 지구 위에 O1A/O1B가 도는 모습을 보여주고 위성을 선택하게 하는 랜딩 페이지."""
    return render_template("select.html", satellites=SELECTABLE_SATELLITES, old=False)


@app.get("/viewer")
def index():
    satellite = (request.args.get("satellite") or "O1A").upper()
    if satellite not in SELECTABLE_SATELLITES:
        satellite = "O1A"
    coord_model = (request.args.get("coord_model") or "cesium").lower()
    if coord_model not in ("cesium", "hkapi"):
        coord_model = "cesium"
    return render_template(
        "index.html",
        satellite=satellite,
        hk_enabled=satellite in HK_ENABLED_SATELLITES,
        old=False,
        coord_model=coord_model,
    )


# ── /old — 구버전 mission planning schedule log(tbl_mps_mission_sch) 테스트/비교용 미러 ──
# 페이지·JS는 위 라우트들과 완전히 동일하고, 미션 조회만 mps_db(구버전 DB)로 간다
# (window.APP_OLD_SERVER 플래그로 sidebar.js가 /api/ep/missions에 old=1을 붙여 호출).
@app.get("/old")
def select_old():
    return render_template("select.html", satellites=SELECTABLE_SATELLITES, old=True)


@app.get("/old/viewer")
def index_old():
    satellite = (request.args.get("satellite") or "O1A").upper()
    if satellite not in SELECTABLE_SATELLITES:
        satellite = "O1A"
    return render_template(
        "index.html",
        satellite=satellite,
        hk_enabled=satellite in HK_ENABLED_SATELLITES,
        old=True,
        coord_model="cesium",
    )


@app.get("/cesium-token")
def cesium_token():
    return jsonify({"token": CESIUM_TOKEN})


@app.get("/api/sensor-calibration/<satellite_id>")
def api_sensor_calibration(satellite_id):
    """3D 뷰어(cesium-viewer.js)의 FOV가 FootprintCalculator.java(SensorCalibration.java가
    읽는 것과 동일한 파일)와 같은 EOC 마운팅 보정 벡터를 쓸 수 있도록 노출."""
    satellite = satellite_id.upper()
    try:
        calibration = json.loads(SENSOR_CALIBRATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        calibration = {}
    vector = calibration.get(satellite, {}).get("eoc_misalignment_unit_vector", [0.0, 0.0, 0.0])
    return jsonify({"eoc_misalignment_unit_vector": vector})


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
    show_axes = request.args.get("axes", "true").lower() == "true"
    coord_model = request.args.get("coord_model", "cesium")
    if coord_model not in ("cesium", "hkapi"):
        return jsonify({"czml": [], "error": f"coord_model은 cesium 또는 hkapi여야 합니다 (받음: {coord_model})."})

    if coord_model == "hkapi":
        # hk_api 자체 ECI->ECEF 모델로 이미 변환된 position/orientation 사용.
        result, error = _load_attitude_ecef_or_error(satellite, start, end)
        if error:
            return jsonify({"czml": [], "error": error})
        timestamps_unix, pos_km, q_scalar_last = result
        mission_hk = build_mission_hk(timestamps_unix, pos_km, q_scalar_last)
        czml = generate_czml(mission_hk, show_axes=show_axes, frame="fixed")
        return jsonify({"czml": czml, "time_range": {"start": start, "end": end}, "coord_model": coord_model})

    att, error = _load_attitude_or_error(satellite, start, end)
    if error:
        return jsonify({"czml": [], "error": error})

    import pandas as pd
    timestamps_unix = pd.to_datetime(att["timestamp"], utc=True).astype("int64") / 1e9

    pos_km = att[["px", "py", "pz"]].values.astype(float)
    if abs(pos_km[0, 0]) > 100_000:
        pos_km = pos_km / 1000.0

    # extract_attitude_columns outputs scalar-first: q0=w, q1=x, q2=y, q3=z.
    # Reindex to scalar-last (x,y,z,w) for build_mission_hk/czml_generator — no sign
    # flip needed (verified empirically: an actual ECI<->Body flip here breaks the
    # Java/Rugged footprint pipeline outright, confirming our stored quaternion is
    # already in the direction both consumers expect).
    q_scalar_last = att[["q1", "q2", "q3", "q0"]].values.astype(float)

    mission_hk = build_mission_hk(timestamps_unix, pos_km, q_scalar_last)

    # The target marker itself is drawn client-side (a standalone Cesium entity the
    # sidebar moves instantly), so it isn't baked into the CZML here — that would leave
    # a stale marker on screen from the previously loaded CZML whenever the user picks
    # a new AOI/mission without reloading the orbit.
    # CZML pyramid uses body -Z for boresight, but O1A body +Z = nadir.
    # Disable CZML pyramid; the JS viewer draws its own footprint using body +Z.
    czml = generate_czml(
        mission_hk,
        show_axes=show_axes,
        frame="inertial",
    )

    return jsonify({"czml": czml, "time_range": {"start": start, "end": end}, "coord_model": coord_model})


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
    use_old = request.args.get("old") == "1"
    try:
        if use_old:
            missions = mps_db.get_missions(satellite, start, end)
        else:
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
        eoc_correction       "false"면 sensor_calibration.json의 EOC 마운팅 보정을 끄고
                             계산 (검증용 A/B 비교). 생략/그 외 값은 항상 적용(기본 on).
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
    job_id = request.args.get("job_id")
    # 검증용 on/off 스위치 — sensor_calibration.json의 EOC 마운팅 보정을 끄고 계산해서
    # 보정 전/후를 비교해볼 수 있게 함. 기본값 on(생략 시 true).
    eoc_correction = (request.args.get("eoc_correction", "true").lower() != "false")

    if not start or not end or target_lat is None or target_lon is None:
        return jsonify({"lines": [], "error": "start/end/target_lat/target_lon이 필요합니다."}), 400

    # job_id는 파일 경로에 그대로 쓰이므로(진행률 파일명) path traversal 방지용으로
    # 영숫자/-/_ 만 허용 — 프론트가 만드는 값이라 정상적으로는 항상 이 형태.
    if job_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
        job_id = None

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
        sensor_calibration_path=str(SENSOR_CALIBRATION_PATH) if eoc_correction else None,
    )

    # %f(마이크로초) 포함 — pipeline._default_line_step()가 이 문자열을 다시 파싱해
    # 창 길이를 계산하므로, 초 단위로 잘라버리면 line_step 계산이 부정확해진다.
    start_utc = line_start_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    end_utc = line_end_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")

    progress_path = (PROGRESS_DIR / f"{job_id}.json") if job_id else None
    if progress_path:
        PROGRESS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        # line_step is left unset — compute_footprint() auto-scales it to the
        # start_utc~end_utc window (see pipeline._default_line_step) so this safety
        # margin lives with the Java-calling code, not duplicated at every call site.
        result_df = compute_footprint_to_dataframe(
            states, config, start_utc=start_utc, end_utc=end_utc,
            satellite_id=satellite,
            progress_json_path=str(progress_path) if progress_path else None,
        )
    except RuntimeError as exc:
        return jsonify({"lines": [], "error": str(exc)}), 500
    except subprocess.TimeoutExpired:
        return jsonify({"lines": [], "error": "Footprint 계산이 시간 초과됐습니다 (5분). 구간을 좁혀서 다시 시도해주세요."}), 504
    finally:
        # 계산이 끝났으니(성공/실패 무관) 더 이상 폴링할 필요 없는 진행률 파일을 치운다.
        if progress_path:
            progress_path.unlink(missing_ok=True)

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
    response["eoc_correction"] = eoc_correction
    return jsonify(response)


@app.get("/api/footprint/progress")
def api_footprint_progress():
    """/api/footprint/compute가 같은 job_id로 진행 중인 계산의 실제 진행률을 반환.

    서버가 threaded=True라 이 요청은 블로킹 중인 compute 요청과 동시에 처리된다.
    파일이 아직 없으면(계산 시작 전이거나 progress_json_path 없이 호출됐거나) 또는
    이미 끝나서 지워졌으면 done/total 둘 다 0 — 프론트는 이걸 "아직 알 수 없음"으로
    다루고 fake-progress로 폴백한다.
    """
    job_id = request.args.get("job_id")
    if not job_id or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id):
        return jsonify({"done": 0, "total": 0})

    progress_path = PROGRESS_DIR / f"{job_id}.json"
    try:
        with open(progress_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return jsonify({"done": int(data.get("done", 0)), "total": int(data.get("total", 0))})
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        # 파일이 없거나(아직 안 씀/이미 정리됨), Java가 write 중간에 읽혔거나(원자적
        # rename이라 사실상 안 생기지만 방어적으로) — 그냥 "아직 모름"으로 처리.
        return jsonify({"done": 0, "total": 0})


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
