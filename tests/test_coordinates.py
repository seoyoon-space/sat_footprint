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
    earth_rotation_angle_rad,
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
    matvec_mul,
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


def test_eci_ecef_round_trip_and_norm_preserved():
    """eci_to_ecef/ecef_to_eci는 (근사)직교 회전 합성이므로 왕복 시 원값 복원, 노름 불변."""
    from datetime import datetime, timezone

    from core.coordinates import ecef_to_eci

    dt = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)
    v_eci = (7000e3, 1234e3, -555e3)

    v_ecef = eci_to_ecef(v_eci, dt)
    back = ecef_to_eci(v_ecef, dt)

    np.testing.assert_allclose(back, v_eci, atol=1e-6)
    np.testing.assert_allclose(np.linalg.norm(v_ecef), np.linalg.norm(v_eci), rtol=1e-12)


def test_eci_ecef_rotation_sign_matches_greenwich_meridian_physics():
    """ECI<->ECEF 회전 방향(부호) 검증: 그리니치 자오선(ECEF +x축)에 고정된 점은
    ECI 기준으로 적경(RA) ~= GMST 방향을 가리켜야 한다(항성시의 정의 자체가 그렇다).

    과거에는 eci_to_ecef가 반대 부호로 회전해 위성 지상궤적이 잘못된 경도에 표시되는
    버그가 있었는데, 이 관계로만 정확히 잡아낼 수 있다(위도만 보는 테스트나 자기 자신의
    각도 계산을 그대로 재사용하는 self-consistency 테스트는 부호 오류를 못 잡는다).
    허용 오차는 세차(연 ~50", 수십 년 누적 시 최대 1도 미만) 규모로 넉넉히 잡는다.
    """
    from datetime import datetime, timezone

    from core.coordinates import ecef_to_eci

    for dt in (
        datetime(1990, 6, 10, 3, 0, 0, tzinfo=timezone.utc),  # 1997년 이전: GAST 운동학적 보정항 미적용 분기
        datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc),  # J2000 epoch 근방(세차~0)
        datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc),
        datetime(2040, 3, 15, 6, 30, 0, tzinfo=timezone.utc),
    ):
        gmst_deg = math.degrees(earth_rotation_angle_rad(dt))
        greenwich_ecef = (WGS84_A, 0.0, 0.0)
        eci_pt = ecef_to_eci(greenwich_ecef, dt)
        ra_deg = math.degrees(math.atan2(eci_pt[1], eci_pt[0])) % 360.0

        diff = (ra_deg - gmst_deg + 180.0) % 360.0 - 180.0
        assert abs(diff) < 1.0, f"{dt}: RA({ra_deg}) should track GMST({gmst_deg}) within ~1 deg, got diff={diff}"


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


# ---------------------------------------------------------------------------
# 세차/장동/겉보기항성시(IAU-76/FK5 축약 모델) - 물리적 크기 범위 검증
# ---------------------------------------------------------------------------

def test_mean_obliquity_within_known_physical_range():
    from core.coordinates import _mean_obliquity_rad

    # 평균 황도경사각은 J2000 부근에서 약 23.4392794도이며, 세기당 약 47" 씩 서서히 감소
    for t_centuries in (-1.0, 0.0, 1.0):
        eps_deg = math.degrees(_mean_obliquity_rad(t_centuries))
        assert 23.0 < eps_deg < 23.6, f"T={t_centuries}: mean obliquity {eps_deg} deg out of expected range"

    eps_j2000 = math.degrees(_mean_obliquity_rad(0.0))
    assert eps_j2000 == pytest.approx(23.4392911, abs=1e-4)


def test_nutation_angles_within_known_physical_bounds():
    from core.coordinates import _nutation_angles_rad

    for t_centuries in (-2.0, -0.5, 0.0, 0.5, 2.0):
        delta_psi, delta_eps, omega = _nutation_angles_rad(t_centuries)
        # 장동의 지배항(주기 ~18.6년)의 진폭은 각각 최대 약 17.2", 9.2" 수준이며,
        # 저정밀도 4항 공식의 합도 이 범위를 크게 벗어날 수 없다.
        assert abs(math.degrees(delta_psi) * 3600.0) < 20.0
        assert abs(math.degrees(delta_eps) * 3600.0) < 11.0
        # omega(달 궤도 승교점 황경)는 다항식 원값을 그대로 반환하므로(삼각함수 인자로만
        # 쓰이고 그 자체로는 정규화가 필요 없음) 유한한 실수인지만 확인
        assert math.isfinite(omega)


def test_precession_matrix_is_near_identity_at_j2000_epoch():
    """T=0(J2000.0 정의 시점) 근방에서는 세차각이 0에 수렴하므로 세차행렬은 항등행렬에 가까워야 함."""
    from core.coordinates import _precession_matrix_eci_to_mod

    m = _precession_matrix_eci_to_mod(1e-6)  # J2000으로부터 아주 짧은 시간
    identity_diff = max(
        abs(m[i][j] - (1.0 if i == j else 0.0)) for i in range(3) for j in range(3)
    )
    assert identity_diff < 1e-6


def test_apparent_sidereal_time_kinematic_term_only_applied_after_1997():
    """GAST = GMST + 분점방정식. 1997년 이후에만 붙는 운동학적 보정항(계수 0.00264"/0.000063")이
    실제로 그 경계(JD 2450449.5)를 기준으로 정확히 켜지고 꺼지는지 직접 검증한다.
    보정항 자체는 밀리각초 수준으로 작아서, 다른 회전-방향 테스트의 1도 허용오차로는
    이 분기 로직의 존재 여부를 구분할 수 없다.
    """
    from datetime import datetime, timezone

    from core.coordinates import (
        ARCSEC_TO_RAD,
        _apparent_sidereal_time_rad,
        _julian_centuries_j2000,
        _mean_obliquity_rad,
        _nutation_angles_rad,
    )

    dt_before = datetime(1990, 6, 10, 3, 0, 0, tzinfo=timezone.utc)
    dt_after = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)

    for dt, expect_kinematic_term in ((dt_before, False), (dt_after, True)):
        t = _julian_centuries_j2000(dt)
        mean_eps = _mean_obliquity_rad(t)
        delta_psi, _, omega = _nutation_angles_rad(t)

        gast = _apparent_sidereal_time_rad(dt, delta_psi, mean_eps, omega)
        gmst = earth_rotation_angle_rad(dt)
        eqe_without_kinematic = delta_psi * math.cos(mean_eps)
        eqe_with_kinematic = eqe_without_kinematic + (
            0.00264 * math.sin(omega) + 0.000063 * math.sin(2.0 * omega)
        ) * ARCSEC_TO_RAD

        expected_eqe = eqe_with_kinematic if expect_kinematic_term else eqe_without_kinematic
        expected_gast = (gmst + expected_eqe) % (2.0 * math.pi)

        assert gast == pytest.approx(expected_gast, abs=1e-12)


def test_precession_nutation_cached_per_utc_date():
    """_earth_orientation_matrices의 세차/장동 부분은 UTC 날짜 단위로 캐시된다(성능 최적화).
    같은 날짜의 서로 다른 시각은 캐시를 공유(동일 객체)해야 하고, 다른 날짜는 별도 계산이어야 한다.
    GMST(겉보기항성시)만큼은 이 캐시와 무관하게 항상 타임스탬프별로 달라져야 한다(지구 자전 반영).
    """
    from datetime import datetime, timezone

    from core.coordinates import _earth_orientation_matrices, _precession_nutation_for_date

    _precession_nutation_for_date.cache_clear()

    dt_a = datetime(2026, 8, 20, 1, 0, 0, tzinfo=timezone.utc)
    dt_b = datetime(2026, 8, 20, 23, 0, 0, tzinfo=timezone.utc)  # 같은 날, 다른 시각
    dt_c = datetime(2026, 8, 21, 1, 0, 0, tzinfo=timezone.utc)  # 다음 날

    same_day_1 = _precession_nutation_for_date(dt_a.date())
    same_day_2 = _precession_nutation_for_date(dt_b.date())
    next_day = _precession_nutation_for_date(dt_c.date())

    assert same_day_1 is same_day_2  # 캐시 히트: 동일 튜플 객체
    assert same_day_1 is not next_day  # 날짜가 바뀌면 재계산

    # 같은 날짜라도 GAST(따라서 최종 ECI<->ECEF 회전)는 시각마다 달라져야 함
    _, _, ast_a = _earth_orientation_matrices(dt_a)
    _, _, ast_b = _earth_orientation_matrices(dt_b)
    assert ast_a != ast_b


def test_precession_matrix_rotation_rate_matches_known_50_arcsec_per_year():
    """세차의 지배항(zeta+z 선형계수 합)은 약 50.29"/year(황도 세차의 잘 알려진 크기)와 일치해야 함."""
    from core.coordinates import _precession_matrix_eci_to_mod

    # 1세기(100년) 전후 두 세차행렬로 ECI +x축이 얼마나 회전하는지 측정
    v = (1.0, 0.0, 0.0)
    v_now = matvec_mul(_precession_matrix_eci_to_mod(0.0), v)
    v_1c = matvec_mul(_precession_matrix_eci_to_mod(1.0), v)

    angle_per_century_arcsec = math.degrees(math.acos(max(-1.0, min(1.0, dot(v_now, v_1c))))) * 3600.0
    # 알려진 세차율: 약 50.29"/year * 100 = 5029"/century (자오선 상 좌표계 회전 관점에서는
    # 이보다 다소 클 수 있으나 - 실제 회전각은 대략 zeta+z 근방인 4600~5100"/century 범위) 이내인지 확인
    assert 4000.0 < angle_per_century_arcsec < 6000.0
