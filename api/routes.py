# POST 요청 수신 및 시뮬레이션 파이프라인 호출 라우터

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_api_key
from .loader_cache import get_loader as _get_loader
from .schemas import TelemetryQueryRequest, TelemetryRecord, TelemetryResponse

router = APIRouter(prefix="/telemetry", tags=["telemetry"], dependencies=[Depends(require_api_key)])


@router.post("/query", response_model=TelemetryResponse)
def query_telemetry(req: TelemetryQueryRequest) -> TelemetryResponse:
    """
    지정한 위성/기간의 HK 텔레메트리를 공통 타임스탬프 기준으로 병합 후 반환.
    위성별로 DB 인스턴스가 다르므로 config/satellites.toml에 등록된 satellite_id만 사용 가능.
    """
    try:
        loader = _get_loader(req.satellite_id)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e

    try:
        df = loader.load(
            # HKLoader.load()/_normalize_query_time는 str/datetime을 모두 받으므로 그대로 전달.
            # (예전에는 .isoformat()을 강제 호출해서, pydantic이 ISO 문자열을 str로 파싱한
            # 경우 - datetime|str 유니온에서 흔히 벌어짐 - AttributeError로 항상 실패했음)
            start_time=req.start_time,
            end_time=req.end_time,
            satellite_id=None,  # DB 자체가 위성별로 분리되어 있어 컬럼 필터 불필요
            merge_tolerance_sec=req.merge_tolerance_sec,
            interpolate_gaps=req.interpolate_gaps,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:  # DB 연결/쿼리 오류 등
        raise HTTPException(status_code=500, detail=f"Telemetry load failed: {e}") from e

    records = [TelemetryRecord(**row) for row in df.to_dict(orient="records")]

    return TelemetryResponse(
        satellite_id=req.satellite_id,
        start_time=req.start_time,
        end_time=req.end_time,
        num_records=len(records),
        records=records,
    )