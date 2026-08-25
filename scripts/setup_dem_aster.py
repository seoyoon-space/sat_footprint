"""ASTGTMV003 DEM 타일 준비: 네트워크 서버 GeoTIFF → Rugged용 binary + tile_index.json.

회사 네트워크 서버(\\NST-SATOPS-81)에 저장된 ASTGTMV003 전세계 DEM 타일을
Rugged TileUpdater가 읽을 수 있는 형태(float32 binary + JSON index)로 변환합니다.

사전 준비:
    1. pip install rasterio numpy
    2. \\NST-SATOPS-81\Download\ASTGTMV003\TIF\ 경로 접근 가능 확인

사용법:
    python scripts/setup_dem_aster.py

    # 특정 영역만:
    python scripts/setup_dem_aster.py --south 37.0 --north 38.0 --west 126.0 --east 128.0

    # 서버 경로 변경:
    python scripts/setup_dem_aster.py --src-dir "Z:\\DEM\\ASTGTMV003"
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
import rasterio

DEFAULT_SOUTH = 33.0
DEFAULT_NORTH = 43.0
DEFAULT_WEST = 124.0
DEFAULT_EAST = 132.0

DEFAULT_SRC_DIR = r"\\NST-SATOPS-81\Download\ASTGTMV003\TIF"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TILES_DIR = DATA_DIR / "tiles"


def _tile_filename(lat: int, lon: int) -> str:
    """ASTGTMV003 파일명 생성. 예: ASTGTMV003_N37E127_dem.tif"""
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"ASTGTMV003_{ns}{abs(lat):02d}{ew}{abs(lon):03d}_dem.tif"


def convert_tiles(src_dir: Path, out_dir: Path,
                  south: int, north: int, west: int, east: int) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    missing = []

    for lat in range(south, north):
        for lon in range(west, east):
            fname = _tile_filename(lat, lon)
            src_path = src_dir / fname

            if not src_path.exists():
                missing.append(fname)
                continue

            with rasterio.open(src_path) as src:
                data = src.read(1)
                pixel_size_x = src.transform.a
                pixel_size_y = -src.transform.e
                rows, cols = data.shape
                tile_west, tile_south, tile_east, tile_north = src.bounds

                nodata = src.nodata
                if nodata is not None:
                    data = np.where(data == nodata, 0.0, data)

            tile_name = f"tile_{lat:+03d}_{lon:+04d}"

            # Rugged용 raw float32 binary (row0=South, col0=West)
            data_south_to_north = np.flipud(data).astype(np.float32)
            bin_path = out_dir / f"{tile_name}.bin"
            data_south_to_north.tofile(bin_path)

            records.append({
                "path": str(src_path),
                "data_path": str(bin_path),
                "min_lat": round(tile_south, 6),
                "max_lat": round(tile_north, 6),
                "min_lon": round(tile_west, 6),
                "max_lon": round(tile_east, 6),
                "lat_step_deg": round(pixel_size_y, 8),
                "lon_step_deg": round(pixel_size_x, 8),
                "rows_px": rows,
                "cols_px": cols,
                "overlap_cells": 0,
            })

            size_mb = bin_path.stat().st_size / 1024 / 1024
            print(f"  [{len(records):3d}] {fname} -> {tile_name}.bin ({size_mb:.1f} MB)")

    if missing:
        print(f"\n[warn] {len(missing)}개 타일 누락 (서버에 파일 없음):", file=sys.stderr)
        for m in missing[:10]:
            print(f"  - {m}", file=sys.stderr)
        if len(missing) > 10:
            print(f"  ... 외 {len(missing) - 10}개", file=sys.stderr)

    index_path = out_dir / "tile_index.json"
    with open(index_path, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"\nCreated {len(records)} tiles, index -> {index_path}")
    return records


def main():
    parser = argparse.ArgumentParser(
        description="Convert ASTGTMV003 DEM tiles for Rugged")
    parser.add_argument("--src-dir", type=str, default=DEFAULT_SRC_DIR,
                        help="ASTGTMV003 GeoTIFF 소스 디렉터리")
    parser.add_argument("--south", type=int, default=DEFAULT_SOUTH)
    parser.add_argument("--north", type=int, default=DEFAULT_NORTH)
    parser.add_argument("--west", type=int, default=DEFAULT_WEST)
    parser.add_argument("--east", type=int, default=DEFAULT_EAST)
    args = parser.parse_args()

    src_dir = Path(args.src_dir)
    if not src_dir.exists():
        print(f"ERROR: 소스 디렉터리 접근 불가: {src_dir}", file=sys.stderr)
        print("네트워크 드라이브 연결 상태를 확인하세요.", file=sys.stderr)
        sys.exit(1)

    print(f"ASTGTMV003 DEM 변환")
    print(f"  소스: {src_dir}")
    print(f"  영역: ({args.south}, {args.west}) ~ ({args.north}, {args.east})")
    print(f"  출력: {TILES_DIR}\n")

    convert_tiles(src_dir, TILES_DIR, args.south, args.north, args.west, args.east)
    print("\nDone. DEM tiles are ready.")


if __name__ == "__main__":
    main()
