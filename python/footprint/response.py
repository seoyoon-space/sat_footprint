"""Footprint 계산 결과(FootprintLine DataFrame)를 웹 API 응답 형태(JSON-serializable
dict, GeoJSON)로 변환하는 순수 함수들.

Flask(request/jsonify)에 대한 의존이 전혀 없다 — attitude-viewer/app.py의 라우트
핸들러들이 이 함수들의 결과를 그대로 jsonify()에 넘기기만 한다. 원래 app.py 안에
있었는데, 라우팅과 무관한 순수 데이터 변환 로직이라 Flask 없이도 테스트/재사용할
수 있도록 이쪽으로 옮겼다.
"""
from __future__ import annotations

import pandas as pd


def load_footprint_rows(csv_path):
    """Serve precomputed footprint CSV as a DataFrame for the 2D map."""
    df = pd.read_csv(csv_path)
    df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)
    return df


def is_target_on_scan_line(row, target):
    lat = float(target["lat"])
    lon = float(target["lon"])
    latitudes = [float(row["left_lat"]), float(row["right_lat"])]
    longitudes = [float(row["left_lon"]), float(row["right_lon"])]
    return (
        min(latitudes) <= lat <= max(latitudes)
        and min(longitudes) <= lon <= max(longitudes)
    )


def capture_events(df, target):
    hits = df[df.apply(is_target_on_scan_line, axis=1, target=target)]
    if hits.empty:
        return []
    return [{
        "target": target["name"],
        "start": hits["time_utc"].iloc[0].isoformat().replace("+00:00", "Z"),
        "end": hits["time_utc"].iloc[-1].isoformat().replace("+00:00", "Z"),
        "line_start": int(hits["line"].iloc[0]),
        "line_end": int(hits["line"].iloc[-1]),
        "samples": int(len(hits)),
    }]


def footprint_to_geojson(df, target):
    """Footprint 스캔 라인들(왼쪽/오른쪽 지상점)을 표준 GeoJSON Polygon으로 변환.

    각 꼭짓점 좌표는 [lon, lat, alt] 3차원 — RFC 7946이 지원하는 형식으로, 고도값이
    좌표 자체에 실려있어 QGIS 등 표준 GIS 도구에서 바로 지형 높이로 인식된다.
    파일 내보내기/웹 지도 렌더링 양쪽에 그대로 쓸 수 있도록 전체(비샘플링) 라인을 사용.
    """
    if df is None or df.empty:
        return None

    left = [[round(r.left_lon, 6), round(r.left_lat, 6), round(r.left_alt, 1)] for r in df.itertuples()]
    right = [[round(r.right_lon, 6), round(r.right_lat, 6), round(r.right_alt, 1)] for r in df.itertuples()]
    ring = left + list(reversed(right)) + [left[0]]

    return {
        "type": "Feature",
        "properties": {
            "target_name": target.get("name"),
            "target_lat": target.get("lat"),
            "target_lon": target.get("lon"),
            "line_count": len(df),
        },
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def footprint_dataframe_to_response(df, target, total=None, geojson_df=None):
    """FootprintLine 컬럼(line/time_utc/left_*/right_*)을 가진 DataFrame을
    /api/footprint 응답 JSON 형태로 변환. 정적 CSV와 실시간 계산 결과 모두에 사용.

    geojson_df: GeoJSON 추출에 쓸 별도의(보통 더 좁은 "scan time" 기준) 서브셋.
    생략하면 지도에 그려지는 것과 동일하게 df 전체를 사용한다.
    """
    if df is None or df.empty:
        return {"lines": [], "target": target, "total": 0, "sampled": 0, "capture_events": [], "geojson": None}

    if not pd.api.types.is_datetime64_any_dtype(df["time_utc"]):
        df = df.copy()
        df["time_utc"] = pd.to_datetime(df["time_utc"], utc=True)

    if geojson_df is None:
        geojson_df = df
    elif not geojson_df.empty and not pd.api.types.is_datetime64_any_dtype(geojson_df["time_utc"]):
        geojson_df = geojson_df.copy()
        geojson_df["time_utc"] = pd.to_datetime(geojson_df["time_utc"], utc=True)

    step = max(1, len(df) // 500)
    sampled = df.iloc[::step]

    lines = []
    for _, r in sampled.iterrows():
        line_time = r["time_utc"].isoformat().replace("+00:00", "Z")
        lines.append({
            "t": line_time,
            "ll": [round(r["left_lat"], 5), round(r["left_lon"], 5)],
            "rl": [round(r["right_lat"], 5), round(r["right_lon"], 5)],
            "la": round(r["left_alt"], 1),
            "ra": round(r["right_alt"], 1),
            "capture": is_target_on_scan_line(r, target),
        })

    return {
        "lines": lines,
        "target": target,
        "total": total if total is not None else len(df),
        "sampled": len(lines),
        "capture_events": capture_events(df, target),
        "geojson": footprint_to_geojson(geojson_df, target),
    }
