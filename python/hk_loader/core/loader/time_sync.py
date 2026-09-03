"""
분산된 HK 패킷(hk1~hk6)을 공통 타임스탬프 기준으로 동기화/병합.

각 패킷은 송신 주기가 다를 수 있으므로, asof-merge(가장 가까운 이전/이후
타임스탬프에 매칭)를 사용합니다. 허용 오차(tolerance)를 벗어나면 해당
필드는 NaN으로 남기고, 이후 보간(interpolate) 옵션으로 채울 수 있습니다.
"""

from __future__ import annotations

import pandas as pd


def _ensure_utc(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    df = df.copy()
    if pd.api.types.is_numeric_dtype(df[time_col]):
        series = pd.to_datetime(df[time_col], unit="s", utc=True)
    else:
        series = pd.to_datetime(df[time_col], utc=True)

    df[time_col] = pd.DatetimeIndex(series).as_unit("ns")
    return df.sort_values(time_col).reset_index(drop=True)


def merge_packets(
    packet_frames: dict[str, pd.DataFrame],
    master_key: str,
    tolerance_sec: float = 1.0,
    interpolate_gaps: bool = True,
    max_interp_gap_sec: float | None = 10.0,
    tolerance_overrides: dict[str, float] | None = None,
) -> pd.DataFrame:
    """
    packet_frames: {packet_name: DataFrame(columns=[time_col(as 'time'), <canonical fields>...])}
                    -> hk_loader.py에서 이미 canonical 컬럼명 + 'time' 컬럼으로 정리해서 넘겨줌
    master_key:     기준이 될 패킷 이름 (예: "hk1")
    tolerance_sec:  asof merge 허용 오차(초). 패킷별 override가 없으면 이 값을 사용.
    interpolate_gaps: True면 남은 결측치를 시간 기반 선형보간으로 채움
    max_interp_gap_sec: 이 값보다 큰 결측 구간은 보간하지 않고 NaN 유지 (외삽 방지)
    tolerance_overrides: {packet_name: tolerance_sec} 형태로 패킷별 허용 오차를 개별
        지정. 송신 주기가 느린 패킷(예: 10초 간격)에 전역 tolerance_sec(예: 1초)를
        그대로 쓰면 대부분의 마스터 타임스탬프가 매칭에 실패해 NaN이 되므로,
        hk_loader.py는 PacketSpec.rate_hz로부터 이 값을 계산해서 넘겨준다.

    반환: 'time' 컬럼 + 모든 canonical 필드가 합쳐진 단일 DataFrame
    """
    if master_key not in packet_frames:
        raise ValueError(f"master_key '{master_key}' not found in packet_frames")

    master = _ensure_utc(packet_frames[master_key], "time")
    merged = master

    overrides = tolerance_overrides or {}
    default_tol = pd.Timedelta(seconds=tolerance_sec)

    for name, df in packet_frames.items():
        if name == master_key:
            continue
        if df.empty:
            continue
        df = _ensure_utc(df, "time")
        duplicate_cols = [c for c in df.columns if c in merged.columns and c != "time"]
        for col in duplicate_cols:
            df = df.rename(columns={col: f"{col}_{name}"})
        tol = pd.Timedelta(seconds=overrides[name]) if name in overrides else default_tol
        merged = pd.merge_asof(
            merged,
            df,
            on="time",
            direction="nearest",
            tolerance=tol,
        )

    if interpolate_gaps:
        merged = merged.set_index("time")
        value_cols = [c for c in merged.columns if pd.api.types.is_numeric_dtype(merged[c])]

        if value_cols:
            original = merged[value_cols]
            interpolated = original.interpolate(method="time", limit_area="inside")

            if max_interp_gap_sec is not None:
                times = merged.index.to_series()
                for col in value_cols:
                    valid_mask = original[col].notna()
                    if valid_mask.sum() < 2:
                        continue
                    # 각 결측 지점 양옆의 가장 가까운 실측값 시각을 찾아 그 간격(초)을 구하고,
                    # max_interp_gap_sec을 넘는 구간은 보간값을 다시 NaN으로 되돌려 외삽을 방지한다.
                    valid_times = times.where(valid_mask)
                    prev_valid_time = valid_times.ffill()
                    next_valid_time = valid_times.bfill()
                    gap_sec = (next_valid_time - prev_valid_time).dt.total_seconds()
                    too_big = (gap_sec > max_interp_gap_sec) & ~valid_mask
                    interpolated.loc[too_big, col] = float("nan")

            merged[value_cols] = interpolated
        merged = merged.reset_index()

    return merged


def slice_time_range(
    df: pd.DataFrame,
    start_time: pd.Timestamp | str | int | float,
    end_time: pd.Timestamp | str | int | float,
    time_col: str = "time",
) -> pd.DataFrame:
    """공통 타임스탬프 기준으로 [start_time, end_time] 구간만 슬라이싱.

    start/end가 Unix epoch seconds로 들어오면 UTC datetime으로 변환해 비교한다.
    """
    if isinstance(start_time, (int, float)):
        start_time = pd.to_datetime(start_time, unit="s", utc=True)
    else:
        start_time = pd.to_datetime(start_time, utc=True)

    if isinstance(end_time, (int, float)):
        end_time = pd.to_datetime(end_time, unit="s", utc=True)
    else:
        end_time = pd.to_datetime(end_time, utc=True)

    mask = (df[time_col] >= start_time) & (df[time_col] <= end_time)
    return df.loc[mask].reset_index(drop=True)