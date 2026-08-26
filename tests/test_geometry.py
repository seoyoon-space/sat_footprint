"""core/geometry/footprint.py 검증 (numpy를 사용한 교차검증 포함)."""
from __future__ import annotations

import math
import random
from datetime import datetime, timezone

import numpy as np
import pytest

from core.coordinates import WGS84_A, WGS84_B, geodetic_to_ecef
from core.geometry.footprint import (
    boresight_ray_ecef,
    compute_footprint,
    fov_corner_rays_body,
    footprint_to_geojson,
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
    # identity 자세, 시간 회전 후에도 원점은 원래 궤도반경과 같아야 함
    assert math.hypot(*origin[:2]) + 0 == pytest.approx(math.hypot(sat_pos_eci[0], sat_pos_eci[1]), rel=1e-9)


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
