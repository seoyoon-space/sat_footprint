"""위성별 카메라(EOC) 마운팅 보정값 로더.

footprint-backend(Java)와 같은 data/sensor_calibration.json을 읽어, nominal boresight
(body +Z)를 실측 방향으로 보내는 misalignment unit vector를 제공(docs/fov-eoc-boresight.md).
파일/위성/필드가 없으면 무보정((0,0,0)) - Java와 동일한 fail-safe라 이 파일 없이 옮겨도 안전.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from core.math_utils.quat import Vector3, magnitude

# hk_api/core/geometry/sensor_calibration.py -> repo root(footprint-backend/attitude-viewer와
# 공유하는 data/ 폴더)까지는 parents[3].
_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "sensor_calibration.json"

_cache_lock = threading.Lock()
_cache: dict[str, dict] = {}


def _calibration_path() -> Path:
    override = os.environ.get("SENSOR_CALIBRATION_PATH")
    return Path(override) if override else _DEFAULT_PATH


def _load_calibration(path: Path) -> dict:
    key = str(path)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        _cache[key] = data
        return data


def get_eoc_misalignment_unit_vector(satellite_id: str | None, path: str | Path | None = None) -> Vector3:
    """satellite_id의 EOC 마운팅 보정 unit vector를 반환. 정보가 없으면 (0,0,0)(무보정)."""
    if not satellite_id:
        return (0.0, 0.0, 0.0)

    data = _load_calibration(Path(path) if path else _calibration_path())
    entry = data.get(satellite_id.upper(), {})
    vector = entry.get("eoc_misalignment_unit_vector", [0.0, 0.0, 0.0])

    try:
        vec3: Vector3 = (float(vector[0]), float(vector[1]), float(vector[2]))
    except (TypeError, ValueError, IndexError):
        return (0.0, 0.0, 0.0)

    return vec3 if magnitude(vec3) > 1e-9 else (0.0, 0.0, 0.0)
