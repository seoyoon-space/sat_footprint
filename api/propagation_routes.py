# TLE(SGP4) 기반 궤도 전파 라우터 (DB 조회 없이 stateless 계산만 수행)

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException

from core.coordinates import build_cesium_track_czml
from core.propagation import TleError, load_tle, propagate

from .auth import require_api_key
from .schemas import PropagationSample, PropagationTrackRequest, PropagationTrackResponse

router = APIRouter(prefix="/propagation", tags=["propagation"], dependencies=[Depends(require_api_key)])

MAX_SAMPLES = 5000  # 과도하게 촘촘한/긴 요청으로 인한 부하 방지


def _propagate_track(req: PropagationTrackRequest) -> list[PropagationSample]:
    if req.end_time <= req.start_time:
        raise HTTPException(status_code=400, detail="end_time must be after start_time")

    span_sec = (req.end_time - req.start_time).total_seconds()
    n = int(span_sec // req.step_sec) + 1
    if n > MAX_SAMPLES:
        raise HTTPException(
            status_code=400,
            detail=f"Requested {n} samples exceeds the limit of {MAX_SAMPLES}; widen step_sec or narrow the time range.",
        )

    try:
        satrec = load_tle(req.tle_line1, req.tle_line2)
    except TleError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    samples: list[PropagationSample] = []
    for i in range(n):
        t = req.start_time + timedelta(seconds=i * req.step_sec)
        try:
            state = propagate(satrec, t)
        except TleError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        samples.append(
            PropagationSample(time=t, position_km=state.position_km, velocity_km_s=state.velocity_km_s)
        )
    return samples


@router.post("/track", response_model=PropagationTrackResponse)
def propagation_track_endpoint(req: PropagationTrackRequest) -> PropagationTrackResponse:
    """TLE로부터 지정 구간을 SGP4로 전파해 시점별 TEME(~=ECI) 위치/속도를 반환.

    실측 텔레메트리와 무관한 순수 예측값이다 - 설계/사전계획 단계의 예상 궤도가
    필요할 때, 또는 HK 텔레메트리 위치와 대조해 궤도전파 정확도를 교차검증할 때 쓴다.
    """
    samples = _propagate_track(req)
    return PropagationTrackResponse(num_records=len(samples), samples=samples)


@router.post("/track/czml")
def propagation_track_czml_endpoint(req: PropagationTrackRequest) -> list:
    """같은 전파 결과를 Cesium CZML(position만, 자세 없음)로 반환."""
    import pandas as pd

    samples = _propagate_track(req)
    df = pd.DataFrame(
        {
            "time": [pd.Timestamp(s.time, tz="UTC") if s.time.tzinfo is None else pd.Timestamp(s.time) for s in samples],
            "pos_x": [s.position_km[0] * 1000.0 for s in samples],
            "pos_y": [s.position_km[1] * 1000.0 for s in samples],
            "pos_z": [s.position_km[2] * 1000.0 for s in samples],
        }
    )
    return build_cesium_track_czml(
        df,
        id_prefix="propagated",
        coordinate_frame="ecef",
        position_cols=("pos_x", "pos_y", "pos_z"),
        orientation_cols=("__none1__", "__none2__", "__none3__", "__none4__"),
        pointing_col=None,
    )
