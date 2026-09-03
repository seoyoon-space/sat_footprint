"""On-demand ASTGTMV003 DEM 타일 확보.

scripts/setup_dem_aster.py와 동일한 변환 로직을 사용하되, 기존 tile_index.json에
없는 타일만 추가로 내려받아 병합합니다 (기존에 확보된 지역은 건드리지 않음).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

DEFAULT_SRC_DIR = r"\\NST-SATOPS-81\Download\ASTGTMV003\TIF"

# ASTGTM 커버리지 한계
ASTGTM_MIN_LAT = -83
ASTGTM_MAX_LAT = 83


def _tile_filename(lat: int, lon: int) -> str:
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"ASTGTMV003_{ns}{abs(lat):02d}{ew}{abs(lon):03d}_dem.tif"

def _tile_name(lat: int, lon: int) -> str:
    return f"tile_{lat:+03d}_{lon:+04d}"


def _load_index(index_path: Path) -> list[dict]:
    if not index_path.exists():
        return []
    with open(index_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _covers(record: dict, lat: int, lon: int) -> bool:
    return (
        record["min_lat"] <= lat + 0.5 <= record["max_lat"]
        and record["min_lon"] <= lon + 0.5 <= record["max_lon"]
    )


def ensure_dem_tiles(
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    tiles_dir: str | Path,
    index_path: str | Path,
    src_dir: str | Path = DEFAULT_SRC_DIR,
) -> dict:
    """bbox를 커버하는 데 필요한 DEM 타일이 로컬에 없으면 네트워크 서버에서 내려받아
    tile_index.json에 병합합니다.

    Returns:
        {"added": [tile_name, ...], "missing": [filename, ...], "index_path": str}
    """
    import numpy as np
    import rasterio

    tiles_dir = Path(tiles_dir)
    index_path = Path(index_path)
    src_dir = Path(src_dir)
    tiles_dir.mkdir(parents=True, exist_ok=True)

    south = max(ASTGTM_MIN_LAT, math.floor(min_lat))
    north = min(ASTGTM_MAX_LAT, math.ceil(max_lat))
    west = math.floor(min_lon)
    east = math.ceil(max_lon)

    records = _load_index(index_path)
    added = []
    missing = []

    for lat in range(int(south), int(north)):
        for lon in range(int(west), int(east)):
            if any(_covers(r, lat, lon) for r in records):
                continue

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

            tile_name = _tile_name(lat, lon)
            data_south_to_north = np.flipud(data).astype(np.float32)
            bin_path = tiles_dir / f"{tile_name}.bin"
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
            added.append(tile_name)

    if added:
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)

    return {"added": added, "missing": missing, "index_path": str(index_path)}
