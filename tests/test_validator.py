"""core/validator/ops_rules.py 검증 (numpy 벡터화 참조 구현과 교차검증 포함)."""
from __future__ import annotations

import math
import random

import numpy as np
import pytest

from core.validator.ops_rules import (
    Status,
    evaluate_ops_status,
    evaluate_settling_time,
    scan_wheel_saturation,
    worst_status,
)


def _numpy_settling_time(times, errors, tolerance, hold_duration):
    """슬라이딩 윈도우를 numpy로 벡터화한 독립 참조 구현."""
    t = np.asarray(times, dtype=float)
    e = np.asarray(errors, dtype=float)
    within = np.abs(e) <= tolerance

    for i in range(len(t)):
        if not within[i]:
            continue
        window_end = t[i] + hold_duration
        mask = t[i:] <= window_end
        if within[i:][mask].all():
            return t[i] - t[0]
    return None


def test_worst_status_ordering():
    assert worst_status([]) == Status.PASS
    assert worst_status([Status.PASS, Status.WARN]) == Status.WARN
    assert worst_status([Status.WARN, Status.FAIL, Status.PASS]) == Status.FAIL


def test_evaluate_settling_time_settles_and_matches_numpy_reference():
    times = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    errors = [5.0, 3.0, 1.5, 0.4, 0.2, 0.05, 0.02, 0.01, 0.01]
    tolerance = 0.5
    hold_duration = 2.0

    result = evaluate_settling_time(times, errors, tolerance=tolerance, hold_duration=hold_duration)
    ref = _numpy_settling_time(times, errors, tolerance, hold_duration)

    assert result.settled is True
    assert result.status is Status.PASS
    assert result.settling_time == pytest.approx(ref)
    assert result.settling_timestamp == pytest.approx(times[0] + ref)


def test_evaluate_settling_time_never_settles_is_fail():
    times = [0.0, 1.0, 2.0, 3.0, 4.0]
    errors = [5.0, 5.0, 5.0, 5.0, 5.0]

    result = evaluate_settling_time(times, errors, tolerance=0.1, hold_duration=1.0)

    assert result.settled is False
    assert result.settling_time is None
    assert result.status is Status.FAIL


def test_evaluate_settling_time_close_but_not_settled_is_warn():
    times = [0.0, 1.0, 2.0]
    errors = [5.0, 0.15, 0.15]  # tolerance=0.1 -> 미정착이지만 마지막 오차가 2*tolerance 이내

    result = evaluate_settling_time(times, errors, tolerance=0.1, hold_duration=5.0, warn_multiplier=2.0)

    assert result.settled is False
    assert result.status is Status.WARN


def test_evaluate_settling_time_transient_dip_is_not_falsely_settled():
    # 0.05초 시점에 잠깐 tolerance 안에 들어왔다가 다시 벗어나는 경우 오탐지하면 안 됨
    times = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    errors = [5.0, 0.05, 4.0, 0.05, 0.05, 0.05]
    tolerance = 0.1
    hold_duration = 2.0

    result = evaluate_settling_time(times, errors, tolerance=tolerance, hold_duration=hold_duration)
    ref = _numpy_settling_time(times, errors, tolerance, hold_duration)

    assert result.settling_time == pytest.approx(ref)
    assert result.settling_timestamp == pytest.approx(3.0)


def test_evaluate_settling_time_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        evaluate_settling_time([0.0, 1.0], [1.0], tolerance=0.1, hold_duration=1.0)


def _numpy_wheel_saturation(times, wheel_speeds, max_rpm, warn_ratio):
    events = []
    for channel, speeds in wheel_speeds.items():
        arr = np.asarray(speeds, dtype=float)
        ratio = np.abs(arr) / max_rpm
        for t, r in zip(times, ratio):
            if r >= 1.0:
                events.append((channel, t, "FAIL"))
            elif r >= warn_ratio:
                events.append((channel, t, "WARN"))
    return events


def test_scan_wheel_saturation_matches_numpy_reference():
    times = [0.0, 1.0, 2.0, 3.0]
    wheel_speeds = {
        "wheel_1": [1000.0, 4600.0, 5000.0, 100.0],
        "wheel_2": [200.0, 300.0, 400.0, 500.0],
    }
    max_rpm = 5000.0
    warn_ratio = 0.9

    report = scan_wheel_saturation(times, wheel_speeds, max_rpm=max_rpm, warn_ratio=warn_ratio)
    ref_events = _numpy_wheel_saturation(times, wheel_speeds, max_rpm, warn_ratio)

    assert len(report.events) == len(ref_events)
    got = {(e.channel, e.timestamp, e.status.name) for e in report.events}
    expected = {(c, t, s) for c, t, s in ref_events}
    assert got == expected

    assert report.status is Status.FAIL  # wheel_1 hits 5000 == max_rpm
    assert report.max_ratio_by_channel["wheel_1"] == pytest.approx(1.0)
    assert report.max_ratio_by_channel["wheel_2"] == pytest.approx(500.0 / 5000.0)


def test_scan_wheel_saturation_all_nominal_is_pass_with_no_events():
    times = [0.0, 1.0, 2.0]
    wheel_speeds = {"wheel_1": [10.0, 20.0, 30.0]}

    report = scan_wheel_saturation(times, wheel_speeds, max_rpm=5000.0)

    assert report.events == []
    assert report.status is Status.PASS


def test_scan_wheel_saturation_rejects_length_mismatch():
    with pytest.raises(ValueError):
        scan_wheel_saturation([0.0, 1.0], {"wheel_1": [1.0]}, max_rpm=100.0)


def test_scan_wheel_saturation_rejects_non_positive_max_rpm():
    with pytest.raises(ValueError):
        scan_wheel_saturation([0.0], {"wheel_1": [1.0]}, max_rpm=0.0)


def test_evaluate_ops_status_aggregates_worst_case():
    times = [0.0, 1.0, 2.0, 3.0]
    settling = evaluate_settling_time(times, [5.0, 5.0, 5.0, 5.0], tolerance=0.1, hold_duration=1.0)
    saturation = scan_wheel_saturation(times, {"wheel_1": [10.0, 20.0, 30.0, 40.0]}, max_rpm=5000.0)

    report = evaluate_ops_status(settling=settling, wheel_saturation=saturation)

    assert report.status is Status.FAIL  # settling failed even though wheels are nominal
    assert any("settle" in reason.lower() for reason in report.reasons)


def test_evaluate_ops_status_pass_when_all_nominal():
    times = [0.0, 1.0, 2.0]
    settling = evaluate_settling_time([0.0, 1.0, 2.0], [0.05, 0.05, 0.05], tolerance=0.1, hold_duration=1.0)
    saturation = scan_wheel_saturation(times, {"wheel_1": [10.0, 20.0, 30.0]}, max_rpm=5000.0)

    report = evaluate_ops_status(settling=settling, wheel_saturation=saturation)

    assert report.status is Status.PASS
    assert report.reasons == []


def test_settling_time_random_series_matches_numpy_reference():
    rng = random.Random(99)
    for _ in range(15):
        n = 30
        times = [float(i) for i in range(n)]
        errors = [rng.uniform(-6.0, 6.0) * math.exp(-0.15 * i) for i in range(n)]
        tolerance = 0.5
        hold_duration = 3.0

        result = evaluate_settling_time(times, errors, tolerance=tolerance, hold_duration=hold_duration)
        ref = _numpy_settling_time(times, errors, tolerance, hold_duration)

        if ref is None:
            assert result.settled is False
        else:
            assert result.settled is True
            assert result.settling_time == pytest.approx(ref)
