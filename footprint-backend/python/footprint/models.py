"""데이터 모델 정의.

위성 상태(SatelliteState)와 센서 설정(SensorConfig)을 정의합니다.
이 모듈은 외부 의존성 없이 순수 Python dataclass로만 구성됩니다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

FMC_GROUND_SPEED_MPS = 3906.173
EARTH_RADIUS_M = 6378137.0


@dataclass
class SatelliteState:
    """한 시점의 위성 상태 (위치 + 속도 + 자세).

    좌표계:
        position/velocity: ECI (≈EME2000), 단위 m / m/s
        quaternion: body-wrt-ECI, 스칼라 우선 (q0=w, q1, q2, q3)

    HK2 데이터의 posWrtEci, velWrtEci, qbodyWrtEci에 대응합니다.
    Python → Java CSV 변환 시 이 구조체를 기준으로 직렬화합니다.
    """
    timestamp: datetime

    # ECI position (m)
    px: float
    py: float
    pz: float

    @property
    def altitude_m(self) -> float:
        dist = math.sqrt(self.px**2 + self.py**2 + self.pz**2)
        return dist - EARTH_RADIUS_M

    # ECI velocity (m/s)
    vx: float
    vy: float
    vz: float

    # body-wrt-ECI quaternion (scalar-first)
    q0: float
    q1: float
    q2: float
    q3: float

    def to_csv_row(self) -> str:
        iso = self.timestamp.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return (
            f"{iso},{self.px},{self.py},{self.pz},"
            f"{self.vx},{self.vy},{self.vz},"
            f"{self.q0},{self.q1},{self.q2},{self.q3}"
        )


@dataclass
class SensorConfig:
    """센서 광학 스펙.

    MultiScape200 기준값이 기본으로 설정되어 있습니다.
    확보되지 않은 파라미터(mounting_error_deg, line_rate)는 기본값(0, 100)으로 두고,
    실제 값이 확보되면 업데이트합니다.
    """
    fov_across_deg: float = 1.6
    focal_length_mm: float = 1067.0
    pixel_size_um: float = 3.2
    mounting_error_deg: float = 0.0
    line_rate: float = 100.0

    @property
    def ifov_rad(self) -> float:
        return (self.pixel_size_um / 1000.0) / self.focal_length_mm

    @property
    def num_pixels(self) -> int:
        return round(math.radians(self.fov_across_deg) / self.ifov_rad)

    def compute_gsd(self, altitude_m: float) -> float:
        return self.ifov_rad * altitude_m

    def compute_line_rate(self, altitude_m: float) -> float:
        return FMC_GROUND_SPEED_MPS / self.compute_gsd(altitude_m)


@dataclass
class FootprintLine:
    """Footprint 계산 결과 한 라인 (push-broom 한 순간)."""
    line: int
    time_utc: str
    left_lat: float
    left_lon: float
    left_alt: float
    right_lat: float
    right_lon: float
    right_alt: float
