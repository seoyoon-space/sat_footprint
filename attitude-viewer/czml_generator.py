"""CZML generator for satellite attitude visualization.

Converts mission HK data (ECI position + body-to-ECI quaternion) into CZML
documents for Cesium 3D globe rendering.

Required HK data format (dict):
    taiSeconds   : array of Unix timestamps (float, seconds)
    posWrtEci1   : array of X position in ECI frame (km)
    posWrtEci2   : array of Y position in ECI frame (km)
    posWrtEci3   : array of Z position in ECI frame (km)
    qbodyWrtEci1 : array of quaternion X (ECI-to-Body)
    qbodyWrtEci2 : array of quaternion Y (ECI-to-Body)
    qbodyWrtEci3 : array of quaternion Z (ECI-to-Body)
    qbodyWrtEci4 : array of quaternion W (ECI-to-Body)

Ported from hk-dashboard: dashboard_web/dash/cesium/czml_generator.py
"""

from datetime import datetime, timezone

import numpy as np


def _to_float(value):
    try:
        if value is None:
            return None
        val = float(value)
        if np.isnan(val) or np.isinf(val):
            return None
        return val
    except (TypeError, ValueError):
        return None


def quaternion_to_rotation_matrix(q):
    x, y, z, w = q
    return np.array([
        [1 - 2*(y**2 + z**2), 2*(x*y - w*z), 2*(x*z + w*y)],
        [2*(x*y + w*z), 1 - 2*(x**2 + z**2), 2*(y*z - w*x)],
        [2*(x*z - w*y), 2*(y*z + w*x), 1 - 2*(x**2 + y**2)]
    ])


def quaternion_conjugate(q):
    """Flip ECI->Body quaternion to Body->ECI for visualization."""
    x, y, z, w = q
    return (-x, -y, -z, w)


def generate_fov_pyramid_eci(q_body, max_view_angle=30):
    try:
        R = quaternion_to_rotation_matrix(q_body)
        half_angle_rad = np.radians(max_view_angle)
        offset = np.tan(half_angle_rad)

        directions_body = np.array([
            [ offset,  offset, -1.0],
            [ offset, -offset, -1.0],
            [-offset, -offset, -1.0],
            [-offset,  offset, -1.0],
        ])

        norm = np.linalg.norm(directions_body, axis=1, keepdims=True)
        norm[norm == 0] = 1e-9
        directions_body = directions_body / norm

        directions_eci = np.dot(R, directions_body.T).T
        return directions_eci.flatten().tolist()
    except Exception:
        return []


def generate_czml(
    mission_hk,
    fov_angle=30,
    show_fov=True,
    show_axes=True,
    show_ground_track=False,
    target_coords=None,
):
    """Generate CZML document from mission HK data.

    Args:
        mission_hk: dict with taiSeconds, posWrtEci1-3, qbodyWrtEci1-4
        fov_angle: FOV half-angle in degrees (for CZML pyramid)
        show_fov: include FOV pyramid in CZML
        show_axes: include body axes (RGB arrows, 500km) in CZML
        show_ground_track: unused, kept for API compat
        target_coords: list of {"name", "lat", "lon"} dicts for ground markers

    Returns:
        list of CZML packet dicts
    """
    if not mission_hk or "taiSeconds" not in mission_hk or len(mission_hk["taiSeconds"]) == 0:
        return []

    try:
        start_ts = _to_float(mission_hk["taiSeconds"][0])
        end_ts = _to_float(mission_hk["taiSeconds"][-1])
        if start_ts is None or end_ts is None:
            return []
    except (ValueError, TypeError, IndexError):
        return []

    start_time = datetime.fromtimestamp(start_ts, tz=timezone.utc)
    end_time = datetime.fromtimestamp(end_ts, tz=timezone.utc)

    epoch = start_time.isoformat().replace("+00:00", "Z")
    availability = f"{epoch}/{end_time.isoformat().replace('+00:00', 'Z')}"

    total_samples = len(mission_hk["taiSeconds"])
    target_count = 3000
    step = max(1, int(total_samples / target_count))
    sample_indices = list(range(0, total_samples, step))

    cartesian = []
    orientation = []
    fov_directions_flat = []

    axis_x_samples = []
    axis_y_samples = []
    axis_z_samples = []
    axis_len = 500000.0  # 500km

    has_valid_data = False

    for i in sample_indices:
        try:
            raw_dt = _to_float(mission_hk["taiSeconds"][i])
            raw_x = _to_float(mission_hk["posWrtEci1"][i])
            raw_y = _to_float(mission_hk["posWrtEci2"][i])
            raw_z = _to_float(mission_hk["posWrtEci3"][i])

            raw_qx = _to_float(mission_hk["qbodyWrtEci1"][i])
            raw_qy = _to_float(mission_hk["qbodyWrtEci2"][i])
            raw_qz = _to_float(mission_hk["qbodyWrtEci3"][i])
            raw_qw = _to_float(mission_hk["qbodyWrtEci4"][i])

            if None in [raw_dt, raw_x, raw_y, raw_z]:
                continue

            if None in [raw_qx, raw_qy, raw_qz, raw_qw]:
                qx, qy, qz, qw = 0.0, 0.0, 0.0, 1.0
            else:
                norm = np.sqrt(raw_qx**2 + raw_qy**2 + raw_qz**2 + raw_qw**2)
                if norm > 1e-9:
                    qx, qy, qz, qw = raw_qx/norm, raw_qy/norm, raw_qz/norm, raw_qw/norm
                else:
                    qx, qy, qz, qw = 0.0, 0.0, 0.0, 1.0

            qx, qy, qz, qw = quaternion_conjugate((qx, qy, qz, qw))

            dt = raw_dt - start_ts
            x, y, z = raw_x * 1000.0, raw_y * 1000.0, raw_z * 1000.0  # km -> m

            cartesian.extend([dt, x, y, z])
            orientation.extend([dt, qx, qy, qz, qw])

            if show_axes:
                R = quaternion_to_rotation_matrix((qx, qy, qz, qw))
                pos_vec = np.array([x, y, z])

                vec_x = np.array([axis_len, 0, 0])
                vec_y = np.array([0, axis_len, 0])
                vec_z = np.array([0, 0, axis_len])

                tip_x = np.dot(R, vec_x) + pos_vec
                tip_y = np.dot(R, vec_y) + pos_vec
                tip_z = np.dot(R, vec_z) + pos_vec

                axis_x_samples.extend([dt, tip_x[0], tip_x[1], tip_x[2]])
                axis_y_samples.extend([dt, tip_y[0], tip_y[1], tip_y[2]])
                axis_z_samples.extend([dt, tip_z[0], tip_z[1], tip_z[2]])

            if show_fov:
                fov_dirs = generate_fov_pyramid_eci((qx, qy, qz, qw), fov_angle)
                if fov_dirs:
                    fov_directions_flat.extend([dt] + fov_dirs)

            has_valid_data = True

        except Exception:
            continue

    doc = [
        {
            "id": "document",
            "name": "Satellite Scenario",
            "version": "1.0",
            "clock": {
                "interval": availability,
                "currentTime": epoch,
                "multiplier": 60,
                "range": "LOOP_STOP",
                "step": "SYSTEM_CLOCK_MULTIPLIER",
            },
        }
    ]

    if has_valid_data:
        doc.append({
            "id": "satellite",
            "name": "Satellite Body",
            "availability": availability,
            "position": {
                "interpolationAlgorithm": "LINEAR",
                "interpolationDegree": 1,
                "referenceFrame": "INERTIAL",
                "epoch": epoch,
                "cartesian": cartesian
            },
            "orientation": {
                "interpolationAlgorithm": "LINEAR",
                "interpolationDegree": 1,
                "epoch": epoch,
                "unitQuaternion": orientation
            },
            "model": {"show": True, "scale": 1.0},
            "point": {
                "color": {"rgba": [0, 255, 255, 255]},
                "pixelSize": 10,
                "outlineColor": {"rgba": [0, 0, 0, 255]},
                "outlineWidth": 1
            },
            "path": {
                "material": {"solidColor": {"color": {"rgba": [255, 255, 0, 150]}}},
                "width": 2,
                "leadTime": 0,
                "trailTime": 5000,
                "resolution": 60
            }
        })

        if show_axes and axis_x_samples:
            def add_axis_entity(axis_name, samples, color_rgba):
                tip_id = f"{axis_name}_tip"
                doc.append({
                    "id": tip_id,
                    "availability": availability,
                    "position": {
                        "interpolationAlgorithm": "LINEAR",
                        "interpolationDegree": 1,
                        "referenceFrame": "INERTIAL",
                        "epoch": epoch,
                        "cartesian": samples
                    }
                })
                doc.append({
                    "id": f"{axis_name}_line",
                    "name": f"{axis_name.upper()} Axis",
                    "polyline": {
                        "positions": {
                            "references": ["satellite#position", f"{tip_id}#position"]
                        },
                        "width": 10,
                        "arcType": "NONE",
                        "material": {
                            "polylineArrow": {
                                "color": {"rgba": color_rgba}
                            }
                        }
                    }
                })

            add_axis_entity("axis_x", axis_x_samples, [255, 50, 50, 255])
            add_axis_entity("axis_y", axis_y_samples, [50, 255, 50, 255])
            add_axis_entity("axis_z", axis_z_samples, [50, 50, 255, 255])

        if show_fov and fov_directions_flat:
            doc.append({
                "id": "sensor_fov",
                "parent": "satellite",
                "position": {"reference": "satellite#position"},
                "pyramid": {
                    "show": True,
                    "directions": {
                        "epoch": epoch,
                        "unitVector": fov_directions_flat,
                        "interpolationAlgorithm": "LINEAR",
                        "interpolationDegree": 1
                    },
                    "radius": 2000000.0,
                    "material": {"solidColor": {"color": {"rgba": [0, 255, 255, 40]}}},
                    "outline": True,
                    "outlineColor": {"rgba": [0, 255, 255, 255]},
                    "outlineWidth": 2
                }
            })

    if target_coords:
        for i, tgt in enumerate(target_coords):
            lat = _to_float(tgt.get("lat"))
            lon = _to_float(tgt.get("lon"))
            name = str(tgt.get("name", f"Target {i + 1}")).strip() or f"Target {i + 1}"
            if lat is None or lon is None:
                continue
            doc.append({
                "id": f"target_{i}",
                "name": name,
                "position": {
                    "cartographicDegrees": [lon, lat, 0]
                },
                "point": {
                    "color": {"rgba": [255, 200, 0, 255]},
                    "pixelSize": 12,
                    "outlineColor": {"rgba": [255, 255, 255, 255]},
                    "outlineWidth": 2
                },
                "label": {
                    "text": name,
                    "font": "bold 12pt monospace",
                    "style": "FILL_AND_OUTLINE",
                    "fillColor": {"rgba": [255, 255, 255, 255]},
                    "outlineColor": {"rgba": [0, 0, 0, 255]},
                    "outlineWidth": 2,
                    "verticalOrigin": "BOTTOM",
                    "pixelOffset": {"cartesian2": [0, -16]}
                }
            })

    return doc


def build_mission_hk(utc_timestamps, pos_eci_km, q_body_wrt_eci):
    """Convenience: build the mission_hk dict from arrays.

    Args:
        utc_timestamps: Unix timestamps (float seconds) or pandas DatetimeIndex
        pos_eci_km: (N, 3) array of ECI position in km
        q_body_wrt_eci: (N, 3) or (N, 4) array of ECI-to-Body quaternion [x, y, z, w]

    Returns:
        dict ready for generate_czml()
    """
    import pandas as pd

    if isinstance(utc_timestamps, pd.DatetimeIndex):
        tai_seconds = np.asarray(utc_timestamps.astype("int64") / 1e9, dtype=float)
    else:
        tai_seconds = np.asarray(utc_timestamps, dtype=float)

    pos = np.asarray(pos_eci_km)
    quat = np.asarray(q_body_wrt_eci)

    return {
        "taiSeconds": tai_seconds,
        "posWrtEci1": pos[:, 0],
        "posWrtEci2": pos[:, 1],
        "posWrtEci3": pos[:, 2],
        "qbodyWrtEci1": quat[:, 0],
        "qbodyWrtEci2": quat[:, 1],
        "qbodyWrtEci3": quat[:, 2],
        "qbodyWrtEci4": quat[:, 3],
    }
