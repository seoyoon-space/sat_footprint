from __future__ import annotations

import math
from datetime import datetime, timezone


EARTH_ROTATION_RATE_RAD_PER_SEC = 7.2921150e-5


def _to_float_tuple(values: tuple[float, float, float] | list[float] | tuple[float, ...]) -> tuple[float, float, float]:
    if len(values) != 3:
        raise ValueError(f"Expected 3-vector, got {len(values)} values")
    return (float(values[0]), float(values[1]), float(values[2]))


def dot(a: tuple[float, float, float] | list[float], b: tuple[float, float, float] | list[float]) -> float:
    ax, ay, az = _to_float_tuple(a)
    bx, by, bz = _to_float_tuple(b)
    return ax * bx + ay * by + az * bz


def cross(a: tuple[float, float, float] | list[float], b: tuple[float, float, float] | list[float]) -> tuple[float, float, float]:
    ax, ay, az = _to_float_tuple(a)
    bx, by, bz = _to_float_tuple(b)
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


def magnitude(v: tuple[float, float, float] | list[float]) -> float:
    x, y, z = _to_float_tuple(v)
    return math.sqrt(x * x + y * y + z * z)


def normalize(v: tuple[float, float, float] | list[float]) -> tuple[float, float, float]:
    m = magnitude(v)
    if m == 0.0:
        return (0.0, 0.0, 0.0)
    x, y, z = _to_float_tuple(v)
    return (x / m, y / m, z / m)


def quaternion_normalize(q: tuple[float, float, float, float] | list[float]) -> tuple[float, float, float, float]:
    if len(q) != 4:
        raise ValueError(f"Expected quaternion with 4 values, got {len(q)}")
    qf = tuple(float(v) for v in q)
    norm = math.sqrt(sum(v * v for v in qf))
    if norm == 0.0:
        return (1.0, 0.0, 0.0, 0.0)
    return tuple(v / norm for v in qf)


def quaternion_multiply(q1: tuple[float, float, float, float] | list[float], q2: tuple[float, float, float, float] | list[float]) -> tuple[float, float, float, float]:
    if len(q1) != 4 or len(q2) != 4:
        raise ValueError("Quaternion multiplication requires 4-element inputs")

    w1, x1, y1, z1 = (float(v) for v in q1)
    w2, x2, y2, z2 = (float(v) for v in q2)
    return (
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    )


def quat_from_scalar_last(q: tuple[float, float, float, float] | list[float]) -> tuple[float, float, float, float]:
    if len(q) != 4:
        raise ValueError(f"Expected 4 components, got {len(q)}")
    x, y, z, w = (float(v) for v in q)
    return (w, x, y, z)


def rotate_vector_by_quaternion(vec: tuple[float, float, float] | list[float], q: tuple[float, float, float, float] | list[float]) -> tuple[float, float, float]:
    """Rotate a 3D vector using a scalar-first quaternion (w, x, y, z)."""
    v = _to_float_tuple(vec)
    qn = quaternion_normalize(q)
    w, x, y, z = qn
    # q * v * q_conj
    qv = (0.0, v[0], v[1], v[2])
    q_conj = (w, -x, -y, -z)
    p = quaternion_multiply(qn, qv)
    p = quaternion_multiply(p, q_conj)
    return (p[1], p[2], p[3])


def pointing_vector_from_quaternion(q: tuple[float, float, float, float] | list[float]) -> tuple[float, float, float]:
    """Return the body X-axis direction after applying the quaternion in ECI/space-fixed coordinates."""
    v_body_x = (1.0, 0.0, 0.0)
    return rotate_vector_by_quaternion(v_body_x, q)


def rotation_matrix_3d(axis: str, angle_rad: float):
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    if axis == "x":
        return ((1.0, 0.0, 0.0), (0.0, c, -s), (0.0, s, c))
    if axis == "y":
        return ((c, 0.0, s), (0.0, 1.0, 0.0), (-s, 0.0, c))
    if axis == "z":
        return ((c, -s, 0.0), (s, c, 0.0), (0.0, 0.0, 1.0))
    raise ValueError(f"Unsupported axis '{axis}'")


def matvec_mul(matrix, vec):
    return (
        matrix[0][0] * vec[0] + matrix[0][1] * vec[1] + matrix[0][2] * vec[2],
        matrix[1][0] * vec[0] + matrix[1][1] * vec[1] + matrix[1][2] * vec[2],
        matrix[2][0] * vec[0] + matrix[2][1] * vec[1] + matrix[2][2] * vec[2],
    )


def eci_to_ecef(vec_eci, utc_datetime: datetime) -> tuple[float, float, float]:
    """Rotate an ECI vector to an ECEF vector using Earth rotation angle for the given UTC time."""
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)
    gmst = earth_rotation_angle_rad(utc_datetime)
    return matvec_mul(rotation_matrix_3d("z", gmst), _to_float_tuple(vec_eci))


def ecef_to_eci(vec_ecef, utc_datetime: datetime) -> tuple[float, float, float]:
    """Rotate an ECEF vector to an ECI vector using the inverse Earth rotation angle."""
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)
    gmst = earth_rotation_angle_rad(utc_datetime)
    return matvec_mul(rotation_matrix_3d("z", -gmst), _to_float_tuple(vec_ecef))


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
    # GMST approximation in radians
    radians_per_day = 2.0 * math.pi
    gmst_days = 0.7790572732640 + 1.00273781191135448 * ((jd - 2451545.0) % 1.0)
    return radians_per_day * gmst_days


def quaternion_to_euler_xyz(q: tuple[float, float, float, float] | list[float]) -> tuple[float, float, float]:
    """Convert scalar-first quaternion to Euler angles in radians using XYZ sequence."""
    w, x, y, z = quaternion_normalize(q)
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return (roll, pitch, yaw)


def quaternion_to_cesium_unit_quaternion(q: tuple[float, float, float, float] | list[float]) -> tuple[float, float, float, float]:
    """Return the quaternion in the order Cesium expects for CZML unitQuaternion: [x, y, z, w]."""
    qn = quaternion_normalize(q)
    return (float(qn[1]), float(qn[2]), float(qn[3]), float(qn[0]))


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

    for row in df.itertuples(index=False):
        ts = getattr(row, time_col)
        ts_dt = pd.Timestamp(ts).tz_convert("UTC") if not isinstance(ts, (int, float)) else pd.to_datetime(ts, unit="s", utc=True)
        offset_seconds = (ts_dt - epoch).total_seconds()

        if all(c in df.columns for c in position_cols):
            x = float(getattr(row, position_cols[0]))
            y = float(getattr(row, position_cols[1]))
            z = float(getattr(row, position_cols[2]))
            if frame == "ecef":
                x, y, z = eci_to_ecef((x, y, z), ts_dt.to_pydatetime())
            pos_entries.extend([offset_seconds, x, y, z])

        if all(c in df.columns for c in orientation_cols):
            q = (
                float(getattr(row, orientation_cols[0])),
                float(getattr(row, orientation_cols[1])),
                float(getattr(row, orientation_cols[2])),
                float(getattr(row, orientation_cols[3])),
            )
            xq, yq, zq, wq = quaternion_to_cesium_unit_quaternion(q)
            orientation_entries.extend([offset_seconds, xq, yq, zq, wq])

        if pointing_col and pointing_col in df.columns:
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
