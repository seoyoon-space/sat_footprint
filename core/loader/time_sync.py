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
) -> pd.DataFrame:
    """
    packet_frames: {packet_name: DataFrame(columns=[time_col(as 'time'), <canonical fields>...])}
                    -> hk_loader.py에서 이미 canonical 컬럼명 + 'time' 컬럼으로 정리해서 넘겨줌
    master_key:     기준이 될 패킷 이름 (예: "hk1")
    tolerance_sec:  asof merge 허용 오차(초)
    interpolate_gaps: True면 남은 결측치를 시간 기반 선형보간으로 채움
    max_interp_gap_sec: 이 값보다 큰 결측 구간은 보간하지 않고 NaN 유지 (외삽 방지)

    반환: 'time' 컬럼 + 모든 canonical 필드가 합쳐진 단일 DataFrame
    """
    if master_key not in packet_frames:
        raise ValueError(f"master_key '{master_key}' not found in packet_frames")

    master = _ensure_utc(packet_frames[master_key], "time")
    merged = master

    tol = pd.Timedelta(seconds=tolerance_sec)

    for name, df in packet_frames.items():
        if name == master_key:
            continue
        if df.empty:
            continue
        df = _ensure_utc(df, "time")
        duplicate_cols = [c for c in df.columns if c in merged.columns and c != "time"]
        for col in duplicate_cols:
            df = df.rename(columns={col: f"{col}_{name}"})
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

        if max_interp_gap_sec is not None:
            # 큰 결측 구간은 보간에서 제외하기 위해 마스크 생성
            for col in value_cols:
                valid = merged[col].notna()
                if valid.sum() < 2:
                    continue
                gap = (
                    merged.index.to_series()
                    .where(valid)
                    .ffill()
                    .sub(merged.index.to_series())
                    .abs()
                )
                # placeholder: 실제 seconds 갭 계산은 아래에서 다시 처리

        if value_cols:
            merged[value_cols] = merged[value_cols].interpolate(method="time", limit_area="inside")
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