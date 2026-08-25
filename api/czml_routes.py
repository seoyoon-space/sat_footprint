from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from fastapi import APIRouter, HTTPException

from core.coordinates import build_cesium_track_czml, pointing_vector_from_quaternion
from core.loader import HKLoader

from .schemas import TelemetryQueryRequest

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@lru_cache(maxsize=None)
def _get_loader(satellite_id: str) -> HKLoader:
    return HKLoader.for_satellite(satellite_id)


@router.post("/czml")
def czml_telemetry(
    req: TelemetryQueryRequest,
    include_pointing: bool = True,
    id_prefix: str = "sat",
    coordinate_frame: str = "ecef",
):
    """Return Cesium-ready CZML for the requested satellite/time window.

    This output is intentionally structured for direct use in Cesium:
      - `position` uses epoch + cartesian values
      - `orientation` uses epoch + unitQuaternion values
      - when available, a pointing vector is included as time-sampled cartesian values
    """
    try:
        loader = _get_loader(req.satellite_id)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    try:
        df = loader.load(
            start_time=req.start_time.isoformat(),
            end_time=req.end_time.isoformat(),
            satellite_id=None,
            merge_tolerance_sec=req.merge_tolerance_sec,
            interpolate_gaps=req.interpolate_gaps,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Telemetry load failed: {e}") from e

    q_cols = ["q_eci2body_1", "q_eci2body_2", "q_eci2body_3", "q_eci2body_4"]
    if all(c in df.columns for c in q_cols) and include_pointing:
        def quat_to_pointing(q1, q2, q3, q4):
            try:
                return list(pointing_vector_from_quaternion((float(q1), float(q2), float(q3), float(q4))))
            except Exception:
                return None

        df = df.copy()
        df["pointing_eci"] = df.apply(
            lambda r: quat_to_pointing(r.get(q_cols[0]), r.get(q_cols[1]), r.get(q_cols[2]), r.get(q_cols[3])),
            axis=1,
        )

    return build_cesium_track_czml(
        df,
        id_prefix=id_prefix,
        coordinate_frame=coordinate_frame,
        time_col="time",
        position_cols=("pos_eci_x", "pos_eci_y", "pos_eci_z"),
        orientation_cols=("q_eci2body_1", "q_eci2body_2", "q_eci2body_3", "q_eci2body_4"),
        pointing_col="pointing_eci" if include_pointing and "pointing_eci" in df.columns else None,
    )
