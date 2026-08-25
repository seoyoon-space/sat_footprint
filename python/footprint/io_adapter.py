"""HK 데이터 입력 어댑터.

다양한 소스(CSV 파일, pandas DataFrame, dict 리스트)로부터
SatelliteState 리스트를 생성합니다.

향후 API 연동 시 이 모듈에 새 함수를 추가하면 됩니다.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import SatelliteState

KM_TO_M = 1000.0


def from_csv(csv_path: str | Path) -> list[SatelliteState]:
    """Java용 attitude CSV를 읽어 SatelliteState 리스트로 변환.

    CSV 형식: isoDate,px,py,pz,vx,vy,vz,q0,q1,q2,q3
    단위: position/velocity는 m (이미 변환된 상태)
    """
    states = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = datetime.fromisoformat(
                row["isoDate"].replace("Z", "+00:00")
            )
            states.append(SatelliteState(
                timestamp=ts,
                px=float(row["px"]), py=float(row["py"]), pz=float(row["pz"]),
                vx=float(row["vx"]), vy=float(row["vy"]), vz=float(row["vz"]),
                q0=float(row["q0"]), q1=float(row["q1"]),
                q2=float(row["q2"]), q3=float(row["q3"]),
            ))
    return states


def from_dataframe(df: "pandas.DataFrame") -> list[SatelliteState]:
    """pandas DataFrame을 SatelliteState 리스트로 변환.

    필수 컬럼: timestamp, px, py, pz, vx, vy, vz, q0, q1, q2, q3
    position/velocity 단위:
        - km이면 자동으로 m으로 변환 (abs(px) < 100_000 → km으로 판단)
    """
    import pandas as pd

    states = []
    sample_px = abs(df["px"].iloc[0]) if len(df) > 0 else 0
    scale = KM_TO_M if sample_px < 100_000 else 1.0

    for _, row in df.iterrows():
        ts = pd.Timestamp(row["timestamp"]).to_pydatetime()
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        states.append(SatelliteState(
            timestamp=ts,
            px=row["px"] * scale, py=row["py"] * scale, pz=row["pz"] * scale,
            vx=row["vx"] * scale, vy=row["vy"] * scale, vz=row["vz"] * scale,
            q0=row["q0"], q1=row["q1"], q2=row["q2"], q3=row["q3"],
        ))
    return states


def from_dicts(records: list[dict[str, Any]]) -> list[SatelliteState]:
    """dict 리스트를 SatelliteState 리스트로 변환.

    향후 API 응답을 직접 변환할 때 사용합니다.
    """
    states = []
    for r in records:
        ts = r["timestamp"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        states.append(SatelliteState(
            timestamp=ts,
            px=float(r["px"]), py=float(r["py"]), pz=float(r["pz"]),
            vx=float(r["vx"]), vy=float(r["vy"]), vz=float(r["vz"]),
            q0=float(r["q0"]), q1=float(r["q1"]),
            q2=float(r["q2"]), q3=float(r["q3"]),
        ))
    return states


def to_attitude_csv(states: list[SatelliteState], output_path: str | Path) -> Path:
    """SatelliteState 리스트를 Java가 읽을 수 있는 attitude CSV로 저장."""
    output_path = Path(output_path)
    with open(output_path, "w", newline="") as f:
        f.write("isoDate,px,py,pz,vx,vy,vz,q0,q1,q2,q3\n")
        for s in states:
            f.write(s.to_csv_row() + "\n")
    return output_path
