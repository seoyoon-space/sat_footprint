# 카메라 FOV 지상 풋프린트 계산 라우터 (DB 조회 없이 stateless 계산만 수행)

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.geometry.footprint import compute_footprint, footprint_to_geojson

from .auth import require_api_key
from .schemas import FootprintRequest

router = APIRouter(prefix="/footprint", tags=["footprint"], dependencies=[Depends(require_api_key)])


@router.post("/compute")
def compute_footprint_endpoint(req: FootprintRequest) -> dict:
    """위성 위치(ECI)/자세 쿼터니언/촬영 시각으로부터 카메라 FOV의 지상 풋프린트를 GeoJSON으로 반환.

    지평선 너머를 바라보는 코너는 계산에서 제외되며, `properties.visible=False`이면
    FOV의 일부(또는 전부)가 지구를 비켜가고 있다는 뜻(compute_footprint 참고).
    """
    footprint = compute_footprint(
        sat_pos_eci=(req.pos_eci_x, req.pos_eci_y, req.pos_eci_z),
        quaternion_body2eci=(req.q_w, req.q_x, req.q_y, req.q_z),
        utc_datetime=req.utc_datetime,
        fov_x_deg=req.fov_x_deg,
        fov_y_deg=req.fov_y_deg,
        boresight_body=(req.boresight_x, req.boresight_y, req.boresight_z),
    )
    return footprint_to_geojson(footprint, properties={"visible": footprint["visible"]})
