"""카메라 LOS(Line of Sight), ECEF 변환, WGS-84 타원체 교점 및 GeoJSON 풋프린트 생성.

쿼터니언은 core.coordinates와 동일하게, 바디 프레임 벡터를 ECI 프레임으로
회전시키는 것으로 취급(core.coordinates.pointing_vector_from_quaternion과 동일).
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

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
    "camera_rays_ecef",
    "compute_footprint",
    "footprint_to_geojson",
    "footprint_to_czml",
    "footprint_track_to_czml",
    "line_ground_points",
    "line_to_geojson",
    "line_track_to_czml",
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
    값이 위성에서 지표면을 내려다볼 때 처음 만나는 지점. 실근이 없으면
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


def camera_rays_ecef(
    sat_pos_eci: Vector3,
    quaternion_body2eci: Quaternion,
    utc_datetime: datetime,
    fov_x_deg: float,
    fov_y_deg: float,
    boresight_body: Vector3 = (0.0, 0.0, 1.0),
) -> dict:
    """카메라 boresight + FOV 네 모서리의 ECEF 광선(공통 원점 + 5개 방향)만 계산.

    타원체/지형과의 교차는 하지 않는다 - 정밀 지형(DEM)을 가진 외부 서버가 이 광선을
    받아 자체 지형모델로 교차시켜 풋프린트를 계산하는 용도로 쓴다(compute_footprint처럼
    이 API 자체가 매끈한 WGS-84 타원체로 근사 교차하는 것보다 더 정확한 결과를 얻을 수
    있음). 방향 벡터는 위치가 아니므로 ECI->ECEF는 순수 회전만 적용한다.
    """
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)

    origin_ecef = eci_to_ecef(sat_pos_eci, utc_datetime)
    boresight = normalize(boresight_body)
    corner_dirs_body = fov_corner_rays_body(boresight, fov_x_deg, fov_y_deg)

    def _dir_ecef(dir_body: Vector3) -> Vector3:
        dir_eci = rotate_vector_by_quaternion(dir_body, quaternion_body2eci)
        return eci_to_ecef(dir_eci, utc_datetime)

    return {
        "origin_ecef": origin_ecef,
        "boresight_direction_ecef": _dir_ecef(boresight),
        "fov_corner_directions_ecef": [_dir_ecef(d) for d in corner_dirs_body],
    }


def compute_footprint(
    sat_pos_eci: Vector3,
    quaternion_body2eci: Quaternion,
    utc_datetime: datetime,
    fov_x_deg: float,
    fov_y_deg: float,
    boresight_body: Vector3 = (0.0, 0.0, 1.0),
) -> dict:
    """카메라 FOV의 지상 풋프린트(중심점 + 네 모서리)를 위경도(lon, lat)로 계산.

    WGS-84 타원체(매끈한 지구본, 실제 지형고도 미반영) 기준 근사 교차이며, 지평선
    너머를 바라보는 광선은 None으로 남기고 `visible`이 False이면 FOV의 일부(또는
    전부)가 지구를 비켜가고 있다는 뜻. 실제 지형(DEM)을 반영한 정밀 풋프린트가
    필요하면 camera_rays_ecef()로 광선만 받아 지형 데이터가 있는 쪽에서 교차시킬 것.
    """
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)

    rays = camera_rays_ecef(sat_pos_eci, quaternion_body2eci, utc_datetime, fov_x_deg, fov_y_deg, boresight_body)
    origin_ecef = rays["origin_ecef"]

    def _hit_to_lonlat(direction_ecef: Vector3) -> tuple[float, float] | None:
        hit = intersect_wgs84_ellipsoid(origin_ecef, direction_ecef)
        if hit is None:
            return None
        lat, lon, _alt = ecef_to_geodetic(hit)
        return (lon, lat)

    center = _hit_to_lonlat(rays["boresight_direction_ecef"])
    corners = [_hit_to_lonlat(d) for d in rays["fov_corner_directions_ecef"]]

    return {
        "center": center,
        "corners": corners,
        "visible": center is not None and all(c is not None for c in corners),
    }


def footprint_to_geojson(footprint: dict, properties: dict | None = None) -> dict:
    """compute_footprint() 결과를 GeoJSON FeatureCollection(dict)으로 변환.

    geojson 패키지 없이 GeoJSON 스펙을 그대로 따르는 순수 dict를 직접 구성.
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


def footprint_to_czml(
    footprint: dict,
    *,
    id_prefix: str = "footprint",
    properties: dict | None = None,
    polygon_fill_rgba: tuple[int, int, int, int] = (255, 255, 0, 80),
    polygon_outline_rgba: tuple[int, int, int, int] = (255, 255, 0, 255),
    center_point_rgba: tuple[int, int, int, int] = (255, 0, 0, 255),
    center_point_pixel_size: float = 8.0,
) -> list[dict]:
    """compute_footprint() 결과를 Cesium CZML 패킷 리스트(document + polygon + point)로 변환.

    GeoJSON과 좌표 표현만 다를 뿐(경위도쌍 대신 CZML의 cartographicDegrees:
    [lon, lat, height, lon, lat, height, ...] 평탄화 배열) 같은 데이터 포함.
    폴리곤 링은 GeoJSON과 달리 첫 점을 마지막에 반복하지 않아도 Cesium이 자동으로 닫음.
    지평선 너머(visible=False)이거나 코너가 3개 미만이면 polygon 패킷은 생략.
    """
    props = dict(properties or {})
    czml: list[dict] = [{"id": "document", "version": "1.0"}]

    corners = footprint.get("corners") or []
    valid_corners = [c for c in corners if c is not None]
    if footprint.get("visible") and len(valid_corners) >= 3:
        cartographic_degrees: list[float] = []
        for lon, lat in corners:
            cartographic_degrees.extend([lon, lat, 0.0])
        czml.append(
            {
                "id": f"{id_prefix}_polygon",
                "properties": props,
                "polygon": {
                    "positions": {"cartographicDegrees": cartographic_degrees},
                    "material": {"solidColor": {"color": {"rgba": list(polygon_fill_rgba)}}},
                    "outline": True,
                    "outlineColor": {"rgba": list(polygon_outline_rgba)},
                },
            }
        )

    center = footprint.get("center")
    if center is not None:
        lon, lat = center
        czml.append(
            {
                "id": f"{id_prefix}_center",
                "properties": {**props, "role": "boresight_center"},
                "position": {"cartographicDegrees": [lon, lat, 0.0]},
                "point": {
                    "color": {"rgba": list(center_point_rgba)},
                    "pixelSize": center_point_pixel_size,
                },
            }
        )

    return czml


def line_ground_points(
    sat_pos_eci: Vector3,
    quaternion_body2eci: Quaternion,
    utc_datetime: datetime,
    fov_across_deg: float,
    boresight_body: Vector3 = (0.0, 0.0, 1.0),
) -> dict:
    """푸시브룸(라인스캔) 센서가 이 순간 스캔 중인 '한 줄'의 좌/우 지상점(WGS-84 타원체
    근사)을 계산.

    실제 카메라는 진행 방향(along-track)으로는 폭이 없는 한 줄만 그 순간 촬영하고,
    위성이 이동하면서 그 줄들이 쌓여 2D 영상이 된다(DEM 서버 쪽 SensorConfig가
    fov_across_deg 하나만 갖고 along-track FOV가 없는 것과 같은 모델). compute_footprint를
    fov_y_deg=0으로 호출하는 특수 케이스로 재사용한다 - along-track 폭이 0이면 네
    모서리가 좌/우 두 쌍으로 겹치므로(corners[0]==corners[3], corners[1]==corners[2]),
    corners[0]/corners[1]이 그대로 이 줄의 좌/우 끝점이 된다. 어느 바디 축이 실제
    across-track(폭 방향)인지는 compute_footprint/camera_rays_ecef와 동일하게
    boresight_body(및 그로부터 유도되는 fov_corner_rays_body의 right/up 축)가 결정하므로,
    호출자가 실제 카메라 마운팅에 맞는 boresight_body를 넘겨야 한다.
    """
    footprint = compute_footprint(
        sat_pos_eci, quaternion_body2eci, utc_datetime, fov_across_deg, 0.0, boresight_body
    )
    corners = footprint.get("corners") or []
    left = corners[0] if len(corners) > 0 else None
    right = corners[1] if len(corners) > 1 else None
    return {"left": left, "right": right, "visible": left is not None and right is not None}


def line_to_geojson(line: dict, properties: dict | None = None) -> dict:
    """line_ground_points() 결과를 GeoJSON FeatureCollection(LineString + 좌/우 Point)으로 변환."""
    props = dict(properties or {})
    features: list[dict] = []

    left, right = line.get("left"), line.get("right")
    if line.get("visible") and left is not None and right is not None:
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [list(left), list(right)]},
                "properties": props,
            }
        )
        for point, role in ((left, "left"), (right, "right")):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": list(point)},
                    "properties": {**props, "role": role},
                }
            )

    return {"type": "FeatureCollection", "features": features}


def line_track_to_czml(
    samples: list[tuple[datetime, dict]],
    *,
    id_prefix: str = "line",
    properties: dict | None = None,
    default_duration_sec: float = 60.0,
    line_rgba: tuple[int, int, int, int] = (0, 255, 255, 255),
    line_width: float = 3.0,
) -> list[dict]:
    """(시각, line_ground_points() 결과) 샘플들을 시간에 따라 전환되는 CZML polyline
    시퀀스로 변환 - footprint_track_to_czml과 동일한 availability 스코핑 방식."""
    props = dict(properties or {})
    czml: list[dict] = [{"id": "document", "version": "1.0"}]

    def _iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")

    for i, (t, line) in enumerate(samples):
        start_dt = t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)
        end_dt = samples[i + 1][0] if i + 1 < len(samples) else start_dt + timedelta(seconds=default_duration_sec)
        availability = f"{_iso(start_dt)}/{_iso(end_dt)}"

        left, right = line.get("left"), line.get("right")
        if not (line.get("visible") and left is not None and right is not None):
            continue

        lon1, lat1 = left
        lon2, lat2 = right
        czml.append(
            {
                "id": f"{id_prefix}_{i}",
                "availability": availability,
                "properties": {**props, "time": _iso(start_dt)},
                "polyline": {
                    "positions": {"cartographicDegrees": [lon1, lat1, 0.0, lon2, lat2, 0.0]},
                    "material": {"solidColor": {"color": {"rgba": list(line_rgba)}}},
                    "width": line_width,
                },
            }
        )

    return czml


def footprint_track_to_czml(
    samples: list[tuple[datetime, dict]],
    *,
    id_prefix: str = "footprint",
    properties: dict | None = None,
    default_duration_sec: float = 60.0,
    polygon_fill_rgba: tuple[int, int, int, int] = (255, 255, 0, 80),
    polygon_outline_rgba: tuple[int, int, int, int] = (255, 255, 0, 255),
    center_point_rgba: tuple[int, int, int, int] = (255, 0, 0, 255),
    center_point_pixel_size: float = 8.0,
) -> list[dict]:
    """(시각, compute_footprint() 결과) 샘플들을 시간에 따라 전환되는 CZML 패킷 시퀀스로 변환.

    CZML 폴리곤의 정점 배열 자체를 시간별로 바꾸는 기능 대신(정의가 번거롭고 지원이
    제한적), core/loader/hk_loader.py의 df_to_czml과 같은 방식으로 매 시점마다 별도
    id의 폴리곤/중심점 패킷을 만들고 `availability`(다음 샘플 시각까지)를 지정한다 -
    Cesium 타임라인이 그 구간을 지날 때만 해당 시점의 풋프린트가 보인다.
    지평선 너머(visible=False)인 샘플은 폴리곤/중심점 패킷 없이 건너뛴다.
    """
    props = dict(properties or {})
    czml: list[dict] = [{"id": "document", "version": "1.0"}]

    def _iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat().replace("+00:00", "Z")

    for i, (t, footprint) in enumerate(samples):
        start_dt = t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)
        end_dt = samples[i + 1][0] if i + 1 < len(samples) else start_dt + timedelta(seconds=default_duration_sec)
        availability = f"{_iso(start_dt)}/{_iso(end_dt)}"
        sample_props = {**props, "time": _iso(start_dt)}

        corners = footprint.get("corners") or []
        valid_corners = [c for c in corners if c is not None]
        if footprint.get("visible") and len(valid_corners) >= 3:
            cartographic_degrees: list[float] = []
            for lon, lat in corners:
                cartographic_degrees.extend([lon, lat, 0.0])
            czml.append(
                {
                    "id": f"{id_prefix}_polygon_{i}",
                    "availability": availability,
                    "properties": sample_props,
                    "polygon": {
                        "positions": {"cartographicDegrees": cartographic_degrees},
                        "material": {"solidColor": {"color": {"rgba": list(polygon_fill_rgba)}}},
                        "outline": True,
                        "outlineColor": {"rgba": list(polygon_outline_rgba)},
                    },
                }
            )

        center = footprint.get("center")
        if center is not None:
            lon, lat = center
            czml.append(
                {
                    "id": f"{id_prefix}_center_{i}",
                    "availability": availability,
                    "properties": {**sample_props, "role": "boresight_center"},
                    "position": {"cartographicDegrees": [lon, lat, 0.0]},
                    "point": {
                        "color": {"rgba": list(center_point_rgba)},
                        "pixelSize": center_point_pixel_size,
                    },
                }
            )

    return czml
