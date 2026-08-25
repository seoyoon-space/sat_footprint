"""Orekit 데이터 자동 다운로드 및 설치.

Orekit 초기화에 필요한 데이터(EOP, 윤초, 행성 위치력 등)를
공식 GitLab에서 git clone으로 다운로드합니다.

사용법:
    python scripts/setup_orekit_data.py
    python scripts/setup_orekit_data.py --output-dir ./data/orekit-data-master
"""
import argparse
import subprocess
import sys
from pathlib import Path

OREKIT_DATA_REPOS = [
    "https://gitlab.orekit.org/orekit/orekit-data.git",
    "https://github.com/eSpace-epfl/orekit-data.git",
]


def clone_orekit_data(output_dir: Path) -> Path:
    if output_dir.exists() and any(output_dir.iterdir()):
        print(f"[skip] {output_dir} already exists")
        return output_dir

    output_dir.parent.mkdir(parents=True, exist_ok=True)

    for repo_url in OREKIT_DATA_REPOS:
        print(f"Trying {repo_url} ...")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(output_dir)],
                check=True,
                timeout=120,
            )
            print(f"Cloned to {output_dir}")
            return output_dir
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            print(f"  Failed: {e}")
            if output_dir.exists():
                import shutil
                shutil.rmtree(output_dir, ignore_errors=True)
            continue

    print("ERROR: All sources failed.", file=sys.stderr)
    sys.exit(1)


def verify(output_dir: Path):
    required = ["tai-utc.dat", "Earth-Orientation-Parameters"]
    missing = [r for r in required if not (output_dir / r).exists()]
    if missing:
        print(f"WARNING: missing expected files: {missing}", file=sys.stderr)
    else:
        print("Verification OK: core data files present.")


def main():
    parser = argparse.ArgumentParser(description="Download Orekit data")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "orekit-data-master",
    )
    args = parser.parse_args()

    clone_orekit_data(args.output_dir)
    verify(args.output_dir)
    print("Done. Orekit data is ready.")


if __name__ == "__main__":
    main()
