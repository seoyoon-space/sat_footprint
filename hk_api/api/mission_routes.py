# MCE(미션 스케줄링) DB 조회 라우터 - 실제 카메라 ON/OFF 구간(core/mission/mce_db.py) 노출

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.mission.mce_db import get_missions

from .auth import require_api_key
from .schemas import MissionScheduleRequest, MissionScheduleResponse

router = APIRouter(prefix="/mission", tags=["mission"], dependencies=[Depends(require_api_key)])


@router.post("/schedule", response_model=MissionScheduleResponse)
def mission_schedule_endpoint(req: MissionScheduleRequest) -> MissionScheduleResponse:
    """지정 위성/기간의 미션 스케줄을 MCE DB에서 조회해, 각 미션의 실제 카메라 ON/OFF
    구간(scanStart/camStart/camEnd)까지 계산해 반환.
    """
    try:
        missions = get_missions(
            satellite_id=req.satellite_id,
            start_iso=req.start_time.isoformat(),
            end_iso=req.end_time.isoformat(),
        )
    except ValueError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except Exception as e:  # DB 연결/쿼리 오류 등
        raise HTTPException(status_code=500, detail=f"Mission schedule query failed: {e}") from e

    return MissionScheduleResponse(satellite_id=req.satellite_id, num_records=len(missions), missions=missions)
