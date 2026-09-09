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
            # HKLoader.load()/_normalize_query_time는 str/datetime을 모두 받으므로 그대로 전달.
            # (예전에는 .isoformat()을 강제 호출해서, pydantic이 ISO 문자열을 str로 파싱한
            # 경우 - datetime|str 유니온에서 흔히 벌어짐 - AttributeError로 항상 실패했음)
            start_time=req.start_time,
            end_time=req.end_time,
            # satellite_id는 위성별 hk1~hk6 테이블 선택에 쓰인다(tbl_obs1a_hk*/tbl_obs1b_hk*) -
            # None으로 넘기면 항상 O1A 테이블을 조회해버리므로 반드시 실제 값을 전달해야 한다.
            satellite_id=req.satellite_id,
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


@router.post("/mission-hk", response_model=MissionHkResponse)
def mission_hk_telemetry(req: TelemetryQueryRequest) -> MissionHkResponse:
    """지정 위성/기간의 실측 위치+자세를 DEM 서버 czml_generator.py의 `mission_hk` dict와
    같은 필드명(camelCase)·컬럼별 배열 형태로 반환한다 - 이름만 맞춘 것이고 값은 이
    API의 다른 엔드포인트와 동일하게 이미 보정된 최종값이다: `posWrtEci1..3`은 미터,
    `qbodyWrtEci1..4`는 Body->ECI 회전을 나타내는 scalar-first 쿼터니언(1=x, 2=y, 3=z,
    4=w - w를 4번 자리에 두는 것은 DEM 쪽 필드 순서에 맞춘 것일 뿐, scalar-last라는
    뜻은 아니다).

    DEM의 generate_czml()을 그대로 이 응답에 쓰려면, 그 함수가 자체적으로 걸던
    km->m 변환(`* 1000.0`)과 쿼터니언 재정렬+conjugate를 제거해야 한다 - 이 응답은
    이미 최종값이라 그 보정을 다시 걸면 값이 어긋난다.
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