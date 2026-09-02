from __future__ import annotations

import math
from datetime import date, datetime, timezone
from functools import lru_cache

from core.math_utils.quat import (
    Quaternion,
    Vector3,
    cross,
    dot,
    magnitude,
    matvec_mul,
    normalize,
    quat_from_scalar_last,
    quat_to_scalar_last,
    quaternion_multiply,
    quaternion_normalize,
    quaternion_to_euler_xyz,
    rotate_vector_by_quaternion,
    rotation_matrix_3d,
    transpose3x3,
)

__all__ = [
    "dot",
    "cross",
    "magnitude",
    "normalize",
    "quaternion_normalize",
    "quaternion_multiply",
    "quat_from_scalar_last",
    "rotate_vector_by_quaternion",
    "pointing_vector_from_quaternion",
    "rotation_matrix_3d",
    "matvec_mul",
    "eci_to_ecef",
    "ecef_to_eci",
    "julian_date_from_datetime",
    "earth_rotation_angle_rad",
    "quaternion_to_euler_xyz",
    "quaternion_to_cesium_unit_quaternion",
    "geodetic_to_ecef",
    "ecef_to_geodetic",
    "build_cesium_track_czml",
]


EARTH_ROTATION_RATE_RAD_PER_SEC = 7.2921150e-5

# WGS-84 타원체
WGS84_A = 6378137.0                       # 장반경 [m]
WGS84_F = 1.0 / 298.257223563             # 편평률
WGS84_B = WGS84_A * (1.0 - WGS84_F)       # 단반경 [m]
WGS84_E2 = WGS84_F * (2.0 - WGS84_F)      # 이심률의 제곱


def _to_float_tuple(values: Vector3 | list[float] | tuple[float, ...]) -> Vector3:
    if len(values) != 3:
        raise ValueError(f"Expected 3-vector, got {len(values)} values")
    return (float(values[0]), float(values[1]), float(values[2]))


def pointing_vector_from_quaternion(q: Quaternion | list[float]) -> Vector3:
    """Return the body X-axis direction after applying the quaternion in ECI/space-fixed coordinates."""
    v_body_x = (1.0, 0.0, 0.0)
    return rotate_vector_by_quaternion(v_body_x, q)


def julian_date_from_datetime(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    epoch = datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc)
    delta_days = (utc - epoch).total_seconds() / 86400.0
    return 2451545.0 + delta_days


ARCSEC_TO_RAD = math.pi / (180.0 * 3600.0)


def earth_rotation_angle_rad(utc_datetime: datetime) -> float:
    """GMST, IAU-82 다항식(gstime.m과 동일 공식).

    IERS 관측 데이터(UT1-UTC 보정치)가 없어 입력 UTC를 UT1로 근사.
    잔여오차는 |UT1-UTC| <= 0.9s -> 각도 오차 <= 약 13.5각초 수준이며,
    IERS 공표 데이터를 실시간으로 받아오지 않는 한 이 오차는 원리적으로 해소 불가.
    """
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)
    jd = julian_date_from_datetime(utc_datetime)
    tut1 = (jd - 2451545.0) / 36525.0

    # 시간초 단위. 360도/86400초 = 1/240 (도/초)로 각도 변환.
    temp_sec = (
        -6.2e-6 * tut1 * tut1 * tut1
        + 0.093104 * tut1 * tut1
        + (876600.0 * 3600.0 + 8640184.812866) * tut1
        + 67310.54841
    )
    return math.radians(temp_sec / 240.0) % (2.0 * math.pi)


def _julian_centuries_j2000(utc_datetime: datetime) -> float:
    """J2000.0(TT) 기준 율리우스 세기 수. UTC와 TT의 차이(2026년 기준 약 69초)는
    100년 단위 T값에 10^-8 수준의 영향만 주므로 무시하고 UTC로 근사."""
    jd = julian_date_from_datetime(utc_datetime)
    return (jd - 2451545.0) / 36525.0


def _mean_obliquity_rad(t: float) -> float:
    """평균 황도경사각, IAU 1980 모델.

    84381.448" - 46.8150"T - 0.00059"T^2 + 0.001813"T^3  (Vallado precess.m, opt='80'의 'ea').
    """
    arcsec = 84381.448 - 46.8150 * t - 0.00059 * t * t + 0.001813 * t ** 3
    return arcsec * ARCSEC_TO_RAD


def _nutation_angles_rad(t: float) -> tuple[float, float, float]:
    """delta_psi, delta_eps과 omega를 저정밀도 공식으로 계산.

    IAU 1980 이론의 106항 전체 급수 대신, 지배적인 4개 항만 사용하는 Meeus의
    Low Accuracy 공식을 적용. 정확도: delta_psi 오차 < 0.5", delta_eps 오차 < 0.1" (수 세기 범위에서).
    세차(연간 약 50", 이 텔레메트리 기준 26년 누적 시 약 20분각)에 비해 장동은 훨씬
    작고 주기적이라, 이 근사만으로도 세차 미보정으로 인한 큰 계통오차는 해소.
    """
    omega = math.radians(125.04452 - 1934.136261 * t)
    l_sun = math.radians(280.4665 + 36000.7698 * t)
    l_moon = math.radians(218.3165 + 481267.8813 * t)

    delta_psi_arcsec = (
        -17.20 * math.sin(omega)
        - 1.32 * math.sin(2.0 * l_sun)
        - 0.23 * math.sin(2.0 * l_moon)
        + 0.21 * math.sin(2.0 * omega)
    )
    delta_eps_arcsec = (
        9.20 * math.cos(omega)
        + 0.57 * math.cos(2.0 * l_sun)
        + 0.10 * math.cos(2.0 * l_moon)
        - 0.09 * math.cos(2.0 * omega)
    )
    return delta_psi_arcsec * ARCSEC_TO_RAD, delta_eps_arcsec * ARCSEC_TO_RAD, omega


def _precession_matrix_eci_to_mod(t: float):
    """세차 행렬(ECI/GCRF(J2000 평균 적도/분점) -> MOD(당일 평균 적도/분점)).

    IAU 1976 세차각 zeta/theta/z 공식 및 행렬 구성은 Vallado precess.m(opt='80')과 동일.
    원본은 MOD->ECI 방향으로 구성되므로, 필요한 반대 방향은 전치로 얻는다.
    """
    t2 = t * t
    t3 = t2 * t
    zeta = (2306.2181 * t + 0.30188 * t2 + 0.017998 * t3) * ARCSEC_TO_RAD
    theta = (2004.3109 * t - 0.42665 * t2 - 0.041833 * t3) * ARCSEC_TO_RAD
    z = (2306.2181 * t + 1.09468 * t2 + 0.018203 * t3) * ARCSEC_TO_RAD

    cz, sz = math.cos(zeta), math.sin(zeta)
    ct, st = math.cos(theta), math.sin(theta)
    cZ, sZ = math.cos(z), math.sin(z)

    mod_to_eci = (
        (cz * ct * cZ - sz * sZ, cz * ct * sZ + sz * cZ, cz * st),
        (-sz * ct * cZ - cz * sZ, -sz * ct * sZ + cz * cZ, -sz * st),
        (-st * cZ, -st * sZ, ct),
    )
    return transpose3x3(mod_to_eci)


def _nutation_matrix_mod_to_tod(delta_psi: float, mean_eps: float, true_eps: float):
    """장동 행렬(MOD(당일 평균 적도/분점) -> TOD(당일 진 적도/분점)).

    행렬 구성은 Vallado nutation.m과 동일(원본은 TOD->MOD 방향이므로 전치해서 사용).
    """
    cospsi, sinpsi = math.cos(delta_psi), math.sin(delta_psi)
    coseps, sineps = math.cos(mean_eps), math.sin(mean_eps)
    ctrueeps, strueeps = math.cos(true_eps), math.sin(true_eps)

    tod_to_mod = (
        (cospsi, ctrueeps * sinpsi, strueeps * sinpsi),
        (-coseps * sinpsi, ctrueeps * coseps * cospsi + strueeps * sineps, strueeps * coseps * cospsi - sineps * ctrueeps),
        (-sineps * sinpsi, ctrueeps * sineps * cospsi - strueeps * coseps, strueeps * sineps * cospsi + ctrueeps * coseps),
    )
    return transpose3x3(tod_to_mod)


def _apparent_sidereal_time_rad(utc_datetime: datetime, delta_psi: float, mean_eps: float, omega: float) -> float:
    """GAST = GMST + 분점방정식.
    """
    gmst = earth_rotation_angle_rad(utc_datetime)
    jd = julian_date_from_datetime(utc_datetime)
    eqe = delta_psi * math.cos(mean_eps)
    if jd > 2450449.5:
        eqe += (0.00264 * math.sin(omega) + 0.000063 * math.sin(2.0 * omega)) * ARCSEC_TO_RAD
    return (gmst + eqe) % (2.0 * math.pi)


@lru_cache(maxsize=64)
def _precession_nutation_for_date(date_key: date):
    """세차/장동 행렬 + 장동각을 UTC 날짜 단위로 캐시.

    세차는 약 50"/year(~0.14"/day), 장동의 지배항은 약 17"의 진폭을 18.6년 주기로
    그리므로 하루 내 변화는 <= 약 0.02"/day 수준. 반면 GMST는
    지구 자전으로 초당 약 15"를 움직이므로 절대 이렇게 캐시하면 안 되고, 항상
    타임스탬프마다 정확히 재계산한다(_earth_orientation_matrices 참고). 날짜 단위
    캐싱으로 추가되는 오차(<= 하루치 세차/장동 변화량)는 GMST 자체가 이미 갖고 있는
    UT1-UTC 미보정 오차(<= 약 13.5", earth_rotation_angle_rad 참고)보다 훨씬 작아
    전체 정확도에 실질적 영향 없음. 
    """
    reference_dt = datetime(date_key.year, date_key.month, date_key.day, 12, 0, 0, tzinfo=timezone.utc)
    t = _julian_centuries_j2000(reference_dt)
    mean_eps = _mean_obliquity_rad(t)
    delta_psi, delta_eps, omega = _nutation_angles_rad(t)
    true_eps = mean_eps + delta_eps
    eci_to_mod = _precession_matrix_eci_to_mod(t)
    mod_to_tod = _nutation_matrix_mod_to_tod(delta_psi, mean_eps, true_eps)
    return eci_to_mod, mod_to_tod, delta_psi, mean_eps, omega


def _earth_orientation_matrices(utc_datetime: datetime):
    """주어진 시각의 (세차 행렬(ECI->MOD), 장동 행렬(MOD->TOD), 겉보기항성시(rad))을 계산."""
    eci_to_mod, mod_to_tod, delta_psi, mean_eps, omega = _precession_nutation_for_date(utc_datetime.date())
    ast = _apparent_sidereal_time_rad(utc_datetime, delta_psi, mean_eps, omega)
    return eci_to_mod, mod_to_tod, ast


def eci_to_ecef(vec_eci, utc_datetime: datetime) -> Vector3:
    """ECI(GCRF/J2000 평균 적도/분점) 벡터 -> ECEF(ITRF 근사) 벡터.

    IAU-76/FK5 축약 모델(세차 + 장동(저정밀도) + 겉보기항성시)을 적용한다. 극운동(polar
    motion)은 IERS 관측치가 없어 생략(영향 < 0.1"로 무시 가능). 잔여오차는 주로
    UT1-UTC 미보정(<= 약 13.5")에서 오며, LEO(~700km) 환산 시 지상 오차 <= 수십 m 수준이다.
    """
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)
    eci_to_mod, mod_to_tod, ast = _earth_orientation_matrices(utc_datetime)
    v = matvec_mul(eci_to_mod, _to_float_tuple(vec_eci))
    v = matvec_mul(mod_to_tod, v)
    v = matvec_mul(rotation_matrix_3d("z", -ast), v)
    return v


def ecef_to_eci(vec_ecef, utc_datetime: datetime) -> Vector3:
    """ECEF(ITRF 근사) 벡터 -> ECI(GCRF/J2000 평균 적도/분점) 벡터. eci_to_ecef의 역변환."""
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)
    eci_to_mod, mod_to_tod, ast = _earth_orientation_matrices(utc_datetime)
    v = matvec_mul(rotation_matrix_3d("z", ast), _to_float_tuple(vec_ecef))
    v = matvec_mul(transpose3x3(mod_to_tod), v)
    v = matvec_mul(transpose3x3(eci_to_mod), v)
    return v


def geodetic_to_ecef(lat_deg: float, lon_deg: float, alt_m: float = 0.0) -> Vector3:
    """WGS-84 측지좌표(위도/경도/고도) -> ECEF 직교좌표 [m]."""
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat, cos_lat = math.sin(lat), math.cos(lat)
    sin_lon, cos_lon = math.sin(lon), math.cos(lon)

    # 수직 방향 곡률 반경 
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)

    x = (n + alt_m) * cos_lat * cos_lon
    y = (n + alt_m) * cos_lat * sin_lon
    z = (n * (1.0 - WGS84_E2) + alt_m) * sin_lat
    return (x, y, z)


def ecef_to_geodetic(vec_ecef: Vector3 | list[float], *, max_iter: int = 8, tol_m: float = 1e-9) -> tuple[float, float, float]:
    """WGS-84 ECEF 직교좌표 -> 측지좌표(위도 deg, 경도 deg, 고도 m).

    Bowring의 초기값으로 시작해 parametric latitude를 소수 회반복 보정하는 방식. 
    지표면 근방 고도 범위에서 mm 이하 정확도로 수렴하며,
    닫힌해가 없는 정확한 반복법이므로 scipy 없이도 안정적으로 동작.
    """
    x, y, z = _to_float_tuple(vec_ecef)
    lon = math.atan2(y, x)

    p = math.hypot(x, y)
    if p < 1e-9:
        # 극점 부근: 경도는 정의되지 않으므로 0으로 두고 위도만 처리
        lat = math.copysign(math.pi / 2.0, z) if z != 0.0 else 0.0
        alt = abs(z) - WGS84_B
        return (math.degrees(lat), math.degrees(lon), alt)

    # Bowring 초기 추정치
    theta = math.atan2(z * WGS84_A, p * WGS84_B)
    ep2 = (WGS84_A * WGS84_A - WGS84_B * WGS84_B) / (WGS84_B * WGS84_B)
    lat = math.atan2(
        z + ep2 * WGS84_B * math.sin(theta) ** 3,
        p - WGS84_E2 * WGS84_A * math.cos(theta) ** 3,
    )

    n = WGS84_A
    for _ in range(max_iter):
        sin_lat = math.sin(lat)
        n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
        alt = p / math.cos(lat) - n
        lat_new = math.atan2(z, p * (1.0 - WGS84_E2 * n / (n + alt)))
        if abs(lat_new - lat) < tol_m / WGS84_A:
            lat = lat_new
            break
        lat = lat_new

    sin_lat = math.sin(lat)
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    alt = p / math.cos(lat) - n

    return (math.degrees(lat), math.degrees(lon), alt)


def quaternion_to_cesium_unit_quaternion(q: Quaternion | list[float]) -> Quaternion:
    """Return the quaternion in the order Cesium expects for CZML unitQuaternion: [x, y, z, w]."""
    return quat_to_scalar_last(quaternion_normalize(q))


def build_cesium_track_czml(
    df,
    *,
    id_prefix: str = "sat",
    coordinate_frame: str = "ecef",
    time_col: str = "time",
    position_cols: tuple[str, str, str] = ("pos_eci_x", "pos_eci_y", "pos_eci_z"),
    orientation_cols: tuple[str, str, str, str] = ("q_eci2body_1", "q_eci2body_2", "q_eci2body_3", "q_eci2body_4"),
    pointing_col: str | None = "pointing_eci",
):
    """Build a Cesium-friendly CZML packet for a time-varying spacecraft track.

    The output is a list of CZML packets suitable for direct use in Cesium:
      - document packet
      - one object packet with `position`/`orientation` time-sampled arrays
    """
    import pandas as pd

    if time_col not in df.columns:
        raise ValueError(f"'{time_col}' column is required to build Cesium CZML")
    if df.empty:
        return [{"id": "document", "version": "1.0"}]

    frame = (coordinate_frame or "ecef").lower()
    if frame not in {"eci", "ecef"}:
        raise ValueError("coordinate_frame must be 'eci' or 'ecef'")

    epoch = pd.Timestamp(df[time_col].iloc[0]).tz_convert("UTC") if pd.api.types.is_datetime64_any_dtype(df[time_col]) else pd.to_datetime(df[time_col].iloc[0], unit="s", utc=True)
    epoch_text = epoch.isoformat().replace("+00:00", "Z")

    pos_entries = []
    orientation_entries = []
    pointing_entries = []

    has_position = all(c in df.columns for c in position_cols)
    has_orientation = all(c in df.columns for c in orientation_cols)
    has_pointing = bool(pointing_col) and pointing_col in df.columns

    for row in df.itertuples(index=False):
        ts = getattr(row, time_col)
        ts_dt = pd.Timestamp(ts).tz_convert("UTC") if not isinstance(ts, (int, float)) else pd.to_datetime(ts, unit="s", utc=True)
        offset_seconds = (ts_dt - epoch).total_seconds()

        if has_position:
            x = float(getattr(row, position_cols[0]))
            y = float(getattr(row, position_cols[1]))
            z = float(getattr(row, position_cols[2]))
            if frame == "ecef":
                x, y, z = eci_to_ecef((x, y, z), ts_dt.to_pydatetime())
            pos_entries.extend([offset_seconds, x, y, z])

        if has_orientation:
            q = (
                float(getattr(row, orientation_cols[0])),
                float(getattr(row, orientation_cols[1])),
                float(getattr(row, orientation_cols[2])),
                float(getattr(row, orientation_cols[3])),
            )
            xq, yq, zq, wq = quaternion_to_cesium_unit_quaternion(q)
            orientation_entries.extend([offset_seconds, xq, yq, zq, wq])

        if has_pointing:
            ptr = getattr(row, pointing_col)
            if ptr is not None:
                if isinstance(ptr, (list, tuple)) and len(ptr) == 3:
                    pointing_entries.extend([offset_seconds, float(ptr[0]), float(ptr[1]), float(ptr[2])])
                elif isinstance(ptr, dict):
                    if "x" in ptr and "y" in ptr and "z" in ptr:
                        pointing_entries.extend([offset_seconds, float(ptr["x"]), float(ptr["y"]), float(ptr["z"])])

    packet = {"id": id_prefix}
    if pos_entries:
        packet["position"] = {"epoch": epoch_text, "cartesian": pos_entries}
    if orientation_entries:
        packet["orientation"] = {"epoch": epoch_text, "unitQuaternion": orientation_entries}
    if pointing_entries:
        packet["pointing_eci"] = {"epoch": epoch_text, "cartesian": pointing_entries}

    return [{"id": "document", "version": "1.0"}, packet]
