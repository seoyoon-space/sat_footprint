# POST 요청 수신 및 시뮬레이션 파이프라인 호출 라우터

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .auth import require_api_key
from .loader_cache import get_loader as _get_loader
from .schemas import MissionHkResponse, TelemetryQueryRequest, TelemetryRecord, TelemetryResponse

_MISSION_HK_COLS = [
    "pos_wrt_eci1",
    "pos_wrt_eci2",
    "pos_wrt_eci3",
    "qbody_wrt_eci1",
    "qbody_wrt_eci2",
    "qbody_wrt_eci3",
    "qbody_wrt_eci4",
]

router = APIRouter(prefix="/telemetry", tags=["telemetry"], dependencies=[Depends(require_api_key)])


@router.post("/query", response_model=TelemetryResponse)
def query_telemetry(req: TelemetryQueryRequest) -> TelemetryResponse:
    """
    지정한 위성/기간의 HK 텔레메트리를 공통 타임스탬프 기준으로 병합 후 반환.
    config/satellites.toml에 등록된 satellite_id만 사용 가능(O1A/O1B는 같은 DB를
    공유하지만 hk1~hk6 테이블 접두어가 satellite_id에 따라 달라짐).
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
            # satellite_id는 위성별 hk1~hk6 테이블 선택 시 시용(tbl_obs1a_hk*/tbl_obs1b_hk*) -
            # None으로 넘기면 항상 O1A 테이블을 조회해버리므로 반드시 실제 값을 전달 필요.
            satellite_id=req.satellite_id,
            merge_tolerance_sec=req.merge_tolerance_sec,
            interpolate_gaps=req.interpolate_gaps,
            invert_quaternion_direction=req.invert_quaternion_direction,
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


@router.post("/mission-hk", response_model=MissionHkResponse)
def mission_hk_telemetry(req: TelemetryQueryRequest) -> MissionHkResponse:
    """지정 위성/기간의 실측 위치+자세를 DEM 서버 czml_generator.py의 `mission_hk` dict와
    같은 필드명(camelCase)·컬럼별 배열 형태로 반환. `posWrtEci1..3`은 미터(그대로).

    `qbodyWrtEci1..4`는 scalar-last(1=x, 2=y, 3=z, 4=w) 순서에 이 프로젝트의 쿼터니언
    "방향반전"은 걸지 않은 값이다 - `core/coordinates.py` 등 이 프로젝트 자신의 계산이
    쓰는 Body->ECI 방향이 **아니다**. DEM 서버(sat_footprint)의 Orekit/Rugged 지형교차
    파이프라인을 실제 타겟 좌표로 A/B 검증한 결과, 이 방향반전이 걸리면 Rugged
    지형교차가 타임아웃/메모리 폭주로 깨지고, 안 걸리면 실제 타겟과 ~1.8km까지
    근접한다는 것이 확인됐다 - 자체 좌표계산이 필요한 소비자를 위한 엔드포인트라
    `invert_quaternion_direction=False`로 로드한다(HKLoader.load 참고). DEM의 CZML
    시각화 자체는 현재 이 값 대신 `/telemetry/czml`을 직접 쓴다(README "Two calling
    modes" 참고).
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
            satellite_id=req.satellite_id,
            merge_tolerance_sec=req.merge_tolerance_sec,
            interpolate_gaps=req.interpolate_gaps,
            invert_quaternion_direction=False,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Telemetry load failed: {e}") from e

    missing = [c for c in _MISSION_HK_COLS if c not in df.columns]
    if missing:
        raise HTTPException(status_code=404, detail=f"Missing required columns for mission_hk export: {missing}")

    df = df[["time", *_MISSION_HK_COLS]].dropna()
    if df.empty:
        raise HTTPException(status_code=404, detail="No complete position/attitude samples found in the requested range.")

    return MissionHkResponse(
        taiSeconds=[t.timestamp() for t in df["time"]],
        posWrtEci1=df["pos_wrt_eci1"].tolist(),
        posWrtEci2=df["pos_wrt_eci2"].tolist(),
        posWrtEci3=df["pos_wrt_eci3"].tolist(),
        qbodyWrtEci1=df["qbody_wrt_eci2"].tolist(),  # x
        qbodyWrtEci2=df["qbody_wrt_eci3"].tolist(),  # y
        qbodyWrtEci3=df["qbody_wrt_eci4"].tolist(),  # z
        qbodyWrtEci4=df["qbody_wrt_eci1"].tolist(),  # w (scalar) - DEM 필드 순서에 맞춰 4번 자리
    )