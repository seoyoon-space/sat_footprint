"""core/mission/mce_db.py 검증 - 실제 DB 연결 없이 순수 계산 로직(카메라 ON/OFF
구간 산출, row->dict 변환)만 확인. DB 쿼리 자체(get_missions/_connect)는 실제
MCE DB 접속정보가 있어야 하는 영역이라 여기서 다루지 않는다.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.mission.mce_db import _row_to_mission, compute_camera_window


def test_compute_camera_window_none_when_mission_params_missing():
    assert compute_camera_window(None) == (None, None, None)
    assert compute_camera_window({}) == (None, None, None)


def test_compute_camera_window_none_when_utc_sec_missing():
    params = {"missionSettings": {"startTime": {}, "cameraSettings": {}}}
    assert compute_camera_window(params) == (None, None, None)


def test_compute_camera_window_none_when_utc_sec_is_zero():
    """`if not utc_sec` 체크가 "필드 없음"과 "값이 정확히 0(1970-01-01)"을 구분하지
    못하는 것은 포팅 원본(DEM 서버 attitude-viewer/mce_db.py)의 동작을 그대로 유지한
    것 - 실제 미션의 utcSec은 항상 큰 양수(현재 시각 기준 Unix epoch)라 실질적으로는
    문제가 되지 않는다. 이 동작이 바뀌면 안 되므로 회귀 테스트로 고정."""
    params = {
        "missionSettings": {
            "startTime": {"utcSec": "0", "utcMsec": "0"},
            "cameraSettings": {"camStartMsec": "500", "camDurationUsec": "10000000"},
        }
    }
    assert compute_camera_window(params) == (None, None, None)


def test_compute_camera_window_computes_scan_cam_start_end():
    """camStartMsec=500ms 지연 후 카메라가 켜지고 camDurationUsec=10_000_000(10초)
    뒤에 꺼져야 한다."""
    params = {
        "missionSettings": {
            "startTime": {"utcSec": "1743000000", "utcMsec": "0"},
            "cameraSettings": {"camStartMsec": "500", "camDurationUsec": "10000000"},
        }
    }

    scan_start, cam_start, cam_end = compute_camera_window(params)

    assert scan_start == "2025-03-26T14:40:00.000Z"
    assert cam_start == "2025-03-26T14:40:00.500Z"
    assert cam_end == "2025-03-26T14:40:10.500Z"


def test_compute_camera_window_defaults_camera_duration_when_field_missing():
    """camDurationUsec 필드 자체가 없으면(구형 미션 등) 기본 10초로 취급한다."""
    params = {
        "missionSettings": {
            "startTime": {"utcSec": "1000", "utcMsec": "0"},
            "cameraSettings": {},
        }
    }

    scan_start, cam_start, cam_end = compute_camera_window(params)

    assert scan_start == cam_start  # camStartMsec 없음 -> 지연 0
    start_dt = datetime.fromisoformat(cam_start.replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(cam_end.replace("Z", "+00:00"))
    assert (end_dt - start_dt).total_seconds() == 10.0


def test_row_to_mission_maps_columns_and_computes_camera_window():
    row = {
        "Id": 1,
        "ScheduleId": "O1A_13419_GGD",
        "SatelliteId": "O1A",
        "OperationId": "GGD",
        "LocationName": "GGD",
        "AoiId": None,
        "Latitude": 37.4299,
        "Longitude": 125.2288,
        "EventStart": datetime(2026, 4, 10, 3, 1, 54, 375000),
        "EventEnd": datetime(2026, 4, 10, 3, 1, 57, 250000),
        "EventStartLocal": None,
        "EventEndLocal": None,
        "Duration": 2.875,
        "MaxEl": 0.0,
        "CloudAmount": -1.0,
        "RequestedScanTime": 9.2,
        "Note": None,
        "MissionStatus": "None",
        "ImageStatus": "None",
        "ClientData": None,
        "Results": None,
        "MissionParameterJson": (
            '{"missionSettings": {"startTime": {"utcSec": "1743000000", "utcMsec": "0"}, '
            '"cameraSettings": {"camStartMsec": "0", "camDurationUsec": "10400000"}}}'
        ),
    }

    mission = _row_to_mission(row)

    assert mission["id"] == 1
    assert mission["scheduleId"] == "O1A_13419_GGD"
    assert mission["satelliteId"] == "O1A"
    assert mission["eventStart"] == "2026-04-10T03:01:54.375Z"
    assert mission["scanStart"] == "2025-03-26T14:40:00.000Z"
    assert mission["camEnd"] == "2025-03-26T14:40:10.400Z"
