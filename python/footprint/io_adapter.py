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

    타임스탬프(NaT)나 자세/위치 값(NaN)이 비어있는 행은 제외한다. HK 패킷 병합 시
    구간 경계 근처에서 보간이 안 된 행이 섞여 나올 수 있는데, 그대로 CSV로 내보내면
    "NaT"/"nan" 문자열이 그대로 찍혀 Java 쪽 파싱이 실패한다.

    GPS 미획득 등으로 위치가 (0,0,0)이나 (1,0,0) 같은 placeholder 값으로 찍히는 구간도
    확인됨 — NaN이 아니라서 dropna로는 안 걸러지지만, 이런 물리적으로 말이 안 되는
    (원점 근처) 값이 하나라도 섞여 들어가면 Orekit 궤도 피팅 전체가 깨진다. 실제
    위성 위치 크기는 항상 수천 km(지구 반지름 ~6378km + 고도) 이상이므로, km/m 단위
    판별과 무관하게 확실히 구분되는 낮은 문턱값으로 걸러낸다.
    """
    import pandas as pd

    required_cols = ["timestamp", "px", "py", "pz", "vx", "vy", "vz", "q0", "q1", "q2", "q3"]
    df = df.dropna(subset=required_cols)

    MIN_POSITION_MAGNITUDE = 100.0  # km이든 m이든 실제 위성 위치보다 훨씬 작은 값
    pos_magnitude = (df["px"] ** 2 + df["py"] ** 2 + df["pz"] ** 2) ** 0.5
    df = df[pos_magnitude > MIN_POSITION_MAGNITUDE]

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
            q0=row["q0"], q1=row["q1"], q2=row["q2"], q3=row["q3"]
        ))
    return states


def find_gap_in_range(
    states: list[SatelliteState],
    range_start: datetime,
    range_end: datetime,
    max_gap_sec: float = 30.0,
) -> tuple[datetime, datetime] | None:
    """states 사이에 max_gap_sec을 넘는 시간 공백이 있고, 그 공백이 [range_start,
    range_end]와 겹치면 (gap_start, gap_end)를 반환. 없으면 None.

    HK 텔레메트리는 보통 촘촘한 간격(O1A/O1B 모두 관측상 ~10초)으로 들어오는데,
    GPS dropout 등으로 실제로 몇 분씩 비는 구간이 있다 (from_dataframe의
    MIN_POSITION_MAGNITUDE 필터가 그 구간의 placeholder 위치값을 걷어내면서 생김).
    이 공백을 걸치는 footprint 라인을 Orekit/Rugged에 그냥 넘기면, 그 구간에서
    무리하게 보간된 궤적을 DEM과 교차시키려다 계산이 수 분/수 GB로 폭주하는 게
    확인됐다 (O1B_04186_GGD 테스트 중 140초 공백에서 재현). Java를 부르기 전에
    미리 걸러내 빠르게 에러를 낸다.
    """
    for i in range(1, len(states)):
        gap_start = states[i - 1].timestamp
        gap_end = states[i].timestamp
        if (gap_end - gap_start).total_seconds() > max_gap_sec:
            if gap_start < range_end and gap_end > range_start:
                return gap_start, gap_end
    return None


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
