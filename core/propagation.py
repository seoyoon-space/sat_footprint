"""TLE(Two-Line Element) 기반 SGP4 궤도 전파.

이 프로젝트의 나머지 계산 코드(core/coordinates.py, core/math_utils/quat.py,
core/geometry/footprint.py)는 표준 라이브러리(math)만으로 직접 구현되어 있지만,
SGP4는 예외적으로 검증된 외부 패키지(sgp4, https://pypi.org/project/sgp4)를 사용한다.

이유: SGP4는 WGS-72 기반 경험 상수가 수십 개 들어가고 deep-space 공진 보정 등
미묘한 분기 처리가 많아, 숙련자도 직접 구현 시 조용히 틀리기 쉬운 알고리즘이다.
반면 `sgp4` 패키지는 Vallado의 공식 검증 벡터(Spacetrack Report #3 / AIAA 2006-6753,
satellite 88888 케이스 등)로 지속적으로 검증되는 성숙한 구현체이므로, 직접 구현보다
이 패키지를 명시적 의존성으로 채택하는 쪽이 훨씬 안전하다.

프레임/시간 관례:
- 전파 결과는 TEME(True Equator Mean Equinox) 프레임의 위치[km]/속도[km/s]다.
  core.coordinates가 다루는 ECI(GCRF/J2000 평균 적도/분점)와는 정의상 다른 준거계지만
  차이는 수 각초~수십 각초 수준이라, SGP4를 다루는 실무에서는 TEME를 그대로 'ECI'로
  취급하는 것이 표준 관행이다(Vallado도 이를 명시적으로 언급).
- 입력 시각은 UTC로 받아 SGP4 내부적으로 UT1으로 근사한다(core.coordinates의
  earth_rotation_angle_rad와 동일한 근사 - ΔUT1-UTC 미보정, 최대 약 0.9초 오차).
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
    """TLE 두 줄을 파싱해서 sgp4 Satrec 객체를 생성.

    line1: '1 '로 시작하는 첫 번째 줄 (위성번호, 발사연도, epoch, drag 항 등)
    line2: '2 '로 시작하는 두 번째 줄 (궤도요소: 경사각/승교점/이심률/근지점편각/평균근점각/평균운동)

    중력상수는 표준 TLE 배포 관례(NORAD)에 맞춰 WGS-72를 사용한다.
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

    TLE epoch로부터 임의의 과거/미래 시점 모두 전파 가능하나, epoch에서 멀어질수록
    SGP4 자체의 모델 오차(항력/섭동 모델링 한계)가 커진다는 점은 SGP4의 근본적 특성이다
    (통상 최신 TLE 기준 수일 내에서 km 수준 정확도, 그 이상은 급격히 저하).
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
