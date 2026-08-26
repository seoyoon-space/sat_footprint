"""core/coordinates.py, core/math_utils/quat.py 검증.

이 계산 코드는 numpy/scipy 없이 표준 라이브러리(math)만으로 구현하는 것이 원칙(requirements.txt 참고). 
여기서는 numpy/scipy를 q비교군으로 사용. 
직접 구현한 stdlib 코드의 결과가 external library results와 일치하는지 검증.
numpy/scipy는 테스트 전용이며 런타임(런타임 requirements)에는 미포함.
"""
from __future__ import annotations

import math
import random

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from core.coordinates import (
    WGS84_A,
    WGS84_B,
    ecef_to_geodetic,
    eci_to_ecef,
    geodetic_to_ecef,
    quaternion_multiply,
    quaternion_to_cesium_unit_quaternion,
    rotate_vector_by_quaternion,
)
from core.math_utils.quat import (
    cross,
    dot,
    magnitude,
    normalize,
    quat_to_scalar_last,
    quaternion_normalize,
    rotate_vector_axis_angle,
)


def _random_unit_quaternion(rng: random.Random) -> tuple[float, float, float, float]:
    q = (rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1))
    return quaternion_normalize(q)


def test_vector_ops_match_numpy():
    rng = random.Random(42)
    for _ in range(20):
        a = (rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(-10, 10))
        b = (rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(-10, 10))

        np.testing.assert_allclose(dot(a, b), np.dot(a, b), rtol=1e-12)
        np.testing.assert_allclose(cross(a, b), np.cross(a, b), rtol=1e-12)
        np.testing.assert_allclose(magnitude(a), np.linalg.norm(a), rtol=1e-12)
        np.testing.assert_allclose(normalize(a), np.asarray(a) / np.linalg.norm(a), rtol=1e-12)


def test_optimized_rotate_vector_matches_scipy_rotation():
    """quat.py의 이중 외적 최적화 회전식이 scipy.spatial.transform.Rotation과 일치하는지 확인."""
    rng = random.Random(7)
    for _ in range(30):
        q = _random_unit_quaternion(rng)  # scalar-first (w, x, y, z)
        v = (rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(-5, 5))

        custom = rotate_vector_by_quaternion(v, q)

        scipy_rot = Rotation.from_quat(quat_to_scalar_last(q))  # scalar-last [x,y,z,w]
        reference = scipy_rot.apply(np.asarray(v))

        np.testing.assert_allclose(custom, reference, rtol=1e-9, atol=1e-9)


def test_rotate_vector_axis_angle_matches_scipy_rotvec():
    rng = random.Random(11)
    for _ in range(20):
        axis = normalize((rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)))
        angle = rng.uniform(-math.pi, math.pi)
        v = (rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(-5, 5))

        custom = rotate_vector_axis_angle(v, axis, angle)

        rotvec = np.asarray(axis) * angle
        reference = Rotation.from_rotvec(rotvec).apply(np.asarray(v))

        np.testing.assert_allclose(custom, reference, rtol=1e-9, atol=1e-9)


def test_quaternion_multiply_matches_scipy_composition():
    rng = random.Random(3)
    for _ in range(20):
        q1 = _random_unit_quaternion(rng)
        q2 = _random_unit_quaternion(rng)

        custom = quaternion_multiply(q1, q2)

        r1 = Rotation.from_quat(quat_to_scalar_last(q1))
        r2 = Rotation.from_quat(quat_to_scalar_last(q2))
        combined = (r1 * r2).as_quat()  # scalar-last
        reference_scalar_first = (combined[3], combined[0], combined[1], combined[2])

        # 쿼터니언은 q와 -q가 같은 회전을 나타내므로 부호까지 비교하려면 방향을 맞춰야함. 
        if np.dot(custom, reference_scalar_first) < 0:
            reference_scalar_first = tuple(-c for c in reference_scalar_first)

        np.testing.assert_allclose(custom, reference_scalar_first, rtol=1e-9, atol=1e-9)


def test_quaternion_to_cesium_unit_quaternion_is_scalar_last():
    q = (0.9238795325112867, 0.38268343236508984, 0.0, 0.0)  # 45deg about X
    cesium_q = quaternion_to_cesium_unit_quaternion(q)
    assert cesium_q == (q[1], q[2], q[3], q[0])


def test_eci_to_ecef_matches_manual_z_rotation():
    dt = __import__("datetime").datetime(2026, 8, 20, 0, 0, 0, tzinfo=__import__("datetime").timezone.utc)
    from core.coordinates import earth_rotation_angle_rad

    angle = earth_rotation_angle_rad(dt)
    v_eci = (7000e3, 0.0, 0.0)

    custom = eci_to_ecef(v_eci, dt)
    reference = (
        v_eci[0] * math.cos(angle) - v_eci[1] * math.sin(angle),
        v_eci[0] * math.sin(angle) + v_eci[1] * math.cos(angle),
        v_eci[2],
    )
    np.testing.assert_allclose(custom, reference, rtol=1e-12)


# WGS-84 geodetic <-> ECEF

def _numpy_geodetic_to_ecef(lat_deg, lon_deg, alt_m):
    lat = np.radians(lat_deg)
    lon = np.radians(lon_deg)
    e2 = 1.0 - (WGS84_B / WGS84_A) ** 2
    n = WGS84_A / np.sqrt(1.0 - e2 * np.sin(lat) ** 2)
    x = (n + alt_m) * np.cos(lat) * np.cos(lon)
    y = (n + alt_m) * np.cos(lat) * np.sin(lon)
    z = (n * (1.0 - e2) + alt_m) * np.sin(lat)
    return np.array([x, y, z])


def _numpy_bowring_closed_form(vec_ecef):
    """iterative refinement 없는 Bowring 단발 공식 (독립적인 교차검증용 구현)."""
    x, y, z = vec_ecef
    a, b = WGS84_A, WGS84_B
    e2 = 1.0 - (b / a) ** 2
    ep2 = (a * a - b * b) / (b * b)

    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    theta = math.atan2(z * a, p * b)

    lat = math.atan2(z + ep2 * b * math.sin(theta) ** 3, p - e2 * a * math.cos(theta) ** 3)
    n = a / math.sqrt(1.0 - e2 * math.sin(lat) ** 2)
    alt = p / math.cos(lat) - n
    return math.degrees(lat), math.degrees(lon), alt


@pytest.mark.parametrize(
    "lat_deg,lon_deg,alt_m",
    [
        (0.0, 0.0, 0.0),
        (0.0, 90.0, 500_000.0),
        (37.5665, 126.9780, 100.0),   # Seoul
        (-33.8688, 151.2093, 0.0),    # Sydney
        (89.9, 45.0, 700_000.0),      # near pole, LEO altitude
        (-89.9, -120.0, 700_000.0),
    ],
)
def test_geodetic_ecef_round_trip_and_numpy_cross_check(lat_deg, lon_deg, alt_m):
    ecef = geodetic_to_ecef(lat_deg, lon_deg, alt_m)

    ref_ecef = _numpy_geodetic_to_ecef(lat_deg, lon_deg, alt_m)
    np.testing.assert_allclose(ecef, ref_ecef, rtol=1e-9, atol=1e-6)

    lat_out, lon_out, alt_out = ecef_to_geodetic(ecef)
    assert lat_out == pytest.approx(lat_deg, abs=1e-7)
    assert lon_out == pytest.approx(lon_deg, abs=1e-7)
    assert alt_out == pytest.approx(alt_m, abs=1e-3)

    ref_lat, ref_lon, ref_alt = _numpy_bowring_closed_form(ecef)
    assert lat_out == pytest.approx(ref_lat, abs=1e-6)
    assert alt_out == pytest.approx(ref_alt, abs=1e-2)


def test_ecef_to_geodetic_north_pole():
    lat, lon, alt = ecef_to_geodetic((0.0, 0.0, WGS84_B))
    assert lat == pytest.approx(90.0, abs=1e-9)
    assert alt == pytest.approx(0.0, abs=1e-6)
