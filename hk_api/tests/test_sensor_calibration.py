"""core/geometry/sensor_calibration.py 검증 - EOC 마운팅 보정값 JSON 로더."""
from __future__ import annotations

import json

import pytest

from core.geometry import sensor_calibration


@pytest.fixture(autouse=True)
def _clear_cache():
    """_load_calibration은 경로별로 캐시하므로, 매 테스트가 서로 다른 tmp_path를
    쓰더라도 캐시 딕셔너리 자체는 프로세스 전역이라 테스트 간 누적된다. 안전하게 매번 비운다."""
    sensor_calibration._cache.clear()
    yield
    sensor_calibration._cache.clear()


def _write_calibration(tmp_path, data: dict):
    path = tmp_path / "sensor_calibration.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_returns_zero_vector_when_satellite_id_is_none(tmp_path):
    path = _write_calibration(tmp_path, {"O1B": {"eoc_misalignment_unit_vector": [0.0, 0.0, 1.0]}})
    assert sensor_calibration.get_eoc_misalignment_unit_vector(None, path=path) == (0.0, 0.0, 0.0)


def test_returns_zero_vector_when_file_missing(tmp_path):
    missing_path = tmp_path / "does_not_exist.json"
    assert sensor_calibration.get_eoc_misalignment_unit_vector("O1B", path=missing_path) == (0.0, 0.0, 0.0)


def test_returns_zero_vector_when_satellite_entry_missing(tmp_path):
    path = _write_calibration(tmp_path, {"O1B": {"eoc_misalignment_unit_vector": [0.0, 0.0, 1.0]}})
    assert sensor_calibration.get_eoc_misalignment_unit_vector("O1A", path=path) == (0.0, 0.0, 0.0)


def test_returns_zero_vector_when_stored_vector_is_zero():
    # O1A 실제 sensor_calibration.json 값 - 아직 보정 없음
    path = None  # 기본 경로(repo data/sensor_calibration.json) 사용
    vector = sensor_calibration.get_eoc_misalignment_unit_vector("O1A", path=path)
    assert vector == (0.0, 0.0, 0.0)


def test_returns_real_vector_for_calibrated_satellite():
    # 실제 저장된 O1B 보정값 - docs/fov-eoc-boresight.md와 동일
    vector = sensor_calibration.get_eoc_misalignment_unit_vector("O1B")
    assert vector == pytest.approx((0.012514, -0.003196, 0.999917))


def test_satellite_id_lookup_is_case_insensitive(tmp_path):
    path = _write_calibration(tmp_path, {"O1B": {"eoc_misalignment_unit_vector": [0.1, 0.0, 0.994987]}})
    assert sensor_calibration.get_eoc_misalignment_unit_vector("o1b", path=path) == pytest.approx((0.1, 0.0, 0.994987))


def test_malformed_vector_falls_back_to_zero(tmp_path):
    path = _write_calibration(tmp_path, {"O1B": {"eoc_misalignment_unit_vector": [1.0]}})
    assert sensor_calibration.get_eoc_misalignment_unit_vector("O1B", path=path) == (0.0, 0.0, 0.0)
