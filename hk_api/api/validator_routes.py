# 정착시간/휠포화 평가(운영 규칙 엔진, core/validator/ops_rules.py) 라우터

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.validator.ops_rules import (
    OpsStatusReport,
    SettlingResult,
    WheelSaturationReport,
    evaluate_ops_status,
    evaluate_settling_time,
    scan_wheel_saturation,
)

from .auth import require_api_key
from .loader_cache import get_loader as _get_loader
from .schemas import (
    OpsStatusRequest,
    OpsStatusResponse,
    SaturationEventSchema,
    SettlingResultSchema,
    WheelSaturationReportSchema,
)

router = APIRouter(prefix="/validator", tags=["validator"], dependencies=[Depends(require_api_key)])

WHEEL_SPEED_COLUMNS = ["filt_speed_rpm1", "filt_speed_rpm2", "filt_speed_rpm3"]


def _settling_schema(r: SettlingResult) -> SettlingResultSchema:
    return SettlingResultSchema(
        settled=r.settled,
        settling_time=r.settling_time,
        settling_timestamp=r.settling_timestamp,
        final_error=r.final_error,
        status=r.status.name,
    )


def _wheel_schema(r: WheelSaturationReport) -> WheelSaturationReportSchema:
    return WheelSaturationReportSchema(
        events=[
            SaturationEventSchema(
                channel=e.channel, timestamp=e.timestamp, value=e.value, ratio=e.ratio, status=e.status.name
            )
            for e in r.events
        ],
        max_ratio_by_channel=r.max_ratio_by_channel,
        status=r.status.name,
    )


@router.post("/ops-status", response_model=OpsStatusResponse)
def evaluate_ops_status_endpoint(req: OpsStatusRequest) -> OpsStatusResponse:
    """지정 위성/기간의 HK 텔레메트리를 로드해 정착시간/휠포화를 평가하고 PASS/WARN/FAIL로 종합.

    settling_tolerance_deg/wheel_max_rpm을 둘 다 생략하면 평가 항목이 없어 항상
    PASS(사유 없음)가 반환된다 - 최소 하나는 지정해야 의미 있는 결과를 얻는다.
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
            # satellite_id가 hk1~hk6 테이블 접두어(tbl_obs1a_hk*/tbl_obs1b_hk*)를 결정한다.
            satellite_id=req.satellite_id,
            merge_tolerance_sec=req.merge_tolerance_sec,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Telemetry load failed: {e}") from e

    if df.empty:
        raise HTTPException(status_code=404, detail="No telemetry data found in the requested range.")

    times = df["time"].apply(lambda t: t.timestamp()).tolist()

    settling_obj: SettlingResult | None = None
    wheel_obj: WheelSaturationReport | None = None

    if req.settling_tolerance_deg is not None:
        if "eigen_err" not in df.columns:
            raise HTTPException(status_code=404, detail="'eigen_err' column not available for settling evaluation")
        settling_obj = evaluate_settling_time(
            times,
            df["eigen_err"].tolist(),
            tolerance=req.settling_tolerance_deg,
            hold_duration=req.settling_hold_duration_sec,
            warn_multiplier=req.settling_warn_multiplier,
        )

    if req.wheel_max_rpm is not None:
        wheel_cols = [c for c in WHEEL_SPEED_COLUMNS if c in df.columns]
        if not wheel_cols:
            raise HTTPException(status_code=404, detail="No wheel speed columns available for saturation evaluation")
        wheel_speeds = {c: df[c].tolist() for c in wheel_cols}
        wheel_obj = scan_wheel_saturation(
            times, wheel_speeds, max_rpm=req.wheel_max_rpm, warn_ratio=req.wheel_warn_ratio
        )

    report: OpsStatusReport = evaluate_ops_status(settling=settling_obj, wheel_saturation=wheel_obj)

    return OpsStatusResponse(
        satellite_id=req.satellite_id,
        status=report.status.name,
        reasons=report.reasons,
        settling=_settling_schema(settling_obj) if settling_obj is not None else None,
        wheel_saturation=_wheel_schema(wheel_obj) if wheel_obj is not None else None,
    )
