"""정착 시간 평가, 휠 포화 스캔, PASS/WARN/FAIL 상태 엔진.

HK 텔레메트리에서 뽑아낸 시계열(시간 리스트 + 값 리스트)을 입력으로 받아 
운영 규칙(정착 판정/포화 판정)을 평가하고, 결과를 PASS/WARN/FAIL 상태로 요약.

대상 HK 필드 예시(core/loader/schema_map.py 기준):
  - hk6.eigen_err       : 자세 오차(eigen-axis error angle)
  - hk2.filt_speed_rpm1..3 : 반응휠 회전속도 [RPM]
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Sequence


class Status(IntEnum):
    """심각도 순서를 갖는 상태. 값이 클수록 나쁨 -> max()로 최악 상태를 쉽게 합성 가능."""

    PASS = 0
    WARN = 1
    FAIL = 2

    def __str__(self) -> str:  # pragma: no cover - 표현용
        return self.name


def worst_status(statuses: Sequence[Status]) -> Status:
    """여러 상태 중 가장 심각한 것을 반환. 빈 시퀀스는 PASS."""
    return max(statuses, default=Status.PASS)


@dataclass(frozen=True)
class SettlingResult:
    settled: bool
    settling_time: float | None  # t0 기준 정착까지 걸린 시간 [초], 미정착 시 None
    settling_timestamp: float | None  # 정착이 시작된 시각(원본 시간축), 미정착 시 None
    final_error: float | None
    status: Status


def evaluate_settling_time(
    times: Sequence[float],
    errors: Sequence[float],
    *,
    tolerance: float,
    hold_duration: float,
    warn_multiplier: float = 2.0,
) -> SettlingResult:
    """오차 시계열이 tolerance 이내로 진입해 hold_duration 동안 유지되는 시점을 탐색.

    times: 오름차순 시간(초 단위 float, 예: epoch 또는 시뮬레이션 경과시간)
    errors: 각 시각의 오차(절대값 비교, 예: 자세 eigen-error angle)
    tolerance: 정착 판정 허용 오차 (|error| <= tolerance)
    hold_duration: tolerance 이내로 최소 유지해야 하는 시간 [초]
    warn_multiplier: 정착에 실패했을 때, 마지막 오차가 tolerance의 몇 배 이내면
        WARN으로 완화할지 결정하는 배수 (그 이상이면 FAIL)

    반환된 settling_time은 t0(times[0]) 기준 경과 시간이다.
    """
    n = len(times)
    if n == 0 or n != len(errors):
        raise ValueError("times/errors must be non-empty sequences of equal length")

    settled_index: int | None = None
    for i in range(n):
        if abs(errors[i]) > tolerance:
            continue
        # i부터 tolerance 이내가 hold_duration 만큼 끊기지 않고 유지되는지 확인
        window_end_time = times[i] + hold_duration
        held = True
        for j in range(i, n):
            if times[j] > window_end_time:
                break
            if abs(errors[j]) > tolerance:
                held = False
                break
        else:
            # 루프가 break 없이 끝났다면(데이터가 hold_duration 전에 끝남) 유지 여부는
            # 마지막까지 관측된 구간에서만 판단 가능 -> 관측된 구간 전부가 tolerance 이내였으므로 유지로 인정
            held = True
        if held:
            settled_index = i
            break

    t0 = times[0]
    final_error = errors[-1]

    if settled_index is not None:
        return SettlingResult(
            settled=True,
            settling_time=times[settled_index] - t0,
            settling_timestamp=times[settled_index],
            final_error=final_error,
            status=Status.PASS,
        )

    status = Status.WARN if abs(final_error) <= tolerance * warn_multiplier else Status.FAIL
    return SettlingResult(
        settled=False,
        settling_time=None,
        settling_timestamp=None,
        final_error=final_error,
        status=status,
    )


@dataclass(frozen=True)
class SaturationEvent:
    channel: str
    timestamp: float
    value: float
    ratio: float  # value / max_value (부호 있는 값 기준 절대비율)
    status: Status


@dataclass(frozen=True)
class WheelSaturationReport:
    events: list[SaturationEvent]
    max_ratio_by_channel: dict[str, float]
    status: Status


def scan_wheel_saturation(
    times: Sequence[float],
    wheel_speeds: dict[str, Sequence[float]],
    *,
    max_rpm: float,
    warn_ratio: float = 0.9,
) -> WheelSaturationReport:
    """휠 회전속도 시계열을 스캔해 포화(saturation) 구간을 PASS/WARN/FAIL로 분류.

    times: 공통 시간축
    wheel_speeds: {채널명: 회전속도[RPM] 시계열}, 각 시퀀스 길이는 len(times)와 동일
    max_rpm: 휠의 최대 정격 회전속도 [RPM] (|speed| >= max_rpm이면 FAIL)
    warn_ratio: |speed| >= warn_ratio * max_rpm 이면 WARN

    PASS 상태인 샘플은 이벤트 목록에 포함하지 않는다(경고/포화 구간만 기록).
    """
    if max_rpm <= 0.0:
        raise ValueError("max_rpm must be positive")

    n = len(times)
    events: list[SaturationEvent] = []
    max_ratio_by_channel: dict[str, float] = {}

    for channel, speeds in wheel_speeds.items():
        if len(speeds) != n:
            raise ValueError(f"channel '{channel}' length {len(speeds)} does not match times length {n}")

        channel_max_ratio = 0.0
        for t, speed in zip(times, speeds):
            ratio = abs(speed) / max_rpm
            channel_max_ratio = max(channel_max_ratio, ratio)

            if ratio >= 1.0:
                status = Status.FAIL
            elif ratio >= warn_ratio:
                status = Status.WARN
            else:
                continue

            events.append(SaturationEvent(channel=channel, timestamp=t, value=speed, ratio=ratio, status=status))

        max_ratio_by_channel[channel] = channel_max_ratio

    overall = worst_status([e.status for e in events])
    return WheelSaturationReport(events=events, max_ratio_by_channel=max_ratio_by_channel, status=overall)


@dataclass(frozen=True)
class OpsStatusReport:
    status: Status
    reasons: list[str] = field(default_factory=list)
    settling: SettlingResult | None = None
    wheel_saturation: WheelSaturationReport | None = None


def evaluate_ops_status(
    *,
    settling: SettlingResult | None = None,
    wheel_saturation: WheelSaturationReport | None = None,
) -> OpsStatusReport:
    """개별 점검 결과들을 종합해 전체 PASS/WARN/FAIL 상태와 사유를 산출."""
    statuses: list[Status] = []
    reasons: list[str] = []

    if settling is not None:
        statuses.append(settling.status)
        if settling.status is Status.FAIL:
            reasons.append("Attitude did not settle within tolerance.")
        elif settling.status is Status.WARN:
            reasons.append("Attitude did not fully settle but remains close to tolerance.")

    if wheel_saturation is not None:
        statuses.append(wheel_saturation.status)
        for channel, ratio in wheel_saturation.max_ratio_by_channel.items():
            if ratio >= 1.0:
                reasons.append(f"Wheel '{channel}' reached saturation ({ratio * 100:.1f}% of max RPM).")
            elif ratio >= 0.9:
                reasons.append(f"Wheel '{channel}' approaching saturation ({ratio * 100:.1f}% of max RPM).")

    return OpsStatusReport(
        status=worst_status(statuses),
        reasons=reasons,
        settling=settling,
        wheel_saturation=wheel_saturation,
    )
