from __future__ import annotations

import math
from datetime import datetime, timezone

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


def earth_rotation_angle_rad(utc_datetime: datetime) -> float:
    """Approximate Greenwich sidereal angle in radians using the J2000 convention."""
    jd = julian_date_from_datetime(utc_datetime)
    radians_per_day = 2.0 * math.pi
    gmst_days = 0.7790572732640 + 1.00273781191135448 * ((jd - 2451545.0) % 1.0)
    return radians_per_day * gmst_days


def eci_to_ecef(vec_eci, utc_datetime: datetime) -> Vector3:
    """Rotate an ECI vector to an ECEF vector using Earth rotation angle for the given UTC time."""
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)
    gmst = earth_rotation_angle_rad(utc_datetime)
    return matvec_mul(rotation_matrix_3d("z", gmst), _to_float_tuple(vec_eci))


def ecef_to_eci(vec_ecef, utc_datetime: datetime) -> Vector3:
    """Rotate an ECEF vector to an ECI vector using the inverse Earth rotation angle."""
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)
    gmst = earth_rotation_angle_rad(utc_datetime)
    return matvec_mul(rotation_matrix_3d("z", -gmst), _to_float_tuple(vec_ecef))


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
