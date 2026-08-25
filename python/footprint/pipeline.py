"""메인 파이프라인: HK 데이터 → Java/Rugged → Footprint 결과.

전체 흐름:
    1. SatelliteState 리스트를 attitude CSV로 저장
    2. Java FootprintCalculator를 subprocess로 호출
    3. 출력 CSV를 파싱해서 FootprintLine 리스트로 반환
"""
from __future__ import annotations

import csv
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .io_adapter import to_attitude_csv
from .models import FootprintLine, SatelliteState, SensorConfig


@dataclass
class PipelineConfig:
    """파이프라인 실행에 필요한 경로 설정."""
    java_home: str
    maven_home: str
    java_project_dir: str
    tile_index_path: str
    orekit_data_path: str


def compute_footprint(
    states: list[SatelliteState],
    config: PipelineConfig,
    start_utc: str | None = None,
    end_utc: str | None = None,
    line_step: int = 100,
    sensor: SensorConfig | None = None,
) -> list[FootprintLine]:
    """HK 자세 데이터로부터 footprint를 계산합니다.

    Args:
        states: 위성 자세 데이터 리스트 (시간순)
        config: Java 실행 환경 설정
        start_utc: 촬영 시작 시각 (ISO8601, UTC). None이면 states의 첫 시각
        end_utc: 촬영 종료 시각 (ISO8601, UTC). None이면 states의 마지막 시각
        line_step: 라인 계산 간격 (기본 100 = lineRate에 따라 약 1초 간격)
        sensor: 센서 스펙. None이면 MultiScape200 기본값 사용

    Returns:
        FootprintLine 리스트 (라인별 좌우 끝점 좌표)
    """
    if not states:
        raise ValueError("states가 비어있습니다.")

    if sensor is None:
        sensor = SensorConfig()

    if start_utc is None:
        start_utc = states[0].timestamp.strftime("%Y-%m-%dT%H:%M:%S")
    if end_utc is None:
        end_utc = states[-1].timestamp.strftime("%Y-%m-%dT%H:%M:%S")

    with tempfile.TemporaryDirectory() as tmpdir:
        att_csv = Path(tmpdir) / "attitude.csv"
        out_csv = Path(tmpdir) / "footprint.csv"

        to_attitude_csv(states, att_csv)

        _run_java(config, str(att_csv), start_utc, end_utc, line_step, str(out_csv))

        return _parse_footprint_csv(out_csv)


def compute_footprint_to_dataframe(
    states: list[SatelliteState],
    config: PipelineConfig,
    **kwargs,
) -> pd.DataFrame:
    """compute_footprint의 결과를 pandas DataFrame으로 반환."""
    lines = compute_footprint(states, config, **kwargs)
    if not lines:
        return pd.DataFrame()
    return pd.DataFrame([vars(fl) for fl in lines])


def _run_java(
    config: PipelineConfig,
    att_csv: str,
    start_utc: str,
    end_utc: str,
    line_step: int,
    out_csv: str,
) -> subprocess.CompletedProcess:
    """Java FootprintCalculator를 실행합니다."""
    env = {
        "JAVA_HOME": config.java_home,
        "PATH": (
            f"{config.java_home}\\bin;"
            f"{config.maven_home}\\bin;"
            + subprocess.os.environ.get("PATH", "")
        ),
        **{k: v for k, v in subprocess.os.environ.items()
           if k not in ("JAVA_HOME", "PATH")},
    }

    args = " ".join([
        att_csv,
        config.tile_index_path,
        start_utc,
        end_utc,
        str(line_step),
        out_csv,
        config.orekit_data_path,
    ])

    cmd = [
        f"{config.maven_home}\\bin\\mvn.cmd",
        "exec:java", "-q",
        f"-Dexec.mainClass=footprint.Main",
        f"-Dexec.args={args}",
    ]

    result = subprocess.run(
        cmd,
        cwd=config.java_project_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Java footprint 계산 실패 (exit {result.returncode}):\n"
            f"{result.stderr[:1000]}"
        )

    return result


def _parse_footprint_csv(csv_path: Path) -> list[FootprintLine]:
    """Java가 출력한 footprint CSV를 파싱합니다."""
    if not csv_path.exists():
        return []

    lines = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lines.append(FootprintLine(
                line=int(row["line"]),
                time_utc=row["time"],
                left_lat=float(row["leftLat"]),
                left_lon=float(row["leftLon"]),
                left_alt=float(row["leftAlt"]),
                right_lat=float(row["rightLat"]),
                right_lon=float(row["rightLon"]),
                right_alt=float(row["rightAlt"]),
            ))
    return lines
