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
    model_config = ConfigDict(extra="allow")

    time: datetime
    q_eci2body_1: float | None = None
    q_eci2body_2: float | None = None
    q_eci2body_3: float | None = None
    q_eci2body_4: float | None = None
    body_rate_x: float | None = None
    body_rate_y: float | None = None
    body_rate_z: float | None = None
    wheel_rpm_1: float | None = None
    wheel_rpm_2: float | None = None
    wheel_rpm_3: float | None = None
    pos_eci_x: float | None = None
    pos_eci_y: float | None = None
    pos_eci_z: float | None = None
    vel_eci_x: float | None = None
    vel_eci_y: float | None = None
    vel_eci_z: float | None = None


class TelemetryResponse(BaseModel):
    satellite_id: str
    start_time: datetime
    end_time: datetime
    num_records: int
    records: list[TelemetryRecord]