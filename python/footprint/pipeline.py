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
from datetime import datetime
from pathlib import Path

import pandas as pd

from .io_adapter import to_attitude_csv
from .models import FootprintLine, SatelliteState, SensorConfig

# line_step은 라인 인덱스 간격이지 시간 간격이 아니다 (Main.java가 elapsed time *
# lineRate로 startLine/endLine을 뽑음, MultiScape200 기준 ~2600 lines/sec) — 고정값을
# 그대로 넓은 창에 쓰면 출력 포인트 수(및 Rugged 계산량/메모리)가 창 길이에 비례해서
# 불어나 결국 Java OOM까지 간다 (Error라 Main.java의 per-line try/catch로도 못 잡음).
# 40초 창 기준으로 튜닝된 100을 그대로 10분짜리 창에 쓰면 15000+ 포인트/200초+ 걸림.
_BASELINE_WINDOW_SEC = 40.0
_BASELINE_LINE_STEP = 100


def _default_line_step(start_utc: str, end_utc: str) -> int:
    """요청 구간 길이에 비례해 안전한 line_step을 계산 (호출자가 line_step을 명시
    안 했을 때 compute_footprint()가 자동으로 씀 — 참고: 이 계산이 예전엔 attitude-viewer
    의 Flask 라우트 핸들러 안에만 있어서, 이 함수를 다른 경로로 호출하면 안전장치가
    아예 안 걸렸다)."""
    window_sec = max(1.0, (datetime.fromisoformat(end_utc) - datetime.fromisoformat(start_utc)).total_seconds())
    return max(_BASELINE_LINE_STEP, round(_BASELINE_LINE_STEP * window_sec / _BASELINE_WINDOW_SEC))


@dataclass
class PipelineConfig:
    """파이프라인 실행에 필요한 경로 설정."""
    java_home: str
    maven_home: str
    java_project_dir: str
    tile_index_path: str
    orekit_data_path: str
    # data/sensor_calibration.json 경로 — 위성별 EOC(카메라) 마운팅 보정 unit vector.
    # None이면 Java 쪽에서 무보정으로 처리 (SensorCalibration.java 참고).
    sensor_calibration_path: str | None = None


def compute_footprint(
    states: list[SatelliteState],
    config: PipelineConfig,
    start_utc: str | None = None,
    end_utc: str | None = None,
    line_step: int | None = None,
    sensor: SensorConfig | None = None,
    satellite_id: str = "O1A",
) -> list[FootprintLine]:
    """HK 자세 데이터로부터 footprint를 계산합니다.

    Args:
        states: 위성 자세 데이터 리스트 (시간순)
        config: Java 실행 환경 설정
        start_utc: 촬영 시작 시각 (ISO8601, UTC). None이면 states의 첫 시각
        end_utc: 촬영 종료 시각 (ISO8601, UTC). None이면 states의 마지막 시각
        line_step: 라인 계산 간격. None(기본)이면 start_utc~end_utc 길이에 비례해
            자동 계산(_default_line_step 참고) — 넓은 창에 작은 고정값을 그대로 쓰면
            Java가 OOM 날 수 있어, 명시적으로 다른 값이 필요한 게 아니면 지정하지 말 것.
        sensor: 센서 스펙. None이면 MultiScape200 기본값 사용
        satellite_id: config.sensor_calibration_path에서 EOC 마운팅 보정값을 찾을 때
            쓰는 위성 키 (예: "O1A", "O1B"). 기본 "O1A".

    Returns:
        FootprintLine 리스트 (라인별 좌우 끝점 좌표)
    """
    if not states:
        raise ValueError("states가 비어있습니다.")

    if sensor is None:
        sensor = SensorConfig()

    if start_utc is None:
        start_utc = states[0].timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")
    if end_utc is None:
        end_utc = states[-1].timestamp.strftime("%Y-%m-%dT%H:%M:%S.%f")

    if line_step is None:
        line_step = _default_line_step(start_utc, end_utc)

    with tempfile.TemporaryDirectory() as tmpdir:
        att_csv = Path(tmpdir) / "attitude.csv"
        out_csv = Path(tmpdir) / "footprint.csv"

        to_attitude_csv(states, att_csv)

        _run_java(config, str(att_csv), start_utc, end_utc, line_step, str(out_csv), satellite_id)

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
    satellite_id: str = "O1A",
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
        satellite_id,
        config.sensor_calibration_path or "",
    ])

    cmd = [
        f"{config.maven_home}\\bin\\mvn.cmd",
        "exec:java", "-q",
        f"-Dexec.mainClass=footprint.Main",
        f"-Dexec.args={args}",
    ]

    # 300s: the footprint line window can now span up to ~10 minutes (attitude-viewer's
    # sidebar widened it from ~40s), which means proportionally more Rugged/DEM
    # intersection work per request — 120s was tuned for the old, much shorter window.
    proc = subprocess.Popen(
        cmd,
        cwd=config.java_project_dir,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=300)
    except subprocess.TimeoutExpired:
        # subprocess.run(timeout=...)의 기본 kill()은 직접 자식(mvn.cmd)만 죽이는데,
        # Windows에서는 실제 계산을 하는 java.exe가 그 밑의 손자 프로세스라 안 죽는다 —
        # 그러면 capture_output이 그 java.exe가 물고 있는 stdout/stderr 파이프가 안
        # 닫혀서 communicate()가 타임아웃 이후에도 영원히 멈춘다 (O1B 테스트 중 실제로
        # 300초를 넘겨서도 응답이 안 왔던 원인 — "5분 타임아웃"이 사실상 안 걸렸던 것).
        # taskkill /T(트리 전체)로 mvn.cmd + java.exe를 다 죽여야 파이프가 실제로 닫힌다.
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, text=True,
        )
        proc.communicate()  # 이제 다 죽었으니 즉시 EOF를 만나 리턴한다
        raise

    if proc.returncode != 0:
        raise RuntimeError(
            f"Java footprint 계산 실패 (exit {proc.returncode}):\n"
            f"{stderr[:1000]}\n{stdout[:1000]}"
        )

    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


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
