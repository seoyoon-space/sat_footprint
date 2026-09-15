"""core/propagation.py(SGP4 래퍼) 검증.

SGP4 알고리즘 자체의 정확성은 sgp4 패키지(Vallado의 공식 검증 벡터로 지속 검증됨)가
책임지므로, 여기서는 우리 래퍼의 배관(TLE 파싱, 시간 변환, 단위, 에러 처리)이 올바른지만
검증한다. 테스트 TLE는 SGP4 검증에 널리 쓰이는 표준 예제인 "satellite 88888"
(Vallado, Revisiting Spacetrack Report #3, AIAA 2006-6753의 SGP4-VER.TLE)이다.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from core.propagation import TleError, load_tle, propagate

# Vallado의 SGP4 검증 표준 예제(satellite 88888, "Original STR#3 SGP4 test")
LINE1 = "1 88888U          80275.98708465  .00073094  13844-3  66816-4 0    87"
LINE2 = "2 88888  72.8435 115.9689 0086731  52.6988 110.5714 16.05824518  1058"

# 평균운동(mean motion) 16.05824518 rev/day로부터 케플러 제3법칙으로 독립 계산한
# 반장축 기대값(WGS-72 mu ~= 398600.8 km^3/s^2): a = (mu / n^2)^(1/3) ~= 6638 km.
# 이심률이 0.0086731로 작아 실제 궤도반경은 이 값 근방(대략 +-1%)에서 변동한다.
EXPECTED_SEMI_MAJOR_AXIS_KM = 6638.0
EXPECTED_ORBITAL_SPEED_KM_S = 7.75  # v = sqrt(mu/a) ~= 7.75 km/s (근사 원궤도 가정)


def test_load_tle_parses_valid_lines():
    satrec = load_tle(LINE1, LINE2)
    assert satrec.satnum == 88888


def test_load_tle_rejects_malformed_lines():
    with pytest.raises(TleError):
        load_tle("not a valid tle line", LINE2)
    with pytest.raises(TleError):
        load_tle(LINE1, "also not valid")


def test_propagate_at_epoch_matches_kepler_third_law_sanity_bounds():
    """전파 결과의 위치/속도 크기가 평균운동으로부터 독립 계산한 궤도반경/속도와 맞아야 함."""
    satrec = load_tle(LINE1, LINE2)
    epoch_utc = datetime(1980, 1, 1, tzinfo=timezone.utc) + timedelta(days=275.98708465 - 1)

    state = propagate(satrec, epoch_utc)

    r = math.sqrt(sum(c * c for c in state.position_km))
    v = math.sqrt(sum(c * c for c in state.velocity_km_s))

    # 이심률 0.0087 궤도이므로 케플러 추정치의 +-2% 이내에 있어야 함
    assert EXPECTED_SEMI_MAJOR_AXIS_KM * 0.98 < r < EXPECTED_SEMI_MAJOR_AXIS_KM * 1.02
    assert EXPECTED_ORBITAL_SPEED_KM_S * 0.98 < v < EXPECTED_ORBITAL_SPEED_KM_S * 1.02


def test_propagate_advances_position_over_time():
    """시간이 지나면 위치가 달라지고(궤도운동), 궤도반경은 여전히 물리적으로 타당해야 함."""
    satrec = load_tle(LINE1, LINE2)
    epoch_utc = datetime(1980, 1, 1, tzinfo=timezone.utc) + timedelta(days=275.98708465 - 1)

    state_t0 = propagate(satrec, epoch_utc)
    state_t10 = propagate(satrec, epoch_utc + timedelta(minutes=10))

    assert state_t0.position_km != state_t10.position_km
    r_t10 = math.sqrt(sum(c * c for c in state_t10.position_km))
    assert EXPECTED_SEMI_MAJOR_AXIS_KM * 0.98 < r_t10 < EXPECTED_SEMI_MAJOR_AXIS_KM * 1.02


def test_propagate_accepts_naive_datetime_as_utc():
    """tzinfo 없는 datetime은 UTC로 간주해야 함(core.coordinates의 다른 함수들과 동일 관례)."""
    satrec = load_tle(LINE1, LINE2)
    epoch_utc = datetime(1980, 1, 1, tzinfo=timezone.utc) + timedelta(days=275.98708465 - 1)
    naive = epoch_utc.replace(tzinfo=None)

    state_aware = propagate(satrec, epoch_utc)
    state_naive = propagate(satrec, naive)

    assert state_aware.position_km == state_naive.position_km


def test_propagate_raises_tle_error_on_sgp4_failure():
    """평균운동이 물리적으로 불가능한(거의 0에 가까운) TLE는 SGP4 내부 오류코드를 반환해야 함."""
    bad_line2 = "2 88888  72.8435 115.9689 0086731  52.6988 110.5714  0.00000001  1058"
    satrec = load_tle(LINE1, bad_line2)
    epoch_utc = datetime(1980, 1, 1, tzinfo=timezone.utc) + timedelta(days=275.98708465 - 1)

    with pytest.raises(TleError):
        propagate(satrec, epoch_utc + timedelta(days=3650))
