"""TLE(Two-Line Element) 기반 SGP4 궤도 전파.

core의 나머지 부분과 달리 검증된 외부 패키지(sgp4)를 사용. 결과는 TEME 프레임
위치[km]/속도[km/s] - core.coordinates의 ECI와는 엄밀히 다른 준거계지만 차이가
수십 각초 수준이라 실무 관행대로 TEME=ECI로 취급한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sgp4.api import SGP4_ERRORS, Satrec, WGS72, jday

__all__ = ["TleError", "PropagatedState", "load_tle", "propagate"]


class TleError(RuntimeError):
    """TLE 파싱 또는 SGP4 전파 실패."""


@dataclass(frozen=True)
class PropagatedState:
    position_km: tuple[float, float, float]   # TEME(~= ECI) 위치
    velocity_km_s: tuple[float, float, float]  # TEME(~= ECI) 속도
    epoch_utc: datetime


def load_tle(line1: str, line2: str) -> Satrec:
    """TLE 두 줄을 파싱해서 sgp4 Satrec 객체 생성.

    중력상수는 표준 TLE 배포 관례(NORAD)에 맞춰 WGS-72를 사용.
    """
    if not line1.startswith("1 ") or not line2.startswith("2 "):
        raise TleError("Invalid TLE lines: line1 must start with '1 ' and line2 with '2 '")
    try:
        satrec = Satrec.twoline2rv(line1, line2, WGS72)
    except (ValueError, IndexError) as exc:
        raise TleError(f"Failed to parse TLE: {exc}") from exc
    return satrec


def propagate(satrec: Satrec, utc_datetime: datetime) -> PropagatedState:
    """주어진 UTC 시각의 위성 상태(TEME 위치/속도)를 SGP4로 전파.
    """
    if utc_datetime.tzinfo is None:
        utc_datetime = utc_datetime.replace(tzinfo=timezone.utc)
    utc_datetime = utc_datetime.astimezone(timezone.utc)

    jd, fr = jday(
        utc_datetime.year,
        utc_datetime.month,
        utc_datetime.day,
        utc_datetime.hour,
        utc_datetime.minute,
        utc_datetime.second + utc_datetime.microsecond / 1.0e6,
    )
    error_code, position, velocity = satrec.sgp4(jd, fr)
    if error_code != 0:
        reason = SGP4_ERRORS.get(error_code, "unknown SGP4 error")
        raise TleError(f"SGP4 propagation failed (code {error_code}: {reason}) at {utc_datetime.isoformat()}")

    return PropagatedState(
        position_km=(position[0], position[1], position[2]),
        velocity_km_s=(velocity[0], velocity[1], velocity[2]),
        epoch_utc=utc_datetime,
    )
