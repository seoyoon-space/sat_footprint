from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.coordinates import build_cesium_track_czml, pointing_vector_from_quaternion

from .auth import require_api_key
from .loader_cache import get_loader as _get_loader
from .schemas import TelemetryQueryRequest

router = APIRouter(prefix="/telemetry", tags=["telemetry"], dependencies=[Depends(require_api_key)])


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
            start_time=req.start_time,
            end_time=req.end_time,
            satellite_id=None,
            merge_tolerance_sec=req.merge_tolerance_sec,
            interpolate_gaps=req.interpolate_gaps,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Telemetry load failed: {e}") from e

    # core/loader/schema_map.py의 canonical 필드명(실제 O1B HK2 스키마 기준: qbodyWrtEci1..4 -> snake_case)
    q_cols = ["qbody_wrt_eci1", "qbody_wrt_eci2", "qbody_wrt_eci3", "qbody_wrt_eci4"]
    if all(c in df.columns for c in q_cols) and include_pointing:
        # df.apply(axis=1)은 행마다 Series를 새로 만들어 느리므로(core/loader/hk_loader.py의
        # df_to_czml, core/coordinates.py의 build_cesium_track_czml과 동일한 이유로),
        # 원본 dtype을 보존하는 itertuples로 순회한다.
        pointing_values = []
        for q1, q2, q3, q4 in df[q_cols].itertuples(index=False, name=None):
            try:
                pointing_values.append(list(pointing_vector_from_quaternion((float(q1), float(q2), float(q3), float(q4)))))
            except Exception:
                pointing_values.append(None)

        df = df.copy()
        df["pointing_eci"] = pointing_values

    return build_cesium_track_czml(
        df,
        id_prefix=id_prefix,
        coordinate_frame=coordinate_frame,
        time_col="time",
        position_cols=("pos_wrt_eci1", "pos_wrt_eci2", "pos_wrt_eci3"),
        orientation_cols=tuple(q_cols),
        pointing_col="pointing_eci" if include_pointing and "pointing_eci" in df.columns else None,
    )
