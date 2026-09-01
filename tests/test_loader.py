"""
실제 DB 연결 없이 core/loader/time_sync.py의 병합/슬라이싱 로직만 검증.
(DB 연동 자체는 실제 스키마 확정 후 integration test로 별도 작성 예정)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.coordinates import (
    build_cesium_track_czml,
    eci_to_ecef,
    earth_rotation_angle_rad,
    pointing_vector_from_quaternion,
    quaternion_multiply,
    quaternion_normalize,
    rotate_vector_by_quaternion,
)
from core.loader.hk_loader import (
    _build_tolerance_overrides,
    _normalize_query_time,
    _write_csv_output,
    _write_text_output,
    df_to_czml,
    extract_attitude_columns,
    _write_czml_output,
)
from core.loader.schema_map import HK_PACKET_SCHEMA, PacketSpec
from core.loader.time_sync import merge_packets, slice_time_range


def _ts(seconds: list[float]) -> pd.Series:
    base = pd.Timestamp("2026-08-01T00:00:00Z")
    return pd.to_datetime([base + pd.Timedelta(seconds=s) for s in seconds], utc=True)


def _reference_quaternion_multiply(q1, q2):
    w1, x1, y1, z1 = np.asarray(q1, dtype=float)
    w2, x2, y2, z2 = np.asarray(q2, dtype=float)
    return np.array(
        [
            w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
            w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
            w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
            w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        ],
        dtype=float,
    )


def _reference_rotate_vector(vec, quat):
    q = np.asarray(quat, dtype=float)
    qn = q / np.linalg.norm(q)
    v = np.asarray(vec, dtype=float)
    qv = np.array([0.0, v[0], v[1], v[2]], dtype=float)
    q_conj = np.array([qn[0], -qn[1], -qn[2], -qn[3]], dtype=float)
    p = _reference_quaternion_multiply(qn, qv)
    p = _reference_quaternion_multiply(p, q_conj)
    return p[1:]


def test_merge_packets_basic_alignment():
    hk1 = pd.DataFrame(
        {
            "time": _ts([0, 1, 2, 3]),
            "q_eci2body_1": [0.1, 0.2, 0.3, 0.4],
        }
    )
    hk2 = pd.DataFrame(
        {
            "time": _ts([0.1, 1.1, 2.1, 3.1]),
            "body_rate_x": [1.0, 2.0, 3.0, 4.0],
        }
    )

    merged = merge_packets(
        {"hk1": hk1, "hk2": hk2},
        master_key="hk1",
        tolerance_sec=1.0,
        interpolate_gaps=False,
    )

    assert len(merged) == 4
    assert list(merged["body_rate_x"]) == [1.0, 2.0, 3.0, 4.0]


def test_merge_packets_out_of_tolerance_is_nan():
    hk1 = pd.DataFrame({"time": _ts([0, 10]), "q_eci2body_1": [0.1, 0.2]})
    hk2 = pd.DataFrame({"time": _ts([0, 100]), "body_rate_x": [1.0, 2.0]})

    merged = merge_packets(
        {"hk1": hk1, "hk2": hk2},
        master_key="hk1",
        tolerance_sec=1.0,
        interpolate_gaps=False,
    )

    assert merged.loc[1, "body_rate_x"] != merged.loc[1, "body_rate_x"]  # NaN check


def test_slice_time_range():
    df = pd.DataFrame({"time": _ts([0, 1, 2, 3, 4]), "value": range(5)})
    sliced = slice_time_range(df, "2026-08-01T00:00:01Z", "2026-08-01T00:00:03Z")
    assert list(sliced["value"]) == [1, 2, 3]


def test_merge_packets_accepts_epoch_seconds_time_columns():
    hk1 = pd.DataFrame({"time": [1787151600, 1787151601], "q_eci2body_1": [0.1, 0.2]})
    hk2 = pd.DataFrame({"time": [1787151600, 1787151602], "body_rate_x": [1.0, 2.0]})

    merged = merge_packets({"hk1": hk1, "hk2": hk2}, master_key="hk1", tolerance_sec=2.0, interpolate_gaps=False)

    assert merged["time"].dtype.kind == "M"
    assert list(merged["body_rate_x"])[:2] == [1.0, 1.0]


def test_merge_packets_resolves_duplicate_field_names():
    hk1 = pd.DataFrame({"time": [1787151600, 1787151601], "hk_file_name": ["a", "b"]})
    hk2 = pd.DataFrame({"time": [1787151600, 1787151602], "hk_file_name": ["c", "d"]})

    merged = merge_packets({"hk1": hk1, "hk2": hk2}, master_key="hk1", tolerance_sec=2.0, interpolate_gaps=False)

    assert "hk_file_name" in merged.columns
    assert "hk_file_name_hk2" in merged.columns


def test_merge_packets_skips_object_columns_during_interpolation():
    hk1 = pd.DataFrame({"time": [1787151600, 1787151601, 1787151602], "value": [1.0, None, 3.0], "hk_file_name": ["a", "b", "c"]})
    hk2 = pd.DataFrame({"time": [1787151600, 1787151602], "other": [10.0, 30.0], "hk_file_name": ["d", "e"]})

    merged = merge_packets({"hk1": hk1, "hk2": hk2}, master_key="hk1", tolerance_sec=2.0, interpolate_gaps=True)

    assert "value" in merged.columns
    assert "hk_file_name_hk2" in merged.columns
    assert merged["value"].notna().all()


def test_merge_packets_max_interp_gap_sec_blocks_large_gaps():
    """time_sync.py의 max_interp_gap_sec: 이 값보다 큰 결측 구간은 보간하지 않고 NaN 유지."""
    # 0초와 100초 지점만 실측값이 있고 그 사이(20/40/60/80초)는 전부 결측인 100초짜리 큰 갭
    hk1 = pd.DataFrame(
        {
            "time": _ts([0, 20, 40, 60, 80, 100]),
            "value": [1.0, None, None, None, None, 7.0],
        }
    )

    # 갭(100초)이 max_interp_gap_sec(10초)보다 크므로 중간 지점은 보간되지 않고 NaN으로 남아야 함
    merged_blocked = merge_packets(
        {"hk1": hk1}, master_key="hk1", tolerance_sec=1.0, interpolate_gaps=True, max_interp_gap_sec=10.0
    )
    assert merged_blocked["value"].isna().sum() == 4
    assert merged_blocked["value"].iloc[[0, 5]].notna().all()

    # max_interp_gap_sec을 갭보다 크게 주면 정상적으로 시간 기반 선형보간
    merged_allowed = merge_packets(
        {"hk1": hk1}, master_key="hk1", tolerance_sec=1.0, interpolate_gaps=True, max_interp_gap_sec=200.0
    )
    assert merged_allowed["value"].notna().all()
    np.testing.assert_allclose(merged_allowed["value"].to_numpy(), [1.0, 2.2, 3.4, 4.6, 5.8, 7.0])

    # max_interp_gap_sec=None이면 갭 크기와 무관하게 항상 보간(기존 동작과 동일)
    merged_unlimited = merge_packets(
        {"hk1": hk1}, master_key="hk1", tolerance_sec=1.0, interpolate_gaps=True, max_interp_gap_sec=None
    )
    assert merged_unlimited["value"].notna().all()
    np.testing.assert_allclose(merged_unlimited["value"].to_numpy(), [1.0, 2.2, 3.4, 4.6, 5.8, 7.0])


def test_merge_packets_tolerance_overrides_widens_slow_packet_matching():
    """time_sync.py의 tolerance_overrides: 패킷별로 asof-merge 허용 오차를 다르게 줄 수 있어야 함."""
    hk1 = pd.DataFrame({"time": _ts([0, 2, 4, 6, 8, 10]), "value": [1.0] * 6})
    # hk2는 10초 주기로만 송신(0초, 10초 두 샘플뿐)하는 느린 패킷을 흉내
    hk2 = pd.DataFrame({"time": _ts([0, 10]), "slow_value": [100.0, 200.0]})

    # 전역 tolerance_sec=1.0으로는 중간 지점들이 전부 매칭 실패 -> NaN
    merged_default = merge_packets(
        {"hk1": hk1, "hk2": hk2}, master_key="hk1", tolerance_sec=1.0, interpolate_gaps=False
    )
    assert merged_default["slow_value"].isna().sum() == 4

    # hk2에만 5초 허용 오차를 override하면 모든 마스터 타임스탬프가 가장 가까운
    # hk2 샘플과 매칭됨(전역 tolerance_sec은 override 없는 패킷에 그대로 유지)
    merged_overridden = merge_packets(
        {"hk1": hk1, "hk2": hk2},
        master_key="hk1",
        tolerance_sec=1.0,
        interpolate_gaps=False,
        tolerance_overrides={"hk2": 5.0},
    )
    assert merged_overridden["slow_value"].notna().all()
    np.testing.assert_allclose(
        merged_overridden["slow_value"].to_numpy(), [100.0, 100.0, 100.0, 200.0, 200.0, 200.0]
    )


def test_build_tolerance_overrides_uses_rate_hz_with_merge_tolerance_as_floor(monkeypatch):
    """hk_loader._build_tolerance_overrides: PacketSpec.rate_hz -> 패킷별 허용 오차(초).

    느린 패킷(0.1Hz = 10초 주기)은 절반 주기(5초)로 넓어지고, 빠른 패킷(10Hz)은
    자연 허용치(0.05초)가 사용자 지정 merge_tolerance_sec보다 좁으므로
    merge_tolerance_sec 그대로 유지(하한으로 동작).
    """
    fake_schema = {
        "slow": PacketSpec(table="t_slow", time_col="timeUtc", fields={}, rate_hz=0.1),
        "fast": PacketSpec(table="t_fast", time_col="timeUtc", fields={}, rate_hz=10.0),
    }
    monkeypatch.setattr("core.loader.hk_loader.HK_PACKET_SCHEMA", fake_schema)

    overrides = _build_tolerance_overrides(["slow", "fast"], merge_tolerance_sec=1.0)

    assert overrides["slow"] == pytest.approx(5.0)
    assert overrides["fast"] == pytest.approx(1.0)


def test_real_o1b_hk_schema_contains_expected_columns():
    assert HK_PACKET_SCHEMA["hk1"].time_col == "timeUtc"
    assert "sel_s_bband" in HK_PACKET_SCHEMA["hk1"].fields
    assert "qbody_wrt_eci1" in HK_PACKET_SCHEMA["hk2"].fields
    assert "startup_cause1" in HK_PACKET_SCHEMA["hk3"].fields
    assert "cmd_stat" in HK_PACKET_SCHEMA["hk4"].fields
    assert "soh_sess_stat" in HK_PACKET_SCHEMA["hk5"].fields
    assert "l0_adc_word1_ch0" in HK_PACKET_SCHEMA["hk6"].fields


def test_normalize_query_time_uses_kst_date_input():
    assert _normalize_query_time("2026-08-20", is_end=False) == 1787151600
    assert _normalize_query_time("2026-08-20", is_end=True) == 1787237999


def test_write_text_output_creates_txt_file(tmp_path):
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-08-20T00:00:00Z", "2026-08-20T00:00:01Z"], utc=True),
            "q_eci2body_1": [0.1, 0.2],
        }
    )
    out_path = tmp_path / "hk_output.txt"

    _write_text_output(str(out_path), df=df, max_rows=0)

    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "Loaded HK DataFrame: 2 rows x 2 columns" in content
    assert "q_eci2body_1" in content


def test_write_csv_output_creates_csv_file(tmp_path):
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-08-20T00:00:00Z", "2026-08-20T00:00:01Z"], utc=True),
            "q_eci2body_1": [0.1, 0.2],
        }
    )
    out_path = tmp_path / "hk_output.csv"

    _write_csv_output(str(out_path), df=df)

    assert out_path.exists()
    content = out_path.read_text(encoding="utf-8")
    assert "time" in content
    assert "q_eci2body_1" in content
    assert "0.1" in content


def test_df_to_czml_basic(tmp_path):
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-08-20T00:00:00Z", "2026-08-20T00:00:01Z"], utc=True),
            "value": [1.23, None],
            "label": ["A", "B"],
        }
    )
    czml = df_to_czml(df, id_prefix="test")
    assert isinstance(czml, list)
    assert czml[0]["id"] == "document"
    assert czml[1]["id"] == "test_0"
    assert "time" in czml[1]
    assert czml[1]["value"] == 1.23
    assert czml[2]["value"] is None
    # write to file
    out = tmp_path / "out.czml"
    _write_czml_output(str(out), df=df, id_prefix="test")
    assert out.exists()
    txt = out.read_text(encoding="utf-8")
    assert "document" in txt
    assert "test_0" in txt


def test_coordinate_utilities_and_quaternion_rotation():
    q_identity = (1.0, 0.0, 0.0, 0.0)
    assert quaternion_normalize(q_identity) == (1.0, 0.0, 0.0, 0.0)

    rotated = rotate_vector_by_quaternion((1.0, 0.0, 0.0), q_identity)
    assert rotated[0] > 0.999
    assert abs(rotated[1]) < 1e-12
    assert abs(rotated[2]) < 1e-12

    pointing = pointing_vector_from_quaternion(q_identity)
    assert abs(pointing[0] - 1.0) < 1e-9

    q = quaternion_multiply(q_identity, q_identity)
    assert q == (1.0, 0.0, 0.0, 0.0)

    angle = earth_rotation_angle_rad(pd.Timestamp("2026-08-20T00:00:00Z").to_pydatetime())
    assert angle > 0.0

    ecef = eci_to_ecef((1.0, 0.0, 0.0), pd.Timestamp("2026-08-20T00:00:00Z").to_pydatetime())
    assert len(ecef) == 3


def test_custom_math_matches_numpy_reference():
    q1 = (0.7071067811865476, 0.0, 0.7071067811865476, 0.0)
    q2 = (0.7071067811865476, 0.0, 0.0, 0.7071067811865476)
    vec = (1.0, 0.0, 0.0)

    ref_q = _reference_quaternion_multiply(q1, q2)
    custom_q = quaternion_multiply(q1, q2)
    np.testing.assert_allclose(custom_q, ref_q, rtol=1e-9, atol=1e-9)

    ref_rot = _reference_rotate_vector(vec, q1)
    custom_rot = rotate_vector_by_quaternion(vec, q1)
    np.testing.assert_allclose(custom_rot, ref_rot, rtol=1e-9, atol=1e-9)

    # eci_to_ecef는 이제 세차+장동+겉보기항성시를 적용한 회전 합성이라 단순 Z축 회전과
    # 더 이상 같지 않다(core/coordinates.py의 세차/장동 근사 참고). 여기서는 numpy로
    # "순수 회전 합성이면 노름이 보존되어야 한다"는 성질만 교차검증한다.
    dt = pd.Timestamp("2026-08-20T00:00:00Z").to_pydatetime()
    v_eci = (7000e3, 1234e3, -555e3)
    custom_ecef = np.asarray(eci_to_ecef(v_eci, dt), dtype=float)
    np.testing.assert_allclose(np.linalg.norm(custom_ecef), np.linalg.norm(v_eci), rtol=1e-12)


def test_extract_attitude_columns_creates_standardized_output():
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-08-20T00:00:00Z", "2026-08-20T00:00:01Z"], utc=True),
            "pos_eci_x": [1.0, 2.0],
            "pos_eci_y": [3.0, 4.0],
            "pos_eci_z": [5.0, 6.0],
            "vel_eci_x": [0.1, 0.2],
            "vel_eci_y": [0.3, 0.4],
            "vel_eci_z": [0.5, 0.6],
            "q_eci2body_1": [0.0, 0.0],
            "q_eci2body_2": [0.0, 0.0],
            "q_eci2body_3": [0.0, 0.0],
            "q_eci2body_4": [1.0, 1.0],
        }
    )

    out = extract_attitude_columns(df)

    assert list(out.columns) == ["timestamp", "px", "py", "pz", "vx", "vy", "vz", "q0", "q1", "q2", "q3"]
    assert out.iloc[0]["q0"] == 0.0
    assert out.iloc[0]["q3"] == 1.0
    assert out.iloc[1]["px"] == 2.0


def test_build_cesium_track_czml():
    df = pd.DataFrame(
        {
            "time": pd.to_datetime(["2026-08-20T00:00:00Z", "2026-08-20T00:00:01Z"], utc=True),
            "pos_eci_x": [1.0, 2.0],
            "pos_eci_y": [0.0, 0.0],
            "pos_eci_z": [0.0, 0.0],
            "q_eci2body_1": [0.0, 0.0],
            "q_eci2body_2": [0.0, 0.0],
            "q_eci2body_3": [0.0, 0.0],
            "q_eci2body_4": [1.0, 1.0],
            "pointing_eci": [[1.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        }
    )
    czml = build_cesium_track_czml(df, id_prefix="sat")
    assert czml[0]["id"] == "document"
    assert czml[1]["id"] == "sat"
    assert "position" in czml[1]
    assert "orientation" in czml[1]
    assert czml[1]["position"]["epoch"] == "2026-08-20T00:00:00Z"
    assert len(czml[1]["position"]["cartesian"]) >= 8
