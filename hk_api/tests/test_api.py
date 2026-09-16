"""api/ 라우터(FastAPI 엔드포인트) 검증.

실제 DB 연결 없이, HKLoader.load()가 반환하는 DataFrame을 monkeypatch로 대체해
라우팅/인증/스키마 직렬화 로직만 검증한다. 특히 이전에는 TelemetryRecord/czml_routes.py가
core/loader/schema_map.py의 실제 canonical 필드명(qbody_wrt_eci1 등)과 다른 이름
(q_eci2body_1 등)을 쓰고 있어서 위치/자세 데이터가 항상 비어버리는 버그가 있었는데,
여기서 실제 필드명으로 왕복되는지 회귀 테스트로 고정한다.
"""

from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import api.czml_routes as czml_routes
import api.footprint_routes as footprint_routes
import api.mission_routes as mission_routes
import api.routes as routes
import api.validator_routes as validator_routes
import config as config_module
import main

client = TestClient(main.app)


def _fake_hk_dataframe() -> pd.DataFrame:
    times = pd.to_datetime(
        ["2026-08-20T00:00:00Z", "2026-08-20T00:00:01Z", "2026-08-20T00:00:02Z"], utc=True
    )
    return pd.DataFrame(
        {
            "time": times,
            "qbody_wrt_eci1": [1.0, 1.0, 1.0],
            "qbody_wrt_eci2": [0.0, 0.0, 0.0],
            "qbody_wrt_eci3": [0.0, 0.0, 0.0],
            "qbody_wrt_eci4": [0.0, 0.0, 0.0],
            "pos_wrt_eci1": [7000e3, 7001e3, 7002e3],
            "pos_wrt_eci2": [0.0, 0.0, 0.0],
            "pos_wrt_eci3": [0.0, 0.0, 0.0],
            "eigen_err": [5.0, 1.0, 0.05],
            "filt_speed_rpm1": [100.0, 200.0, 5900.0],
        }
    )


class _FakeLoader:
    def load(self, **kwargs):
        return _fake_hk_dataframe()


@pytest.fixture(autouse=True)
def _clear_loader_caches():
    routes._get_loader.cache_clear()
    czml_routes._get_loader.cache_clear()
    validator_routes._get_loader.cache_clear()
    footprint_routes._get_loader.cache_clear()
    yield
    routes._get_loader.cache_clear()
    czml_routes._get_loader.cache_clear()
    validator_routes._get_loader.cache_clear()
    footprint_routes._get_loader.cache_clear()


@pytest.fixture
def fake_loader(monkeypatch):
    monkeypatch.setattr(routes, "_get_loader", lambda satellite_id: _FakeLoader())
    monkeypatch.setattr(czml_routes, "_get_loader", lambda satellite_id: _FakeLoader())
    monkeypatch.setattr(validator_routes, "_get_loader", lambda satellite_id: _FakeLoader())
    monkeypatch.setattr(footprint_routes, "_get_loader", lambda satellite_id: _FakeLoader())


def test_health_check_requires_no_auth():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_mission_hk_matches_dem_field_names_and_positional_convention(fake_loader):
    """/telemetry/mission-hk는 DEM 서버 czml_generator.py의 mission_hk dict와 같은
    camelCase 필드명·배열 형태를 쓴다 - qbody_wrt_eci1(w)이 qbodyWrtEci4(w 자리, DEM
    필드 순서)로, qbody_wrt_eci2/3/4(x,y,z)가 qbodyWrtEci1/2/3으로 재배치된다.
    이 엔드포인트는 loader.load(invert_quaternion_direction=False)로 로드하므로
    이 프로젝트 자신의 Body->ECI 방향반전은 걸리지 않는다 - 여기 fixture는 identity
    쿼터니언(x=y=z=0, w=1)이라 방향과 무관하게 값이 같게 나오고, 방향반전 자체의
    on/off는 tests/test_loader.py의
    test_raw_db_quaternion_becomes_scalar_first_body_to_eci_end_to_end /
    test_reorder_without_invert_matches_dem_expected_direction이 검증한다."""
    resp = client.post(
        "/telemetry/mission-hk",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["taiSeconds"][0] == pytest.approx(1787184000.0)
    assert body["posWrtEci1"] == [7000e3, 7001e3, 7002e3]
    assert body["qbodyWrtEci4"] == [1.0, 1.0, 1.0]  # w
    assert body["qbodyWrtEci1"] == [0.0, 0.0, 0.0]  # x
    assert body["qbodyWrtEci2"] == [0.0, 0.0, 0.0]  # y
    assert body["qbodyWrtEci3"] == [0.0, 0.0, 0.0]  # z


def test_telemetry_query_uses_real_canonical_field_names(fake_loader):
    resp = client.post(
        "/telemetry/query",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["num_records"] == 3
    record = body["records"][0]
    assert record["qbody_wrt_eci1"] == 1.0
    assert record["pos_wrt_eci1"] == 7000e3
    assert record["eigen_err"] == 5.0


class _RecordingLoader:
    """load()에 실제로 전달된 kwargs를 기록 - invert_quaternion_direction이 요청 필드에서
    loader.load() 호출까지 제대로 전달되는지 검증하는 용도(값 자체의 방향반전 로직은
    tests/test_loader.py에서 이미 검증됨)."""

    last_kwargs: dict | None = None

    def load(self, **kwargs):
        _RecordingLoader.last_kwargs = kwargs
        return _fake_hk_dataframe()


def test_telemetry_query_defaults_to_inverted_quaternion(monkeypatch):
    monkeypatch.setattr(routes, "_get_loader", lambda satellite_id: _RecordingLoader())
    resp = client.post(
        "/telemetry/query",
        json={"satellite_id": "O1A", "start_time": "2026-08-20T00:00:00Z", "end_time": "2026-08-20T00:00:02Z"},
    )
    assert resp.status_code == 200
    assert _RecordingLoader.last_kwargs["invert_quaternion_direction"] is True


def test_telemetry_query_can_request_uninverted_quaternion(monkeypatch):
    monkeypatch.setattr(routes, "_get_loader", lambda satellite_id: _RecordingLoader())
    resp = client.post(
        "/telemetry/query",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
            "invert_quaternion_direction": False,
        },
    )
    assert resp.status_code == 200
    assert _RecordingLoader.last_kwargs["invert_quaternion_direction"] is False


def test_telemetry_czml_can_request_uninverted_quaternion(monkeypatch):
    monkeypatch.setattr(czml_routes, "_get_loader", lambda satellite_id: _RecordingLoader())
    resp = client.post(
        "/telemetry/czml",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
            "invert_quaternion_direction": False,
        },
    )
    assert resp.status_code == 200
    assert _RecordingLoader.last_kwargs["invert_quaternion_direction"] is False


def test_telemetry_czml_positions_are_populated_with_real_field_names(fake_loader):
    resp = client.post(
        "/telemetry/czml",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
        },
    )
    assert resp.status_code == 200
    packets = resp.json()
    assert packets[0] == {"id": "document", "version": "1.0"}
    data_packet = packets[1]
    # 이전 버그: position_cols가 실제 DataFrame 컬럼과 안 맞아 'position' 키 자체가 빠졌었음
    assert "position" in data_packet
    assert len(data_packet["position"]["cartesian"]) == 3 * 4  # 3 rows * (t,x,y,z)


def test_telemetry_czml_missing_position_or_attitude_columns_returns_404(monkeypatch):
    """/footprint/*·/telemetry/mission-hk와 같은 기준 - 위치/자세 컬럼이 없으면
    (예: hk2 패킷만 비어있는 경우) 빈 CZML을 200으로 조용히 반환하는 대신 404를 내야
    한다."""
    df = pd.DataFrame({"time": pd.to_datetime(["2026-08-20T00:00:00Z"], utc=True)})

    class _NoAttitudeLoader:
        def load(self, **kwargs):
            return df

    monkeypatch.setattr(czml_routes, "_get_loader", lambda satellite_id: _NoAttitudeLoader())

    resp = client.post(
        "/telemetry/czml",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
        },
    )
    assert resp.status_code == 404


def test_footprint_compute_returns_geojson_feature_collection():
    resp = client.post(
        "/footprint/compute",
        json={
            "pos_eci_x": 7000e3,
            "pos_eci_y": 0.0,
            "pos_eci_z": 0.0,
            "q_w": 1.0,
            "q_x": 0.0,
            "q_y": 0.0,
            "q_z": 0.0,
            "utc_datetime": "2026-08-20T00:00:00Z",
            "fov_x_deg": 10.0,
            "fov_y_deg": 10.0,
            "boresight_x": -1.0,
            "boresight_y": 0.0,
            "boresight_z": 0.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) >= 1


def test_footprint_czml_returns_cesium_loadable_packets():
    resp = client.post(
        "/footprint/czml",
        json={
            "pos_eci_x": 7000e3,
            "pos_eci_y": 0.0,
            "pos_eci_z": 0.0,
            "q_w": 1.0,
            "q_x": 0.0,
            "q_y": 0.0,
            "q_z": 0.0,
            "utc_datetime": "2026-08-20T00:00:00Z",
            "fov_x_deg": 10.0,
            "fov_y_deg": 10.0,
            "boresight_x": -1.0,
            "boresight_y": 0.0,
            "boresight_z": 0.0,
        },
    )
    assert resp.status_code == 200
    packets = resp.json()
    assert packets[0] == {"id": "document", "version": "1.0"}
    polygon_packet = next(p for p in packets if "polygon" in p)
    assert "cartographicDegrees" in polygon_packet["polygon"]["positions"]
    point_packet = next(p for p in packets if "point" in p)
    assert len(point_packet["position"]["cartographicDegrees"]) == 3


def test_footprint_rays_returns_ecef_origin_and_directions_from_real_telemetry(fake_loader):
    """DEM 서버가 자체 지형모델로 교차시킬 수 있도록, 타원체 교차 없이 실측
    텔레메트리 기반 ECEF 광선(원점+방향)만 반환하는지 확인."""
    resp = client.post(
        "/footprint/rays",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
            "fov_x_deg": 10.0,
            "fov_y_deg": 10.0,
            "boresight_x": -1.0,
            "boresight_y": 0.0,
            "boresight_z": 0.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["satellite_id"] == "O1A"
    assert body["num_records"] == 3
    sample = body["samples"][0]
    assert sample["origin_ecef"][0] != 0.0  # 위성 위치가 실측 텔레메트리에서 왔는지
    assert len(sample["boresight_direction_ecef"]) == 3
    assert len(sample["fov_corner_directions_ecef"]) == 4
    # 순수 방향벡터만 반환하고 지표 교차(위경도)는 하지 않아야 함
    assert "center" not in sample and "corners" not in sample


def test_footprint_track_returns_geojson_with_time_tagged_features(fake_loader):
    """실측 텔레메트리 기반 촬영영역 폴리곤(타원체 근사)이 시간 태그와 함께 나오는지 확인."""
    resp = client.post(
        "/footprint/track",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
            "fov_x_deg": 10.0,
            "fov_y_deg": 10.0,
            "boresight_x": -1.0,
            "boresight_y": 0.0,
            "boresight_z": 0.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "FeatureCollection"
    polygon_features = [f for f in body["features"] if f["geometry"]["type"] == "Polygon"]
    point_features = [f for f in body["features"] if f["geometry"]["type"] == "Point"]
    # 3개 샘플 모두 지구를 내려다보는 nadir 근방이라 전부 visible해야 함
    assert len(polygon_features) == 3
    assert len(point_features) == 3
    assert polygon_features[0]["properties"]["time"] == "2026-08-20T00:00:00Z"
    assert polygon_features[1]["properties"]["time"] == "2026-08-20T00:00:01Z"


def test_footprint_track_czml_scopes_each_sample_by_availability(fake_loader):
    resp = client.post(
        "/footprint/track/czml",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
            "fov_x_deg": 10.0,
            "fov_y_deg": 10.0,
            "boresight_x": -1.0,
            "boresight_y": 0.0,
            "boresight_z": 0.0,
        },
    )
    assert resp.status_code == 200
    packets = resp.json()
    assert packets[0] == {"id": "document", "version": "1.0"}
    polygon_ids = [p["id"] for p in packets if "polygon" in p]
    assert polygon_ids == ["footprint_polygon_0", "footprint_polygon_1", "footprint_polygon_2"]
    first_polygon = next(p for p in packets if p["id"] == "footprint_polygon_0")
    assert first_polygon["availability"] == "2026-08-20T00:00:00Z/2026-08-20T00:00:01Z"


def test_line_track_returns_left_right_ground_points_per_sample(fake_loader):
    """푸시브룸 라인(along-track 폭 0)의 좌/우 지상점이 HK 샘플 개수만큼 나오는지 확인."""
    resp = client.post(
        "/footprint/line/track",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
            "fov_across_deg": 1.6,
            "boresight_x": -1.0,
            "boresight_y": 0.0,
            "boresight_z": 0.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["satellite_id"] == "O1A"
    assert body["num_records"] == 3
    for sample in body["samples"]:
        assert sample["visible"] is True
        assert sample["left"] is not None and sample["right"] is not None
        assert sample["left"] != sample["right"]


def test_line_track_geojson_returns_linestring_per_sample(fake_loader):
    resp = client.post(
        "/footprint/line/track/geojson",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
            "fov_across_deg": 1.6,
            "boresight_x": -1.0,
            "boresight_y": 0.0,
            "boresight_z": 0.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["type"] == "FeatureCollection"
    linestrings = [f for f in body["features"] if f["geometry"]["type"] == "LineString"]
    assert len(linestrings) == 3
    assert linestrings[0]["properties"]["time"] == "2026-08-20T00:00:00Z"


def test_line_track_czml_scopes_each_sample_by_availability(fake_loader):
    resp = client.post(
        "/footprint/line/track/czml",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
            "fov_across_deg": 1.6,
            "boresight_x": -1.0,
            "boresight_y": 0.0,
            "boresight_z": 0.0,
        },
    )
    assert resp.status_code == 200
    packets = resp.json()
    assert packets[0] == {"id": "document", "version": "1.0"}
    line_ids = [p["id"] for p in packets if "polyline" in p]
    assert line_ids == ["line_0", "line_1", "line_2"]
    first = next(p for p in packets if p["id"] == "line_0")
    assert first["availability"] == "2026-08-20T00:00:00Z/2026-08-20T00:00:01Z"


# Vallado의 SGP4 검증 표준 예제(satellite 88888) - tests/test_propagation.py와 동일 TLE
PROP_LINE1 = "1 88888U          80275.98708465  .00073094  13844-3  66816-4 0    87"
PROP_LINE2 = "2 88888  72.8435 115.9689 0086731  52.6988 110.5714 16.05824518  1058"
PROP_EPOCH = "1980-10-01T23:41:24.113760Z"


def test_propagation_track_returns_sgp4_position_velocity_samples():
    resp = client.post(
        "/propagation/track",
        json={
            "tle_line1": PROP_LINE1,
            "tle_line2": PROP_LINE2,
            "start_time": PROP_EPOCH,
            "end_time": "1980-10-01T23:51:24.113760Z",
            "step_sec": 300,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["num_records"] == 3
    first = body["samples"][0]
    assert first["time"] == PROP_EPOCH
    assert len(first["position_km"]) == 3 and len(first["velocity_km_s"]) == 3
    # 궤도반경이 물리적으로 타당한 범위인지(이심률 작은 궤도, 반장축 ~6638km 근방)
    r = sum(c**2 for c in first["position_km"]) ** 0.5
    assert 6500 < r < 6800


def test_propagation_track_czml_has_position_only_no_orientation():
    resp = client.post(
        "/propagation/track/czml",
        json={
            "tle_line1": PROP_LINE1,
            "tle_line2": PROP_LINE2,
            "start_time": PROP_EPOCH,
            "end_time": "1980-10-01T23:46:24.113760Z",
            "step_sec": 300,
        },
    )
    assert resp.status_code == 200
    packets = resp.json()
    assert packets[0] == {"id": "document", "version": "1.0"}
    assert "position" in packets[1]
    assert "orientation" not in packets[1]


def test_propagation_track_rejects_invalid_tle():
    resp = client.post(
        "/propagation/track",
        json={
            "tle_line1": "not a valid tle line",
            "tle_line2": PROP_LINE2,
            "start_time": PROP_EPOCH,
            "end_time": "1980-10-01T23:51:24.113760Z",
        },
    )
    assert resp.status_code == 400


def test_propagation_track_rejects_end_before_start():
    resp = client.post(
        "/propagation/track",
        json={
            "tle_line1": PROP_LINE1,
            "tle_line2": PROP_LINE2,
            "start_time": PROP_EPOCH,
            "end_time": "1980-10-01T23:00:00.000000Z",
        },
    )
    assert resp.status_code == 400


def test_propagation_track_rejects_excessive_sample_count():
    resp = client.post(
        "/propagation/track",
        json={
            "tle_line1": PROP_LINE1,
            "tle_line2": PROP_LINE2,
            "start_time": PROP_EPOCH,
            "end_time": "1980-10-02T23:41:24.113760Z",  # 24시간
            "step_sec": 1,  # 1초 간격 -> 86400개, 한도 초과
        },
    )
    assert resp.status_code == 400


def test_ops_status_evaluates_settling_and_saturation(fake_loader):
    resp = client.post(
        "/validator/ops-status",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
            "settling_tolerance_deg": 0.1,
            "settling_hold_duration_sec": 1.0,
            "wheel_max_rpm": 6000.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["satellite_id"] == "O1A"
    assert body["settling"]["settled"] is True
    assert body["wheel_saturation"]["status"] == "WARN"  # 5900/6000 ratio ~0.983 >= warn_ratio 0.9


def test_ops_status_settling_ignores_nan_eigen_err_samples(monkeypatch):
    """abs(NaN) > tolerance는 항상 False이므로, NaN(결측) 샘플을 걸러내지 않으면
    '허용오차 이내'로 오판돼 거짓 PASS/settled가 나올 수 있었던 버그 - eigen_err가
    NaN인 시점은 평가 대상에서 제외해야 한다."""
    times = pd.to_datetime(
        ["2026-08-20T00:00:00Z", "2026-08-20T00:00:01Z", "2026-08-20T00:00:02Z"], utc=True
    )
    df = pd.DataFrame({"time": times, "eigen_err": [10.0, float("nan"), float("nan")]})

    class _NanLoader:
        def load(self, **kwargs):
            return df

    monkeypatch.setattr(validator_routes, "_get_loader", lambda satellite_id: _NanLoader())

    resp = client.post(
        "/validator/ops-status",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
            "settling_tolerance_deg": 0.5,
            "settling_hold_duration_sec": 0.0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # 고쳐지기 전엔 NaN 두 번째 샘플(index 1)이 "허용오차 이내"로 오판돼
    # settled=True/PASS가 나왔다 - NaN을 걸러내면 유효 샘플은 10.0(허용오차 밖) 하나뿐이라
    # 미정착(FAIL)이어야 한다.
    assert body["settling"]["settled"] is False
    assert body["settling"]["status"] == "FAIL"


def test_ops_status_rejects_non_positive_wheel_max_rpm(fake_loader):
    """wheel_max_rpm<=0은 core/validator/ops_rules.py::scan_wheel_saturation가
    ValueError를 던지는데, 이 호출은 try/except로 감싸여 있지 않아 그대로 500으로
    새던 버그 - 다른 FOV 필드들처럼 Field(gt=0)으로 요청 단계에서 걸러낸다."""
    resp = client.post(
        "/validator/ops-status",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
            "wheel_max_rpm": 0,
        },
    )
    assert resp.status_code == 422


def test_ops_status_without_any_evaluation_criteria_is_pass(fake_loader):
    resp = client.post(
        "/validator/ops-status",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "PASS"
    assert body["settling"] is None
    assert body["wheel_saturation"] is None


def test_telemetry_query_unknown_satellite_returns_400():
    resp = client.post(
        "/telemetry/query",
        json={
            "satellite_id": "DOES_NOT_EXIST",
            "start_time": "2026-08-20T00:00:00Z",
            "end_time": "2026-08-20T00:00:02Z",
        },
    )
    # config/satellites.toml이 없거나 satellite_id가 등록되지 않은 경우 -> 400/500 중 하나로
    # 명확한 HTTP 에러가 나야 하며, 500 Internal Server Error(트레이스백 노출)로 새면 안 됨.
    assert resp.status_code in (400, 500)
    assert "detail" in resp.json()


def test_api_key_required_when_configured(fake_loader, monkeypatch):
    monkeypatch.setattr(config_module.settings, "api_key", "secret123")

    resp_no_key = client.post(
        "/telemetry/query",
        json={"satellite_id": "O1A", "start_time": "2026-08-20T00:00:00Z", "end_time": "2026-08-20T00:00:02Z"},
    )
    assert resp_no_key.status_code == 401

    resp_wrong_key = client.post(
        "/telemetry/query",
        json={"satellite_id": "O1A", "start_time": "2026-08-20T00:00:00Z", "end_time": "2026-08-20T00:00:02Z"},
        headers={"X-API-Key": "wrong"},
    )
    assert resp_wrong_key.status_code == 401

    resp_ok = client.post(
        "/telemetry/query",
        json={"satellite_id": "O1A", "start_time": "2026-08-20T00:00:00Z", "end_time": "2026-08-20T00:00:02Z"},
        headers={"X-API-Key": "secret123"},
    )
    assert resp_ok.status_code == 200


def test_api_key_not_required_when_unset(fake_loader):
    assert config_module.settings.api_key is None
    resp = client.post(
        "/telemetry/query",
        json={"satellite_id": "O1A", "start_time": "2026-08-20T00:00:00Z", "end_time": "2026-08-20T00:00:02Z"},
    )
    assert resp.status_code == 200


def test_health_check_bypasses_api_key_even_when_configured(monkeypatch):
    monkeypatch.setattr(config_module.settings, "api_key", "secret123")
    resp = client.get("/health")
    assert resp.status_code == 200


def test_cors_allowed_origins_list_parses_comma_separated_string():
    assert config_module.Settings(cors_allowed_origins="").cors_allowed_origins_list == []
    assert config_module.Settings(
        cors_allowed_origins="https://a.example.com, https://b.example.com"
    ).cors_allowed_origins_list == ["https://a.example.com", "https://b.example.com"]


def test_default_cors_config_blocks_cross_origin_browser_requests():
    """CORS_ALLOWED_ORIGINS 미설정(기본값)이면 CORSMiddleware가 붙어 있어도 origin 목록이
    비어 있어 어떤 브라우저 cross-origin 요청도 허용되지 않아야 한다(서버-서버 호출은
    Origin 헤더 자체가 없으므로 영향받지 않음 - 여기서 검증하는 건 브라우저 preflight만).
    """
    resp = client.options(
        "/health", headers={"Origin": "https://example.com", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in resp.headers


def test_cors_middleware_allows_configured_origin_and_rejects_others():
    """main.py와 동일한 방식(app.add_middleware(CORSMiddleware, allow_origins=...))으로
    구성했을 때, 설정한 origin은 허용하고 그 외는 차단하는지 확인."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.cors import CORSMiddleware

    test_app = FastAPI()
    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=config_module.Settings(cors_allowed_origins="https://dem.example.com").cors_allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @test_app.get("/ping")
    def ping():
        return {"ok": True}

    test_client = TestClient(test_app)

    allowed = test_client.options(
        "/ping", headers={"Origin": "https://dem.example.com", "Access-Control-Request-Method": "GET"}
    )
    assert allowed.headers.get("access-control-allow-origin") == "https://dem.example.com"

    blocked = test_client.options(
        "/ping", headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in blocked.headers


def test_routers_share_a_single_loader_cache_per_satellite():
    """routes.py/czml_routes.py/validator_routes.py/footprint_routes.py가 각자 별도
    @lru_cache를 두면 같은 satellite_id에 대해 SQLAlchemy Engine(DB 커넥션 풀)이
    라우터별로 중복 생성된다. api/loader_cache.py로 통합한 뒤에는 네 라우터가 동일한
    캐시 함수 객체를 공유해야 한다.
    """
    assert routes._get_loader is czml_routes._get_loader
    assert routes._get_loader is validator_routes._get_loader
    assert routes._get_loader is footprint_routes._get_loader


def test_mission_schedule_returns_missions_with_camera_window(monkeypatch):
    """/mission/schedule은 core/mission/mce_db.py::get_missions()를 그대로 호출해
    JSON으로 감싸 반환한다 - HK DB와 무관한 별개 DB이므로 fake_loader가 아니라
    get_missions 자체를 monkeypatch한다."""

    def _fake_get_missions(satellite_id, start_iso, end_iso):
        assert satellite_id == "O1A"
        return [
            {
                "id": 1,
                "scheduleId": "O1A_13419_GGD",
                "satelliteId": "O1A",
                "eventStart": "2026-04-10T03:01:54.375Z",
                "eventEnd": "2026-04-10T03:01:57.250Z",
                "scanStart": "2026-04-10T03:01:54.375Z",
                "camStart": "2026-04-10T03:01:54.375Z",
                "camEnd": "2026-04-10T03:02:04.775Z",
            }
        ]

    monkeypatch.setattr(mission_routes, "get_missions", _fake_get_missions)

    resp = client.post(
        "/mission/schedule",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-04-01T00:00:00Z",
            "end_time": "2026-04-30T23:59:59Z",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["satellite_id"] == "O1A"
    assert body["num_records"] == 1
    assert body["missions"][0]["scheduleId"] == "O1A_13419_GGD"
    assert body["missions"][0]["camStart"] == "2026-04-10T03:01:54.375Z"


def test_mission_schedule_surfaces_db_config_error_as_500(monkeypatch):
    def _raise_config_error(satellite_id, start_iso, end_iso):
        raise ValueError("MCE DB env is incomplete.")

    monkeypatch.setattr(mission_routes, "get_missions", _raise_config_error)

    resp = client.post(
        "/mission/schedule",
        json={
            "satellite_id": "O1A",
            "start_time": "2026-04-01T00:00:00Z",
            "end_time": "2026-04-30T23:59:59Z",
        },
    )
    assert resp.status_code == 500
    assert "MCE DB env is incomplete" in resp.json()["detail"]
