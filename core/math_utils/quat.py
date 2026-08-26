"""3차원 벡터 및 쿼터니언 연산 공용 라이브러리.

core/coordinates.py,core/geometry/footprint.py 등 
좌표/자세 계산 전반에서 공유하는 원시 연산.

쿼터니언은 별도 표기가 없는 한 scalar-first (w, x, y, z) 표기 사용
"""
from __future__ import annotations

import math

Vector3 = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]


def _to_float_tuple3(values: Vector3 | list[float]) -> Vector3:
    if len(values) != 3:
        raise ValueError(f"Expected 3-vector, got {len(values)} values")
    return (float(values[0]), float(values[1]), float(values[2]))


def _to_float_tuple4(values: Quaternion | list[float]) -> Quaternion:
    if len(values) != 4:
        raise ValueError(f"Expected 4-vector, got {len(values)} values")
    return (float(values[0]), float(values[1]), float(values[2]), float(values[3]))


# Vector3 연산

def dot(a: Vector3 | list[float], b: Vector3 | list[float]) -> float:
    ax, ay, az = _to_float_tuple3(a)
    bx, by, bz = _to_float_tuple3(b)
    return ax * bx + ay * by + az * bz


def cross(a: Vector3 | list[float], b: Vector3 | list[float]) -> Vector3:
    ax, ay, az = _to_float_tuple3(a)
    bx, by, bz = _to_float_tuple3(b)
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


def magnitude(v: Vector3 | list[float]) -> float:
    x, y, z = _to_float_tuple3(v)
    return math.sqrt(x * x + y * y + z * z)


def normalize(v: Vector3 | list[float]) -> Vector3:
    x, y, z = _to_float_tuple3(v)
    m = math.sqrt(x * x + y * y + z * z)
    if m == 0.0:
        return (0.0, 0.0, 0.0)
    return (x / m, y / m, z / m)


def vec_add(a: Vector3 | list[float], b: Vector3 | list[float]) -> Vector3:
    ax, ay, az = _to_float_tuple3(a)
    bx, by, bz = _to_float_tuple3(b)
    return (ax + bx, ay + by, az + bz)


def vec_sub(a: Vector3 | list[float], b: Vector3 | list[float]) -> Vector3:
    ax, ay, az = _to_float_tuple3(a)
    bx, by, bz = _to_float_tuple3(b)
    return (ax - bx, ay - by, az - bz)


def vec_scale(v: Vector3 | list[float], s: float) -> Vector3:
    x, y, z = _to_float_tuple3(v)
    s = float(s)
    return (x * s, y * s, z * s)


# Quaternion 연산 (scalar-first: w, x, y, z)

def quaternion_normalize(q: Quaternion | list[float]) -> Quaternion:
    qf = _to_float_tuple4(q)
    norm = math.sqrt(sum(v * v for v in qf))
    if norm == 0.0:
        return (1.0, 0.0, 0.0, 0.0)
    return (qf[0] / norm, qf[1] / norm, qf[2] / norm, qf[3] / norm)


def quaternion_conjugate(q: Quaternion | list[float]) -> Quaternion:
    w, x, y, z = _to_float_tuple4(q)
    return (w, -x, -y, -z)


def quaternion_multiply(q1: Quaternion | list[float], q2: Quaternion | list[float]) -> Quaternion:
    w1, x1, y1, z1 = _to_float_tuple4(q1)
    w2, x2, y2, z2 = _to_float_tuple4(q2)
    return (
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    )


def quat_from_scalar_last(q: Quaternion | list[float]) -> Quaternion:
    """[x, y, z, w] (scalar-last) -> (w, x, y, z) (scalar-first)."""
    x, y, z, w = _to_float_tuple4(q)
    return (w, x, y, z)


def quat_to_scalar_last(q: Quaternion | list[float]) -> Quaternion:
    """(w, x, y, z) (scalar-first) -> [x, y, z, w] (scalar-last, 예: Cesium/scipy 관례)."""
    w, x, y, z = _to_float_tuple4(q)
    return (x, y, z, w)


def rotate_vector_by_quaternion(vec: Vector3 | list[float], q: Quaternion | list[float]) -> Vector3:
    """단위 쿼터니언으로 3D 벡터를 회전.

    q*v*q_conj를 직접 계산하는 대신, double cross-product 형태를 사용해 
    쿼터니언 곱셈 2회(약 32회 곱셈) 대신 외적 2회 + 스칼라
    연산(약 15회 곱셈)만으로 회전 계산.

        t  = 2 * (u x v)
        v' = v + w * t + (u x t),   q = (w, u)
    """
    v = _to_float_tuple3(vec)
    w, x, y, z = quaternion_normalize(q)
    u = (x, y, z)

    tx, ty, tz = cross(u, v)
    t = (2.0 * tx, 2.0 * ty, 2.0 * tz)

    cx, cy, cz = cross(u, t)
    return (
        v[0] + w * t[0] + cx,
        v[1] + w * t[1] + cy,
        v[2] + w * t[2] + cz,
    )


def rotate_vector_axis_angle(vec: Vector3 | list[float], axis: Vector3 | list[float], angle_rad: float) -> Vector3:
    """Rodrigues 회전 공식으로 임의의 단위축 기준 회전을 적용.

    v_rot = v*cos(theta) + (k x v)*sin(theta) + k*(k.v)*(1 - cos(theta))
    """
    v = _to_float_tuple3(vec)
    k = normalize(axis)
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    kv = dot(k, v)
    kxv = cross(k, v)

    return (
        v[0] * c + kxv[0] * s + k[0] * kv * (1.0 - c),
        v[1] * c + kxv[1] * s + k[1] * kv * (1.0 - c),
        v[2] * c + kxv[2] * s + k[2] * kv * (1.0 - c),
    )


def quaternion_to_euler_xyz(q: Quaternion | list[float]) -> Vector3:
    """scalar-first 쿼터니언 -> XYZ 시퀀스 오일러각(rad)."""
    w, x, y, z = quaternion_normalize(q)
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2.0 * (w * y - z * x))))
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return (roll, pitch, yaw)


def euler_xyz_to_quaternion(roll: float, pitch: float, yaw: float) -> Quaternion:
    """XYZ 시퀀스 오일러각(rad) -> scalar-first 쿼터니언."""
    hr, hp, hy = roll * 0.5, pitch * 0.5, yaw * 0.5
    cr, sr = math.cos(hr), math.sin(hr)
    cp, sp = math.cos(hp), math.sin(hp)
    cy, sy = math.cos(hy), math.sin(hy)

    w = cr * cp * cy - sr * sp * sy
    x = sr * cp * cy + cr * sp * sy
    y = cr * sp * cy - sr * cp * sy
    z = cr * cp * sy + sr * sp * cy
    return quaternion_normalize((w, x, y, z))


def quaternion_to_rotation_matrix(q: Quaternion | list[float]):
    """scalar-first 쿼터니언 -> 3x3 회전행렬 (행 우선 튜플의 튜플)."""
    w, x, y, z = quaternion_normalize(q)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z

    return (
        (1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)),
        (2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)),
        (2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)),
    )


# 3x3 행렬 연산

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


def matvec_mul(matrix, vec: Vector3 | list[float]) -> Vector3:
    x, y, z = _to_float_tuple3(vec)
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2] * z,
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2] * z,
        matrix[2][0] * x + matrix[2][1] * y + matrix[2][2] * z,
    )


def matmul(a, b):
    return tuple(
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
        for i in range(3)
    )
