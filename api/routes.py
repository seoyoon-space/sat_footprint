# POST 요청 수신 및 시뮬레이션 파이프라인 호출 라우터

from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, HTTPException

from core.loader import HKLoader
from core.loader.hk_loader import df_to_czml

from .schemas import TelemetryQueryRequest, TelemetryRecord, TelemetryResponse

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@lru_cache(maxsize=None)
def _get_loader(satellite_id: str) -> HKLoader:
    """위성별 DB 커넥션(Engine)을 재사용하기 위한 캐시."""
    return HKLoader.for_satellite(satellite_id)


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
            start_time=req.start_time.isoformat(),
            end_time=req.end_time.isoformat(),
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