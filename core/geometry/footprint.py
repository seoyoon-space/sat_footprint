"""카메라 LOS(Line of Sight), ECEF 변환, WGS-84 타원체 교점 및 GeoJSON 풋프린트 생성.

쿼터니언은 core.coordinates와 동일하게, 바디 프레임 벡터를 ECI 프레임으로
회전시키는 것으로 취급(core.coordinates.pointing_vector_from_quaternion과 동일 관례).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from core.coordinates import WGS84_A, WGS84_B, ecef_to_geodetic, eci_to_ecef
from core.math_utils.quat import (
    Quaternion,
    Vector3,
    cross,
    dot,
    normalize,
    rotate_vector_axis_angle,
    rotate_vector_by_quaternion,
)

__all__ = [
    "intersect_wgs84_ellipsoid",
    "boresight_ray_ecef",
    "fov_corner_rays_body",
    "compute_footprint",
    "footprint_to_geojson",
]


def intersect_wgs84_ellipsoid(
    origin_ecef: Vector3,
    direction_ecef: Vector3,
    *,
    a: float = WGS84_A,
    b: float = WGS84_B,
) -> Vector3 | None:
    """WGS-84 타원체와 광선(origin + t*direction, t>=0)의 최근접 교점을 계산.

    타원체 방정식 x^2/a^2 + y^2/a^2 + z^2/b^2 = 1 에 광선 방정식을 대입하면
    t에 대한 2차방정식 A*t^2 + B*t + C = 0 이 되며, t>=0인 실근 중 가장 작은
    값이 위성에서 지표면을 내려다볼 때 처음 만나는 지점이다. 실근이 없으면
    광선이 지구를 비켜가는 경우(지평선 너머를 보는 경우)이므로 None 반환.
    """
    ox, oy, oz = origin_ecef
    dx, dy, dz = normalize(direction_ecef)

    a2 = a * a
    b2 = b * b

    coeff_a = (dx * dx + dy * dy) / a2 + (dz * dz) / b2
    coeff_b = 2.0 * ((ox * dx + oy * dy) / a2 + (oz * dz) / b2)
    coeff_c = (ox * ox + oy * oy) / a2 + (oz * oz) / b2 - 1.0

    if coeff_a == 0.0:
        return None

    discriminant = coeff_b * coeff_b - 4.0 * coeff_a * coeff_c
    if discriminant < 0.0:
        return None

    sqrt_disc = math.sqrt(discriminant)
    t1 = (-coeff_b - sqrt_disc) / (2.0 * coeff_a)
    t2 = (-coeff_b + sqrt_disc) / (2.0 * coeff_a)

    candidates = [t for t in (t1, t2) if t >= 0.0]
    if not candidates:
        return None
    t = min(candidates)

    return (ox + t * dx, oy + t * dy, oz + t * dz)


def boresight_ray_ecef(
    sat_pos_eci: Vector3,
    quaternion_body2eci: Quaternion,
    utc_datetime: datetime,
    boresight_body: Vector3 = (0.0, 0.0, 1.0),
) -> tuple[Vector3, Vector3]:
    """위성 위치(ECI)와 자세 쿼터니언으로부터 카메라 시선(LOS)의 ECEF 원점/방향을 계산."""
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)

    direction_eci = rotate_vector_by_quaternion(boresight_body, quaternion_body2eci)
    origin_ecef = eci_to_ecef(sat_pos_eci, utc_datetime)
    # 방향 벡터는 위치가 아니므로 회전만 적용(ECI->ECEF는 원점이 동일한 순수 회전)
    direction_ecef = eci_to_ecef(direction_eci, utc_datetime)
    return origin_ecef, direction_ecef


def fov_corner_rays_body(
    boresight_body: Vector3,
    fov_x_deg: float,
    fov_y_deg: float,
    up_hint: Vector3 = (0.0, 0.0, 1.0),
) -> list[Vector3]:
    """바디 프레임 기준 카메라 시야각(FOV) 네 모서리 방향 벡터를 계산.

    boresight를 중심으로 직교하는 right/up 축을 만들고, 각 축 기준으로
    Rodrigues 회전을 두 번 합성해(피치 -> 요) 모서리 광선 계산
    반환 순서는 폴리곤 링으로 바로 가능하도록, (-x,-y)->(+x,-y)->(+x,+y)->(-x,+y) 순서.
    """
    boresight = normalize(boresight_body)
    reference = up_hint if abs(dot(boresight, up_hint)) < 0.999 else (0.0, 1.0, 0.0)
    right = normalize(cross(reference, boresight))
    up = normalize(cross(boresight, right))

    half_x = math.radians(fov_x_deg) / 2.0
    half_y = math.radians(fov_y_deg) / 2.0

    corners = []
    for sign_x, sign_y in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)):
        ray = rotate_vector_axis_angle(boresight, right, sign_y * half_y)
        ray = rotate_vector_axis_angle(ray, up, sign_x * half_x)
        corners.append(normalize(ray))
    return corners


def compute_footprint(
    sat_pos_eci: Vector3,
    quaternion_body2eci: Quaternion,
    utc_datetime: datetime,
    fov_x_deg: float,
    fov_y_deg: float,
    boresight_body: Vector3 = (0.0, 0.0, 1.0),
) -> dict:
    """카메라 FOV의 지상 풋프린트(중심점 + 네 모서리)를 위경도(lon, lat)로 계산.

    지평선 너머를 바라보는 광선은 None으로 남기고, `visible`이 False이면
    FOV의 일부(또는 전부)가 지구를 비켜가고 있다는 뜻.
    """
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)

    origin_ecef = eci_to_ecef(sat_pos_eci, utc_datetime)
    boresight = normalize(boresight_body)
    corner_dirs_body = fov_corner_rays_body(boresight, fov_x_deg, fov_y_deg)

    def _ray_to_lonlat(dir_body: Vector3) -> tuple[float, float] | None:
        dir_eci = rotate_vector_by_quaternion(dir_body, quaternion_body2eci)
        dir_ecef = eci_to_ecef(dir_eci, utc_datetime)
        hit = intersect_wgs84_ellipsoid(origin_ecef, dir_ecef)
        if hit is None:
            return None
        lat, lon, _alt = ecef_to_geodetic(hit)
        return (lon, lat)

    center = _ray_to_lonlat(boresight)
    corners = [_ray_to_lonlat(d) for d in corner_dirs_body]

    return {
        "center": center,
        "corners": corners,
        "visible": center is not None and all(c is not None for c in corners),
    }


def footprint_to_geojson(footprint: dict, properties: dict | None = None) -> dict:
    """compute_footprint() 결과를 GeoJSON FeatureCollection(dict)으로 변환.

    geojson 패키지 없이 GeoJSON 스펙을 그대로 따르는 순수 dict를 직접 구성한다.
    """
    props = dict(properties or {})
    features: list[dict] = []

    corners = footprint.get("corners") or []
    valid_corners = [c for c in corners if c is not None]
    if footprint.get("visible") and len(valid_corners) >= 3:
        ring = [list(pt) for pt in corners] + [list(corners[0])]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": props,
            }
        )

    center = footprint.get("center")
    if center is not None:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": list(center)},
                "properties": {**props, "role": "boresight_center"},
            }
        )

    return {"type": "FeatureCollection", "features": features}
