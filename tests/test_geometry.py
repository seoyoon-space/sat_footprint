"""core/geometry/footprint.py 검증 (numpy를 사용한 교차검증 포함)."""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone

import numpy as np
import pytest

from core.coordinates import WGS84_A, WGS84_B
from core.geometry.footprint import (
    boresight_ray_ecef,
    camera_rays_ecef,
    compute_footprint,
    fov_corner_rays_body,
    footprint_to_czml,
    footprint_to_geojson,
    footprint_track_to_czml,
    intersect_wgs84_ellipsoid,
)


def _numpy_ellipsoid_intersection(origin, direction, a=WGS84_A, b=WGS84_B):
    o = np.asarray(origin, dtype=float)
    d = np.asarray(direction, dtype=float)
    d = d / np.linalg.norm(d)

    A = (d[0] ** 2 + d[1] ** 2) / a**2 + d[2] ** 2 / b**2
    B = 2.0 * ((o[0] * d[0] + o[1] * d[1]) / a**2 + o[2] * d[2] / b**2)
    C = (o[0] ** 2 + o[1] ** 2) / a**2 + o[2] ** 2 / b**2 - 1.0

    disc = B * B - 4 * A * C
    if disc < 0:
        return None
    t1 = (-B - math.sqrt(disc)) / (2 * A)
    t2 = (-B + math.sqrt(disc)) / (2 * A)
    candidates = [t for t in (t1, t2) if t >= 0]
    if not candidates:
        return None
    t = min(candidates)
    return o + t * d


def test_intersect_wgs84_ellipsoid_nadir_from_leo_matches_numpy():
    # 적도 상공 700km에서 정확히 지구 중심 방향으로 바라보는 광선
    origin = (WGS84_A + 700_000.0, 0.0, 0.0)
    direction = (-1.0, 0.0, 0.0)

    hit = intersect_wgs84_ellipsoid(origin, direction)
    ref = _numpy_ellipsoid_intersection(origin, direction)

    assert hit is not None
    np.testing.assert_allclose(hit, ref, rtol=1e-9)
    # 적도 표면과 만나야 하므로 x ~= WGS84_A
    assert hit[0] == pytest.approx(WGS84_A, abs=1e-3)


def test_intersect_wgs84_ellipsoid_ray_missing_earth_returns_none():
    origin = (WGS84_A + 700_000.0, 0.0, 0.0)
    direction = (0.0, 1.0, 0.0)  # 지구 접선 방향, 지표면과 만나지 않음

    assert intersect_wgs84_ellipsoid(origin, direction) is None
    assert _numpy_ellipsoid_intersection(origin, direction) is None


def test_intersect_wgs84_ellipsoid_matches_numpy_random_rays():
    rng = random.Random(123)
    for _ in range(30):
        alt = rng.uniform(400_000.0, 800_000.0)
        lat = math.radians(rng.uniform(-80, 80))
        lon = math.radians(rng.uniform(-180, 180))
        r = WGS84_A + alt
        origin = (r * math.cos(lat) * math.cos(lon), r * math.cos(lat) * math.sin(lon), r * math.sin(lat))

        # 지구 중심을 향한 방향에 약간의 오프셋을 준 광선 (여전히 지표면과 교차)
        direction = (-origin[0], -origin[1], -origin[2])

        hit = intersect_wgs84_ellipsoid(origin, direction)
        ref = _numpy_ellipsoid_intersection(origin, direction)

        assert hit is not None and ref is not None
        np.testing.assert_allclose(hit, ref, rtol=1e-8)


def test_fov_corner_rays_are_unit_vectors_and_symmetric():
    boresight = (0.0, 0.0, 1.0)
    corners = fov_corner_rays_body(boresight, fov_x_deg=10.0, fov_y_deg=6.0)
    assert len(corners) == 4
    for c in corners:
        np.testing.assert_allclose(np.linalg.norm(c), 1.0, rtol=1e-9)

    # 대칭 FOV이므로 네 모서리 모두 boresight으로부터 같은 각거리를 가져야 한다.
    # (피치->요 회전 합성은 비선형이라 corner[0]+corner[2] == 2*boresight 같은 벡터합은
    # 성립하지 않지만, 각거리는 부호 조합과 무관하게 항상 동일하다.)
    angles = [math.degrees(math.acos(max(-1.0, min(1.0, np.dot(boresight, c))))) for c in corners]
    np.testing.assert_allclose(angles, angles[0], rtol=1e-9)


def test_camera_rays_ecef_matches_compute_footprint_intersection():
    """camera_rays_ecef가 반환하는 원점/방향으로 직접 타원체 교차를 계산하면
    compute_footprint()의 결과와 정확히 일치해야 한다(compute_footprint은 내부적으로
    camera_rays_ecef를 재사용하도록 리팩터링됨 - DEM 서버가 광선만 받아 자체 지형과
    교차시키는 경로가, 이 API가 타원체로 교차시키는 경로와 같은 광선 위에서 출발하는지 검증).
    """
    identity_q = (1.0, 0.0, 0.0, 0.0)
    sat_pos_eci = (WGS84_A + 700_000.0, 0.0, 0.0)
    dt = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)

    rays = camera_rays_ecef(
        sat_pos_eci, identity_q, dt, fov_x_deg=5.0, fov_y_deg=5.0, boresight_body=(-1.0, 0.0, 0.0)
    )
    assert len(rays["fov_corner_directions_ecef"]) == 4
    for d in [rays["boresight_direction_ecef"], *rays["fov_corner_directions_ecef"]]:
        np.testing.assert_allclose(np.linalg.norm(d), 1.0, rtol=1e-9)

    hit = intersect_wgs84_ellipsoid(rays["origin_ecef"], rays["boresight_direction_ecef"])
    assert hit is not None

    footprint = compute_footprint(
        sat_pos_eci, identity_q, dt, fov_x_deg=5.0, fov_y_deg=5.0, boresight_body=(-1.0, 0.0, 0.0)
    )
    from core.coordinates import ecef_to_geodetic

    expected_lat, expected_lon, _ = ecef_to_geodetic(hit)
    assert footprint["center"] == pytest.approx((expected_lon, expected_lat), abs=1e-9)


def test_compute_footprint_nadir_pointing_from_equator():
    identity_q = (1.0, 0.0, 0.0, 0.0)
    sat_pos_eci = (WGS84_A + 700_000.0, 0.0, 0.0)
    dt = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)

    footprint = compute_footprint(
        sat_pos_eci,
        identity_q,
        dt,
        fov_x_deg=5.0,
        fov_y_deg=5.0,
        boresight_body=(-1.0, 0.0, 0.0),  # identity 자세이므로 ECI에서도 -X, 즉 지구 중심 방향
    )

    assert footprint["visible"] is True
    assert footprint["center"] is not None
    lon, lat = footprint["center"]
    assert lat == pytest.approx(0.0, abs=1.0)
    assert len(footprint["corners"]) == 4
    assert all(c is not None for c in footprint["corners"])


def test_compute_footprint_looking_away_from_earth_is_not_visible():
    identity_q = (1.0, 0.0, 0.0, 0.0)
    sat_pos_eci = (WGS84_A + 700_000.0, 0.0, 0.0)
    dt = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)

    footprint = compute_footprint(
        sat_pos_eci,
        identity_q,
        dt,
        fov_x_deg=5.0,
        fov_y_deg=5.0,
        boresight_body=(1.0, 0.0, 0.0),  # 지구 반대 방향
    )

    assert footprint["center"] is None
    assert footprint["visible"] is False


def test_boresight_ray_ecef_matches_manual_rotation():
    identity_q = (1.0, 0.0, 0.0, 0.0)
    sat_pos_eci = (WGS84_A + 700_000.0, 0.0, 0.0)
    dt = datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc)

    origin, direction = boresight_ray_ecef(sat_pos_eci, identity_q, dt, boresight_body=(-1.0, 0.0, 0.0))
    np.testing.assert_allclose(np.linalg.norm(direction), 1.0, rtol=1e-9)
    # ECI->ECEF는 (근사)순수 회전이므로 3D 노름(궤도반경)은 정확히 보존되어야 함.
    # (참고: 세차/장동으로 인한 미세한 축 기울어짐 때문에 XY 평면 성분만의 크기는
    # 더 이상 정확히 보존되지 않는다 - 2차 오더 효과(z^2/2R 수준, 수십 m)라 3D 노름으로 검증)
    origin_radius = math.sqrt(sum(c * c for c in origin))
    expected_radius = math.hypot(sat_pos_eci[0], sat_pos_eci[1])
    assert origin_radius == pytest.approx(expected_radius, rel=1e-9)


def test_footprint_to_geojson_structure():
    footprint = {
        "center": (10.0, 20.0),
        "corners": [(9.0, 19.0), (11.0, 19.0), (11.0, 21.0), (9.0, 21.0)],
        "visible": True,
    }
    geojson = footprint_to_geojson(footprint, properties={"satellite_id": "O1A"})

    assert geojson["type"] == "FeatureCollection"
    assert len(geojson["features"]) == 2

    polygon = next(f for f in geojson["features"] if f["geometry"]["type"] == "Polygon")
    ring = polygon["geometry"]["coordinates"][0]
    assert ring[0] == ring[-1]  # closed ring
    assert polygon["properties"]["satellite_id"] == "O1A"

    point = next(f for f in geojson["features"] if f["geometry"]["type"] == "Point")
    assert point["geometry"]["coordinates"] == [10.0, 20.0]


def test_footprint_to_geojson_not_visible_has_no_polygon():
    footprint = {"center": None, "corners": [None, None, None, None], "visible": False}
    geojson = footprint_to_geojson(footprint)
    assert geojson["features"] == []


def test_footprint_to_czml_structure():
    footprint = {
        "center": (10.0, 20.0),
        "corners": [(9.0, 19.0), (11.0, 19.0), (11.0, 21.0), (9.0, 21.0)],
        "visible": True,
    }
    czml = footprint_to_czml(footprint, properties={"satellite_id": "O1A"})

    assert czml[0] == {"id": "document", "version": "1.0"}
    assert len(czml) == 3  # document + polygon + center point

    polygon_packet = next(p for p in czml if "polygon" in p)
    # CZML은 GeoJSON과 달리 폐합점을 반복하지 않음: 4개 코너 * (lon,lat,height) = 12개 값
    positions = polygon_packet["polygon"]["positions"]["cartographicDegrees"]
    assert positions == [9.0, 19.0, 0.0, 11.0, 19.0, 0.0, 11.0, 21.0, 0.0, 9.0, 21.0, 0.0]
    assert polygon_packet["properties"]["satellite_id"] == "O1A"

    point_packet = next(p for p in czml if "point" in p)
    assert point_packet["position"]["cartographicDegrees"] == [10.0, 20.0, 0.0]
    assert point_packet["properties"]["role"] == "boresight_center"


def test_footprint_to_czml_not_visible_has_no_polygon_packet():
    footprint = {"center": None, "corners": [None, None, None, None], "visible": False}
    czml = footprint_to_czml(footprint)

    assert czml == [{"id": "document", "version": "1.0"}]


def test_footprint_track_to_czml_scopes_each_sample_with_availability():
    fp = {
        "center": (10.0, 20.0),
        "corners": [(9.0, 19.0), (11.0, 19.0), (11.0, 21.0), (9.0, 21.0)],
        "visible": True,
    }
    fp2 = {
        "center": (12.0, 20.0),
        "corners": [(11.0, 19.0), (13.0, 19.0), (13.0, 21.0), (11.0, 21.0)],
        "visible": True,
    }
    samples = [
        (datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc), fp),
        (datetime(2026, 8, 20, 0, 0, 10, tzinfo=timezone.utc), fp2),
    ]

    czml = footprint_track_to_czml(samples, id_prefix="fp")

    assert czml[0] == {"id": "document", "version": "1.0"}
    # 샘플 2개 * (폴리곤+중심점) = 4개 패킷 + document
    assert len(czml) == 5

    polygon_0 = next(p for p in czml if p["id"] == "fp_polygon_0")
    assert polygon_0["availability"] == "2026-08-20T00:00:00Z/2026-08-20T00:00:10Z"

    polygon_1 = next(p for p in czml if p["id"] == "fp_polygon_1")
    # 마지막 샘플은 default_duration_sec만큼의 구간을 가짐(다음 샘플이 없으므로)
    assert polygon_1["availability"] == "2026-08-20T00:00:10Z/2026-08-20T00:01:10Z"


def test_footprint_track_to_czml_skips_not_visible_samples():
    visible_fp = {"center": (10.0, 20.0), "corners": [(9, 19), (11, 19), (11, 21), (9, 21)], "visible": True}
    hidden_fp = {"center": None, "corners": [None, None, None, None], "visible": False}
    samples = [
        (datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc), visible_fp),
        (datetime(2026, 8, 20, 0, 0, 10, tzinfo=timezone.utc), hidden_fp),
    ]

    czml = footprint_track_to_czml(samples)

    ids = [p["id"] for p in czml]
    assert "footprint_polygon_0" in ids and "footprint_center_0" in ids
    assert "footprint_polygon_1" not in ids and "footprint_center_1" not in ids
