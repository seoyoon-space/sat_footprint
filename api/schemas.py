from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TelemetryQueryRequest(BaseModel):
    satellite_id: str = Field(..., description="위성 코드 (예: O1A, E3T, O1B, BSS)")
    start_time: datetime | str = Field(..., description="조회 시작 시각 (KST 또는 UTC, 예: 2026-08-20 또는 2026-08-20T00:00:00+09:00)")
    end_time: datetime | str = Field(..., description="조회 종료 시각 (KST 또는 UTC, 예: 2026-08-20 또는 2026-08-20T23:59:59+09:00)")
    merge_tolerance_sec: float = Field(1.0, description="HK 패킷 병합 시 asof 허용 오차(초)")
    interpolate_gaps: bool = Field(True, description="결측 구간 시간 기반 선형보간 여부")


class TelemetryRecord(BaseModel):
    """core/loader/schema_map.py의 canonical 필드명(실제 O1B HK 스키마 기준)과 일치시킨다.
    (예전 버전은 q_eci2body_1/pos_eci_x 등 실제 DB에 없는 이름을 썼던 버그가 있었음)
    """

    model_config = ConfigDict(extra="allow")

    time: datetime
    qbody_wrt_eci1: float | None = None
    qbody_wrt_eci2: float | None = None
    qbody_wrt_eci3: float | None = None
    qbody_wrt_eci4: float | None = None
    body_rate1: float | None = None
    body_rate2: float | None = None
    body_rate3: float | None = None
    filt_speed_rpm1: float | None = None
    filt_speed_rpm2: float | None = None
    filt_speed_rpm3: float | None = None
    pos_wrt_eci1: float | None = None
    pos_wrt_eci2: float | None = None
    pos_wrt_eci3: float | None = None
    vel_wrt_eci1: float | None = None
    vel_wrt_eci2: float | None = None
    vel_wrt_eci3: float | None = None
    eigen_err: float | None = None


class TelemetryResponse(BaseModel):
    satellite_id: str
    start_time: datetime
    end_time: datetime
    num_records: int
    records: list[TelemetryRecord]


class FootprintRequest(BaseModel):
    pos_eci_x: float = Field(..., description="위성 위치 ECI X [m]")
    pos_eci_y: float = Field(..., description="위성 위치 ECI Y [m]")
    pos_eci_z: float = Field(..., description="위성 위치 ECI Z [m]")
    q_w: float = Field(..., description="자세 쿼터니언 스칼라부(body->eci, scalar-first 관례)")
    q_x: float = Field(..., description="자세 쿼터니언 벡터부 x")
    q_y: float = Field(..., description="자세 쿼터니언 벡터부 y")
    q_z: float = Field(..., description="자세 쿼터니언 벡터부 z")
    utc_datetime: datetime = Field(..., description="촬영 시각 (UTC)")
    fov_x_deg: float = Field(..., gt=0, description="카메라 시야각 X축 [deg]")
    fov_y_deg: float = Field(..., gt=0, description="카메라 시야각 Y축 [deg]")
    boresight_x: float = Field(0.0, description="바디 프레임 보어사이트 방향 벡터 x")
    boresight_y: float = Field(0.0, description="바디 프레임 보어사이트 방향 벡터 y")
    boresight_z: float = Field(1.0, description="바디 프레임 보어사이트 방향 벡터 z")


class CameraRayTrackRequest(BaseModel):
    """실측 텔레메트리(위치/자세) 기반 카메라 광선(ECEF 원점/방향) 조회 요청.

    타원체/지형 교차는 하지 않는다 - 지형(DEM)을 가진 외부 서버가 이 광선을 받아
    자체 정밀 지형모델로 풋프린트를 계산하는 것을 전제로 한다.
    """

    satellite_id: str = Field(..., description="위성 코드 (예: O1A, E3T, O1B, BSS)")
    start_time: datetime | str = Field(..., description="조회 시작 시각 (KST 또는 UTC)")
    end_time: datetime | str = Field(..., description="조회 종료 시각 (KST 또는 UTC)")
    merge_tolerance_sec: float = Field(1.0, description="HK 패킷 병합 시 asof 허용 오차(초)")
    fov_x_deg: float = Field(..., gt=0, description="카메라 시야각 X축 [deg]")
    fov_y_deg: float = Field(..., gt=0, description="카메라 시야각 Y축 [deg]")
    boresight_x: float = Field(0.0, description="바디 프레임 보어사이트 방향 벡터 x")
    boresight_y: float = Field(0.0, description="바디 프레임 보어사이트 방향 벡터 y")
    boresight_z: float = Field(1.0, description="바디 프레임 보어사이트 방향 벡터 z")


class CameraRaySample(BaseModel):
    time: datetime
    origin_ecef: tuple[float, float, float] = Field(..., description="위성 위치 ECEF [m]")
    boresight_direction_ecef: tuple[float, float, float] = Field(
        ..., description="보어사이트 방향 단위벡터 (ECEF, 정규화됨)"
    )
    fov_corner_directions_ecef: list[tuple[float, float, float]] = Field(
        ..., description="FOV 네 모서리 방향 단위벡터 (ECEF), 폴리곤 순서(반시계/시계 일관)"
    )


class CameraRayTrackResponse(BaseModel):
    satellite_id: str
    num_records: int
    samples: list[CameraRaySample]


class LineTrackRequest(BaseModel):
    """실측 텔레메트리 기반, 푸시브룸(라인스캔) 센서가 각 시점에 스캔 중인 '한 줄'의
    좌/우 지상점 조회 요청. along-track(진행 방향) 폭은 0으로 취급 - fov_across_deg
    하나만 받는다(DEM 서버 쪽 SensorConfig와 동일한 단일-FOV 라인센서 모델).

    시점 간격은 HK 텔레메트리 원본 샘플 주기(보통 ~1Hz) 그대로이며, 실제 카메라의
    line_rate(초당 수백~수천 라인)만큼 보간하지 않는다 - 그 정밀도가 필요하면 DEM
    서버의 Orekit/Rugged 파이프라인이 담당하는 영역이다.
    """

    satellite_id: str = Field(..., description="위성 코드 (예: O1A, E3T, O1B, BSS)")
    start_time: datetime | str = Field(..., description="조회 시작 시각 (KST 또는 UTC)")
    end_time: datetime | str = Field(..., description="조회 종료 시각 (KST 또는 UTC)")
    merge_tolerance_sec: float = Field(1.0, description="HK 패킷 병합 시 asof 허용 오차(초)")
    fov_across_deg: float = Field(..., gt=0, description="센서 폭 방향(across-track) 시야각 [deg]")
    boresight_x: float = Field(0.0, description="바디 프레임 보어사이트 방향 벡터 x")
    boresight_y: float = Field(0.0, description="바디 프레임 보어사이트 방향 벡터 y")
    boresight_z: float = Field(1.0, description="바디 프레임 보어사이트 방향 벡터 z")


class LineGroundPoint(BaseModel):
    time: datetime
    left: tuple[float, float] | None = Field(None, description="줄 왼쪽 끝 지상점 [lon, lat]")
    right: tuple[float, float] | None = Field(None, description="줄 오른쪽 끝 지상점 [lon, lat]")
    visible: bool


class LineTrackResponse(BaseModel):
    satellite_id: str
    num_records: int
    samples: list[LineGroundPoint]


class PropagationTrackRequest(BaseModel):
    """TLE 기반 SGP4 궤도 전파 요청. DB 조회 없이(실측 텔레메트리와 무관) 순수 예측값을 낸다."""

    tle_line1: str = Field(..., description="TLE 1번째 줄 ('1 '로 시작)")
    tle_line2: str = Field(..., description="TLE 2번째 줄 ('2 '로 시작)")
    start_time: datetime = Field(..., description="전파 시작 시각 (UTC)")
    end_time: datetime = Field(..., description="전파 종료 시각 (UTC)")
    step_sec: float = Field(60.0, gt=0, description="전파 간격 [초]")


class PropagationSample(BaseModel):
    time: datetime
    position_km: tuple[float, float, float] = Field(..., description="TEME(~=ECI) 위치 [km]")
    velocity_km_s: tuple[float, float, float] = Field(..., description="TEME(~=ECI) 속도 [km/s]")


class PropagationTrackResponse(BaseModel):
    num_records: int
    samples: list[PropagationSample]


class SettlingResultSchema(BaseModel):
    settled: bool
    settling_time: float | None
    settling_timestamp: float | None
    final_error: float | None
    status: str


class SaturationEventSchema(BaseModel):
    channel: str
    timestamp: float
    value: float
    ratio: float
    status: str


class WheelSaturationReportSchema(BaseModel):
    events: list[SaturationEventSchema]
    max_ratio_by_channel: dict[str, float]
    status: str


class OpsStatusRequest(BaseModel):
    satellite_id: str = Field(..., description="위성 코드 (예: O1A, E3T, O1B, BSS)")
    start_time: datetime | str = Field(..., description="조회 시작 시각 (KST 또는 UTC)")
    end_time: datetime | str = Field(..., description="조회 종료 시각 (KST 또는 UTC)")
    merge_tolerance_sec: float = Field(1.0, description="HK 패킷 병합 시 asof 허용 오차(초)")

    settling_tolerance_deg: float | None = Field(
        None, description="정착 판정 허용 오차 [deg] (eigen_err 기준). None이면 정착 평가를 생략"
    )
    settling_hold_duration_sec: float = Field(30.0, description="정착 판정을 위해 tolerance 이내로 유지해야 하는 시간 [초]")
    settling_warn_multiplier: float = Field(2.0, description="미정착 시 WARN/FAIL 경계 배수")

    wheel_max_rpm: float | None = Field(
        None, description="휠 최대 정격 회전속도 [RPM]. None이면 휠 포화 평가를 생략"
    )
    wheel_warn_ratio: float = Field(0.9, description="휠 포화 WARN 임계 비율")


class OpsStatusResponse(BaseModel):
    satellite_id: str
    status: str
    reasons: list[str]
    settling: SettlingResultSchema | None = None
    wheel_saturation: WheelSaturationReportSchema | None = None


class MissionScheduleRequest(BaseModel):
    """MCE(미션 스케줄링) DB 조회 요청 - HK DB(satellite_id 기반 텔레메트리)와는 별개의 DB."""

    satellite_id: str = Field(..., description="위성 코드 (예: O1A, O1B)")
    start_time: datetime = Field(..., description="조회 시작 시각 (UTC) - EventStart 기준")
    end_time: datetime = Field(..., description="조회 종료 시각 (UTC) - EventStart 기준")


class MissionScheduleRecord(BaseModel):
    """core/mission/mce_db.py::_row_to_mission()의 필드와 1:1 대응."""

    model_config = ConfigDict(extra="allow")

    id: int
    scheduleId: str
    satelliteId: str | None = None
    operationId: str | None = None
    location: str | None = None
    aoiId: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    eventStart: str | None = None
    eventEnd: str | None = None
    eventStartKST: str | None = None
    eventEndKST: str | None = None
    duration: float | None = None
    maxEl: float | None = None
    cloudAmount: float | None = None
    requestedScanTime: float | None = None
    note: str | None = None
    missionStatus: str | None = None
    imageStatus: str | None = None
    clientData: dict | list | None = None
    results: dict | list | None = None
    scanStart: str | None = Field(None, description="실제 카메라 스캔 시작 시각(ISO8601 UTC)")
    camStart: str | None = Field(None, description="실제 카메라 ON 시각(ISO8601 UTC)")
    camEnd: str | None = Field(None, description="실제 카메라 OFF 시각(ISO8601 UTC)")


class MissionScheduleResponse(BaseModel):
    satellite_id: str
    num_records: int
    missions: list[MissionScheduleRecord]