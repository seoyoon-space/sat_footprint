"""
sat_footprint 사용 예시.

이 예시는 전체 파이프라인의 사용법을 보여줍니다:
    1. HK 데이터를 SatelliteState로 변환
    2. Java/Rugged를 호출해 footprint 계산
    3. 결과를 DataFrame으로 확인

실행 전 필요사항:
    - Java 17 + Maven 설치
    - java/ 디렉토리에서 mvn compile 실행
    - orekit-data-master 디렉토리 준비
    - DEM 타일 준비 (tile_index.json + .bin 파일들)
"""
import sys
from pathlib import Path

# sat_footprint 모듈 경로 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python"))

from footprint.models import SatelliteState, SensorConfig
from footprint.io_adapter import from_csv, from_dataframe, to_attitude_csv
from footprint.pipeline import PipelineConfig, compute_footprint, compute_footprint_to_dataframe


def example_from_csv():
    """이미 만들어진 attitude CSV에서 footprint 계산."""

    project_root = Path(__file__).resolve().parent.parent

    config = PipelineConfig(
        java_home=r"C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot",
        maven_home=r"C:\Users\NST_SYLEE\AppData\Local\Programs\apache-maven-3.9.16",
        java_project_dir=str(project_root / "java"),
        tile_index_path=str(project_root / "data" / "tiles" / "tile_index.json"),
        orekit_data_path=str(project_root / "data" / "orekit-data-master"),
    )

    # 1) CSV에서 SatelliteState 로드
    states = from_csv("path/to/attitude.csv")
    print(f"Loaded {len(states)} states")

    # 2) Footprint 계산
    result_df = compute_footprint_to_dataframe(
        states=states,
        config=config,
        line_step=100,
    )

    print(result_df)


def example_from_hk_loader():
    """hk_loader에서 직접 HK 데이터를 가져와 footprint 계산.

    hk_loader가 설치된 환경에서만 동작합니다.
    """
    project_root = Path(__file__).resolve().parent.parent
    HK_LOADER_ROOT = project_root / "python" / "hk_loader"
    sys.path.insert(0, str(HK_LOADER_ROOT))

    from dotenv import load_dotenv
    load_dotenv(HK_LOADER_ROOT / ".env")

    from core.loader.hk_loader import HKLoader, extract_attitude_columns

    # 1) HK DB에서 데이터 로드
    loader = HKLoader.from_env()
    df = loader.load(
        start_time="2026-08-16T11:58:00+09:00",
        end_time="2026-08-16T12:03:00+09:00",
        packets=["hk1", "hk2"],
    )
    att = extract_attitude_columns(df, verbose=True)
    print(f"Loaded {len(att)} attitude rows from HK DB")

    # 2) DataFrame → SatelliteState (km → m 자동 변환)
    states = from_dataframe(att)
    print(f"Converted to {len(states)} SatelliteState objects")

    # 3) Footprint 계산
    project_root = Path(__file__).resolve().parent.parent

    config = PipelineConfig(
        java_home=r"C:\Program Files\Microsoft\jdk-17.0.20.101-hotspot",
        maven_home=r"C:\Users\NST_SYLEE\AppData\Local\Programs\apache-maven-3.9.16",
        java_project_dir=str(project_root / "java"),
        tile_index_path=str(project_root / "data" / "tiles" / "tile_index.json"),
        orekit_data_path=str(project_root / "data" / "orekit-data-master"),
    )

    result_df = compute_footprint_to_dataframe(
        states=states,
        config=config,
        line_step=100,
    )

    print(f"\n=== Footprint Result ({len(result_df)} lines) ===")
    print(result_df.to_string(index=False))


if __name__ == "__main__":
    # example_from_csv()
    example_from_hk_loader()
