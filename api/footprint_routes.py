# 카메라 FOV 지상 풋프린트 계산 라우터

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException

from core.geometry.footprint import (
    camera_rays_ecef,
    compute_footprint,
    footprint_to_czml,
    footprint_to_geojson,
    footprint_track_to_czml,
    line_ground_points,
    line_to_geojson,
    line_track_to_czml,
)

from .auth import require_api_key
from .loader_cache import get_loader as _get_loader
from .schemas import (
    CameraRaySample,
    CameraRayTrackRequest,
    CameraRayTrackResponse,
    FootprintRequest,
    LineGroundPoint,
    LineTrackRequest,
    LineTrackResponse,
)

router = APIRouter(prefix="/footprint", tags=["footprint"], dependencies=[Depends(require_api_key)])

_POS_COLS = ["pos_wrt_eci1", "pos_wrt_eci2", "pos_wrt_eci3"]
_QUAT_COLS = ["qbody_wrt_eci1", "qbody_wrt_eci2", "qbody_wrt_eci3", "qbody_wrt_eci4"]


def _compute_footprint_from_request(req: FootprintRequest) -> dict:
    return compute_footprint(
        sat_pos_eci=(req.pos_eci_x, req.pos_eci_y, req.pos_eci_z),
        quaternion_body2eci=(req.q_w, req.q_x, req.q_y, req.q_z),
        utc_datetime=req.utc_datetime,
        fov_x_deg=req.fov_x_deg,
        fov_y_deg=req.fov_y_deg,
        boresight_body=(req.boresight_x, req.boresight_y, req.boresight_z),
    )


def _load_real_telemetry_samples(
    req: CameraRayTrackRequest | LineTrackRequest,
) -> list[tuple[datetime, tuple[float, float, float], tuple[float, float, float, float]]]:
    """satellite_id+기간의 실측 위치/자세 텔레메트리를 로드해 (시각, 위치, 쿼터니언)
    샘플 리스트로 정리. /footprint/rays·track·track/czml·line/*이 공유하는 공통 로딩 로직
    (satellite_id/start_time/end_time/merge_tolerance_sec 필드만 있으면 됨).
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

    missing = [c for c in (*_POS_COLS, *_QUAT_COLS) if c not in df.columns]
    if missing:
        raise HTTPException(status_code=404, detail=f"Missing required columns for footprint computation: {missing}")

    samples: list[tuple[datetime, tuple[float, float, float], tuple[float, float, float, float]]] = []
    for row in df[["time", *_POS_COLS, *_QUAT_COLS]].itertuples(index=False, name=None):
        ts, px, py, pz, q1, q2, q3, q4 = row
        values = (px, py, pz, q1, q2, q3, q4)
        if any(v != v for v in values):  # NaN(결측) 샘플은 계산 불가하므로 건너뜀
            continue
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        samples.append((ts_dt, (px, py, pz), (q1, q2, q3, q4)))
    return samples


@router.post("/compute")
def compute_footprint_endpoint(req: FootprintRequest) -> dict:
    """위성 위치(ECI)/자세 쿼터니언/촬영 시각으로부터 카메라 FOV의 지상 풋프린트를 GeoJSON으로 반환.

    지평선 너머를 바라보는 코너는 계산에서 제외, `properties.visible=False`이면
    FOV의 일부(또는 전부)가 지구를 비켜가고 있다는 뜻(compute_footprint 참고).
    """
    footprint = _compute_footprint_from_request(req)
    return footprint_to_geojson(footprint, properties={"visible": footprint["visible"]})


@router.post("/czml")
def footprint_czml_endpoint(req: FootprintRequest) -> list:
    """위성 위치/자세/촬영 시각으로부터 카메라 FOV의 지상 풋프린트를 CZML로 반환.

    Cesium에 직접 로드 가능한 패킷 리스트(document + polygon + boresight point)반화. 
    `/telemetry/czml`(위성 궤적/자세)과 함께 로드하면 같은 뷰어에서
    궤적과 촬영 영역을 동시에 시각화 가능.
    """
    footprint = _compute_footprint_from_request(req)
    return footprint_to_czml(footprint, properties={"visible": footprint["visible"]})


@router.post("/rays", response_model=CameraRayTrackResponse)
def camera_ray_track_endpoint(req: CameraRayTrackRequest) -> CameraRayTrackResponse:
    """지정 위성/기간의 실측 자세(HK 텔레메트리)로부터 카메라 boresight+FOV 모서리
    광선(ECEF 원점/방향)을 시간별로 반환.

    타원체/지형 교차는 하지 않는다 - 지형(DEM)을 가진 외부 서버가 이 광선을 자체
    정밀 지형모델로 교차시켜 촬영 풋프린트를 계산하는 것을 전제로 한다. 타원체
    근사만으로 충분하면 /footprint/track(폴리곤까지 이 API가 직접 계산)을 대신 쓴다.
    """
    boresight_body = (req.boresight_x, req.boresight_y, req.boresight_z)
    samples: list[CameraRaySample] = []
    for ts_dt, pos, quat in _load_real_telemetry_samples(req):
        rays = camera_rays_ecef(
            sat_pos_eci=pos,
            quaternion_body2eci=quat,
            utc_datetime=ts_dt,
            fov_x_deg=req.fov_x_deg,
            fov_y_deg=req.fov_y_deg,
            boresight_body=boresight_body,
        )
        samples.append(
            CameraRaySample(
                time=ts_dt,
                origin_ecef=rays["origin_ecef"],
                boresight_direction_ecef=rays["boresight_direction_ecef"],
                fov_corner_directions_ecef=rays["fov_corner_directions_ecef"],
            )
        )

    return CameraRayTrackResponse(satellite_id=req.satellite_id, num_records=len(samples), samples=samples)


def _compute_footprint_track(req: CameraRayTrackRequest) -> list[tuple[datetime, dict]]:
    boresight_body = (req.boresight_x, req.boresight_y, req.boresight_z)
    return [
        (
            ts_dt,
            compute_footprint(
                sat_pos_eci=pos,
                quaternion_body2eci=quat,
                utc_datetime=ts_dt,
                fov_x_deg=req.fov_x_deg,
                fov_y_deg=req.fov_y_deg,
                boresight_body=boresight_body,
            ),
        )
        for ts_dt, pos, quat in _load_real_telemetry_samples(req)
    ]


@router.post("/track")
def footprint_track_endpoint(req: CameraRayTrackRequest) -> dict:
    """지정 위성/기간의 실측 자세(HK 텔레메트리)로부터 매 시점 촬영 풋프린트를
    GeoJSON FeatureCollection으로 반환(WGS-84 타원체 근사, 지형 미반영).

    각 시점의 Polygon/Point Feature에 `time`(ISO8601 UTC) 속성이 붙는다. 지형(DEM)
    반영 정밀 풋프린트가 필요하면 /footprint/rays의 광선을 자체 지형모델과 교차시킬 것.
    """
    features: list[dict] = []
    for ts_dt, footprint in _compute_footprint_track(req):
        iso = ts_dt.isoformat().replace("+00:00", "Z") if ts_dt.tzinfo else ts_dt.isoformat() + "Z"
        geojson = footprint_to_geojson(footprint, properties={"time": iso, "visible": footprint["visible"]})
        features.extend(geojson["features"])

    return {"type": "FeatureCollection", "features": features}


@router.post("/track/czml")
def footprint_track_czml_endpoint(req: CameraRayTrackRequest) -> list:
    """지정 위성/기간의 실측 자세로부터 매 시점 촬영 풋프린트를 CZML로 반환(타원체 근사).

    각 시점의 폴리곤/중심점 패킷은 다음 샘플 시각까지만 표시되도록(availability)
    구성되어, Cesium 타임라인을 재생하면 촬영영역이 시간에 따라 전환된다.
    """
    return footprint_track_to_czml(_compute_footprint_track(req), id_prefix="footprint")


def _compute_line_track(req: LineTrackRequest) -> list[tuple[datetime, dict]]:
    boresight_body = (req.boresight_x, req.boresight_y, req.boresight_z)
    return [
        (
            ts_dt,
            line_ground_points(
                sat_pos_eci=pos,
                quaternion_body2eci=quat,
                utc_datetime=ts_dt,
                fov_across_deg=req.fov_across_deg,
                boresight_body=boresight_body,
            ),
        )
        for ts_dt, pos, quat in _load_real_telemetry_samples(req)
    ]


@router.post("/line/track", response_model=LineTrackResponse)
def line_track_endpoint(req: LineTrackRequest) -> LineTrackResponse:
    """지정 위성/기간의 실측 자세로부터, 푸시브룸 센서가 각 시점에 스캔 중인 '한 줄'의
    좌/우 지상점(WGS-84 타원체 근사)을 시간별로 반환.

    /footprint/track(전체 FOV 사각형 스냅샷)과 달리 along-track 폭을 0으로 취급해,
    Cesium 뷰어에서 사각뿔 FOV 안에 "지금 스캔 중인 라인" 위치를 표시하는 용도로 쓴다.
    시점 간격은 HK 텔레메트리 원본 샘플 주기(~1Hz) 그대로다 - 실제 카메라 line_rate
    수준의 보간은 하지 않는다(LineTrackRequest 참고).
    """
    samples: list[LineGroundPoint] = []
    for ts_dt, line in _compute_line_track(req):
        samples.append(
            LineGroundPoint(time=ts_dt, left=line["left"], right=line["right"], visible=line["visible"])
        )
    return LineTrackResponse(satellite_id=req.satellite_id, num_records=len(samples), samples=samples)


@router.post("/line/track/geojson")
def line_track_geojson_endpoint(req: LineTrackRequest) -> dict:
    """같은 라인 트랙을 GeoJSON FeatureCollection(LineString + 좌/우 Point)으로 반환."""
    features: list[dict] = []
    for ts_dt, line in _compute_line_track(req):
        iso = ts_dt.isoformat().replace("+00:00", "Z") if ts_dt.tzinfo else ts_dt.isoformat() + "Z"
        geojson = line_to_geojson(line, properties={"time": iso, "visible": line["visible"]})
        features.extend(geojson["features"])

    return {"type": "FeatureCollection", "features": features}


@router.post("/line/track/czml")
def line_track_czml_endpoint(req: LineTrackRequest) -> list:
    """같은 라인 트랙을 CZML polyline 시퀀스로 반환(availability로 시점 전환)."""
    return line_track_to_czml(_compute_line_track(req), id_prefix="line")
