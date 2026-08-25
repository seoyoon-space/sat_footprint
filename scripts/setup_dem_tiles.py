"""DEM 타일 자동 준비: 다운로드 → 타일링 → tile_index.json 생성.

Copernicus GLO-30 DEM을 OpenTopography API에서 다운로드하고,
Rugged TileUpdater가 읽을 수 있는 형태(1°×1° float32 binary + JSON index)로 변환합니다.

사전 준비:
    1. pip install requests rasterio numpy
    2. https://portal.opentopography.org 에서 무료 계정 생성 후 API 키 발급

사용법:
    python scripts/setup_dem_tiles.py --api-key YOUR_API_KEY

    # 한국 전체 대신 특정 영역만:
    python scripts/setup_dem_tiles.py --api-key KEY --south 37.0 --north 38.0 --west 126.0 --east 128.0
"""
import argparse
import json
import os
import struct
import sys
from pathlib import Path

import numpy as np
import requests
import rasterio
from rasterio.windows import Window


DEFAULT_SOUTH = 36.0
DEFAULT_NORTH = 39.0
DEFAULT_WEST = 125.0
DEFAULT_EAST = 130.0

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
TILES_DIR = DATA_DIR / "tiles"


def download_glo30(south: float, north: float, west: float, east: float,
                    api_key: str, out_path: Path) -> Path:
    if out_path.exists():
        print(f"[skip] {out_path} already exists")
        return out_path

    url = "https://portal.opentopography.org/API/globaldem"
    params = {
        "demtype": "COP30",
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "outputFormat": "GTiff",
        "API_Key": api_key,
    }

    print(f"Downloading GLO-30 DEM for ({south}, {west}) ~ ({north}, {east}) ...")
    resp = requests.get(url, params=params, timeout=300)

    if resp.status_code != 200:
        print(f"ERROR: status={resp.status_code}", file=sys.stderr)
        print(resp.text[:500], file=sys.stderr)
        sys.exit(1)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(resp.content)
    print(f"Saved {len(resp.content) / 1024 / 1024:.1f} MB -> {out_path}")
    return out_path


def tile_with_overlap(src_path: Path, outdir: Path,
                      tile_deg: float = 1.0, overlap_cells: int = 2) -> list:
    outdir.mkdir(parents=True, exist_ok=True)
    records = []

    with rasterio.open(src_path) as src:
        pixel_size_x = src.transform.a
        pixel_size_y = -src.transform.e
        tile_px_w = int(round(tile_deg / pixel_size_x))
        tile_px_h = int(round(tile_deg / pixel_size_y))

        west, south, east, north = src.bounds
        n_cols = int(np.ceil((east - west) / tile_deg))
        n_rows = int(np.ceil((north - south) / tile_deg))

        print(f"Tiling: {src.width}x{src.height}px -> {n_cols}x{n_rows} tiles (overlap={overlap_cells})")

        for row in range(n_rows):
            for col in range(n_cols):
                tile_west = west + col * tile_deg
                tile_north = north - row * tile_deg

                col_off = max(0, int(round((tile_west - west) / pixel_size_x)) - overlap_cells)
                row_off = max(0, int(round((north - tile_north) / pixel_size_y)) - overlap_cells)
                width = min(src.width - col_off, tile_px_w + 2 * overlap_cells)
                height = min(src.height - row_off, tile_px_h + 2 * overlap_cells)

                if width <= 0 or height <= 0:
                    continue

                window = Window(col_off, row_off, width, height)
                transform = src.window_transform(window)
                data = src.read(1, window=window)

                if np.all(data == src.nodata):
                    continue

                tile_name = f"tile_{row:03d}_{col:03d}"

                tif_path = outdir / f"{tile_name}.tif"
                out_meta = src.meta.copy()
                out_meta.update({"height": height, "width": width, "transform": transform})
                with rasterio.open(tif_path, "w", **out_meta) as dst:
                    dst.write(data, 1)

                # Rugged용 raw float32 binary (row0=South, col0=West)
                data_south_to_north = np.flipud(data).astype(np.float32)
                bin_path = outdir / f"{tile_name}.bin"
                data_south_to_north.tofile(bin_path)

                actual_west, actual_north = transform * (0, 0)
                actual_east, actual_south = transform * (width, height)

                records.append({
                    "path": str(tif_path),
                    "data_path": str(bin_path),
                    "row": row,
                    "col": col,
                    "min_lat": round(actual_south, 6),
                    "max_lat": round(actual_north, 6),
                    "min_lon": round(actual_west, 6),
                    "max_lon": round(actual_east, 6),
                    "lat_step_deg": round(pixel_size_y, 8),
                    "lon_step_deg": round(pixel_size_x, 8),
                    "rows_px": height,
                    "cols_px": width,
                    "overlap_cells": overlap_cells,
                })

    index_path = outdir / "tile_index.json"
    with open(index_path, "w") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"Created {len(records)} tiles, index -> {index_path}")
    return records


def main():
    parser = argparse.ArgumentParser(description="Download and tile GLO-30 DEM for Rugged")
    parser.add_argument("--api-key", required=True, help="OpenTopography API key")
    parser.add_argument("--south", type=float, default=DEFAULT_SOUTH)
    parser.add_argument("--north", type=float, default=DEFAULT_NORTH)
    parser.add_argument("--west", type=float, default=DEFAULT_WEST)
    parser.add_argument("--east", type=float, default=DEFAULT_EAST)
    parser.add_argument("--tile-deg", type=float, default=1.0)
    parser.add_argument("--overlap-cells", type=int, default=2)
    args = parser.parse_args()

    raw_tif = DATA_DIR / "dem_raw.tif"
    download_glo30(args.south, args.north, args.west, args.east, args.api_key, raw_tif)
    tile_with_overlap(raw_tif, TILES_DIR, args.tile_deg, args.overlap_cells)

    print("Done. DEM tiles are ready.")


if __name__ == "__main__":
    main()
