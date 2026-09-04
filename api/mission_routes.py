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
    구간(scanStart/camStart/camEnd)까지 계산해 반환한다.

    HK DB(텔레메트리)와는 별개의 DB(MCE_DB_* 환경변수)를 쓴다 - 미리보기/스케줄링
    상태가 아니라, 이 DB의 실측 스케줄 데이터를 그대로 반환한다는 점에서 EP 서버의
    HTTP API(GET /api/Mission/selected)보다 신뢰할 수 있는 경우가 있었다(EP HTTP API가
    일부 scheduleId에 대해 stale한 위경도/상태를 반환하는 것이 실측으로 확인됨 - DEM
    서버 쪽에서 이 DB를 직접 읽도록 전환한 이유).
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
