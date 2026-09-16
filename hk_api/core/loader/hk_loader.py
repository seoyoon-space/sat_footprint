"""HK 텔레메트리 로더 - 라이브러리(HKLoader) + CLI 겸용.

설치/환경변수/CLI 예시/시간 입력 형식은 README 참고 (여기 문서는 코드 계약만 다룸).
빠른 예시: `python -m core.loader.hk_loader --start-time "2026-08-10" --end-time "2026-08-14" --output hk.csv`
"""
from __future__ import annotations

import argparse
import json
import logging
import numbers
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .schema_map import (
    DEFAULT_MERGE_TOLERANCE_SEC,
    MASTER_PACKET,
    PacketSpec,
    get_hk_packet_schema,
)
from .time_sync import merge_packets, slice_time_range

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))


def _normalize_query_time(value: str | datetime | int | float, *, is_end: bool = False) -> int:
    """사용자 입력을 Unix epoch(UTC, second)로 변환.

    - KST 일자 문자열: "2026-08-20" 또는 "2026-08-20T00:00:00"
    - epoch integer: "1787203236"
    - ISO8601 문자열: "2026-08-20T00:00:00Z" / "+09:00"
    """
    if value is None:
        raise ValueError("time value is required")

    if isinstance(value, (int, float)):
        return int(value)

    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        return int(dt.astimezone(timezone.utc).timestamp())

    text_value = str(value).strip()
    if not text_value:
        raise ValueError("time value is empty")

    if text_value.isdigit():
        return int(text_value)

    if len(text_value) == 10 and text_value.count("-") == 2 and text_value[4] == "-" and text_value[7] == "-":
        dt = datetime.fromisoformat(text_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=KST)
        if is_end:
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(dt.astimezone(timezone.utc).timestamp())

    dt = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return int(dt.astimezone(timezone.utc).timestamp())


def _build_tolerance_overrides(
    schema: dict[str, PacketSpec], packet_names: list[str], merge_tolerance_sec: float
) -> dict[str, float]:
    """패킷별 asof-merge 허용 오차를 PacketSpec.rate_hz로부터 계산.

    패킷 주기(1/rate_hz)의 절반을 자연스러운 최소 허용 오차로 보고, 
    사용자가 지정한 merge_tolerance_sec보다 더 넓게 필요한 패킷에 한해서만
    override.(즉 merge_tolerance_sec을 하한으로 취급 -> 절대 더 좁아지지 않음).
    """
    return {
        name: max(merge_tolerance_sec, 0.5 / schema[name].rate_hz)
        for name in packet_names
        if schema[name].rate_hz > 0
    }


# DB의 qbody_wrt_eci1..4는 scalar-last(x,y,z,w)(실측 검증됨) - core 전체가 쓰는
# scalar-first(w,x,y,z)로 여기서 한 번 재정렬(컬럼명은 그대로 유지).
# q_ecef_wrt_eci1..4/cmd_q_body_wrt_eci1..4는 미사용/미확인이라 대상에서 제외.
_QUATERNION_SCALAR_LAST_GROUPS: tuple[tuple[str, str, str, str], ...] = (
    ("qbody_wrt_eci1", "qbody_wrt_eci2", "qbody_wrt_eci3", "qbody_wrt_eci4"),
)


def _reorder_scalar_last_quaternions(df: pd.DataFrame) -> pd.DataFrame:
    for col1, col2, col3, col4 in _QUATERNION_SCALAR_LAST_GROUPS:
        if all(c in df.columns for c in (col1, col2, col3, col4)):
            x, y, z, w = df[col1].copy(), df[col2].copy(), df[col3].copy(), df[col4].copy()
            df[col1], df[col2], df[col3], df[col4] = w, x, y, z
    return df


# qbody_wrt_eci1..4는 실제로 ECI->Body 회전(DEM 서버가 conjugate로 뒤집어 쓰는 것으로 확인됨) -
# Body->ECI를 가정하므로(quaternion_body2eci), 여기서 켤레를 취해 뒤집는다.
# scalar-first로 이미 재정렬된 뒤 호출되므로, w(col1)는 두고 x,y,z(col2~4) 부호만 뒤집으면 된다.
def _invert_quaternion_rotation_direction(df: pd.DataFrame) -> pd.DataFrame:
    for col1, col2, col3, col4 in _QUATERNION_SCALAR_LAST_GROUPS:
        if all(c in df.columns for c in (col1, col2, col3, col4)):
            df[col2] = -df[col2]
            df[col3] = -df[col3]
            df[col4] = -df[col4]
    return df


# pos_wrt_eci1..3/vel_wrt_eci1..3은 DB 원본이 km(/s) 단위(확인됨) - core 전체가 미터
# 기준(WGS84_A 등)이므로 로딩 경계에서 한 번 변환해둔다.
_KM_TO_M_COLUMNS: tuple[str, ...] = (
    "pos_wrt_eci1", "pos_wrt_eci2", "pos_wrt_eci3",
    "vel_wrt_eci1", "vel_wrt_eci2", "vel_wrt_eci3",
)


def _convert_km_to_m(df: pd.DataFrame) -> pd.DataFrame:
    for col in _KM_TO_M_COLUMNS:
        if col in df.columns:
            df[col] = df[col] * 1000.0
    return df


class HKLoader:
    def __init__(self, connection_url: str, engine: Engine | None = None, satellite_id_col: str | None = None):
        """
        connection_url: SQLAlchemy 접속 문자열 (예: mysql+pymysql://user:pass@host:3306/db)
        engine:         이미 생성된 Engine을 재사용하고 싶을 때 전달 (테스트용 등)
        satellite_id_col: 한 테이블에 여러 위성 데이터가 섞여 있을 때만 쓰는 구분 컬럼명.
                            O1A/O1B는 테이블 자체가 분리돼 있어 None으로 둔다.
        """
        self.engine = engine or create_engine(connection_url, pool_pre_ping=True)
        self.satellite_id_col = satellite_id_col
        # 테이블 스키마는 런타임 중 불변이므로 캐시(HKLoader가 api/*.py에서 lru_cache로
        # 재사용되므로 이후 API 요청들에도 재사용됨).
        self._columns_cache: dict[str, set[str]] = {}
        self._columns_cache_lock = threading.Lock()

    @classmethod
    def from_env(cls, *, connection_url: str | None = None, schema: str | None = None) -> "HKLoader":
        """환경변수(.env) 기반으로 MySQL 연결 생성.

        config는 여기(및 for_satellite())에서만 지연 임포트 - connection_url을 직접 주는
        호출부는 config.py 없이도 동작해야 하므로.
        """
        from config import build_mysql_connection_url

        url = build_mysql_connection_url(connection_url=connection_url, schema=schema)
        return cls(connection_url=url, satellite_id_col=None)

    @classmethod
    def for_satellite(cls, satellite_id: str) -> "HKLoader":
        """
        config/satellites.toml에 등록된 위성별 DB 프로필로 커넥션 생성.
        """
        from config import satellite_registry

        db_config = satellite_registry.get(satellite_id)
        return cls(connection_url=db_config.connection_url, satellite_id_col=None)

    def _get_table_columns(self, table_name: str) -> set[str]:
        cached = self._columns_cache.get(table_name)
        if cached is not None:
            return cached

        with self._columns_cache_lock:
            # 락 대기 중 다른 스레드가 이미 채웠을 수 있으므로 재확인(double-checked locking).
            cached = self._columns_cache.get(table_name)
            if cached is not None:
                return cached

            with self.engine.connect() as conn:
                rows = conn.exec_driver_sql(f"SHOW COLUMNS FROM `{table_name}`").fetchall()
            columns = {str(row[0]) for row in rows}
            self._columns_cache[table_name] = columns
            return columns

    def _resolve_time_column(self, table_name: str, preferred: str, columns: set[str]) -> str:
        candidates = [
            preferred,
            "timeUtc",
            "time_utc",
            "time",
            "time_utc_str",
            "timeUtcStr",
            "timestamp",
            "epoch",
            "unix_time",
            "utc_time",
        ]
        for candidate in candidates:
            if candidate in columns:
                return candidate
        raise ValueError(
            f"Table '{table_name}' does not expose a usable HK time column. "
            f"Expected one of: {candidates}. Available columns: {sorted(columns)}"
        )

    def _fetch_packet(
        self,
        spec: PacketSpec,
        satellite_id: str | None,
        start_time: int,
        end_time: int,
        invert_quaternion_direction: bool = True,
    ) -> pd.DataFrame:
        """단일 hk 테이블에서 지정 구간의 데이터를 조회해 canonical 컬럼명으로 반환."""
        # load()의 _normalize_query_time()이 이미 int로 정규화해서 넘기므로 재캐스팅 불필요.
        start_epoch = start_time
        end_epoch = end_time

        available_columns = self._get_table_columns(spec.table)
        try:
            time_col = self._resolve_time_column(spec.table, spec.time_col, available_columns)
        except ValueError:
            logger.exception("Unable to resolve valid time column for table '%s'", spec.table)
            raise

        mapped_fields = {canonical: db_col for canonical, db_col in spec.fields.items() if db_col in available_columns}
        if not mapped_fields:
            logger.warning("No HK fields were found in table '%s'; available columns: %s", spec.table, sorted(available_columns))
            return pd.DataFrame(columns=["time"])

        select_cols = [time_col] + list(mapped_fields.values())
        col_list_sql = ", ".join(f"`{c}`" for c in select_cols)

        where_clauses = [f"`{time_col}` BETWEEN :start_time AND :end_time"]
        params: dict = {"start_time": start_epoch, "end_time": end_epoch}

        if satellite_id is not None and self.satellite_id_col:
            where_clauses.append(f"`{self.satellite_id_col}` = :satellite_id")
            params["satellite_id"] = satellite_id

        query = text(
            f"SELECT {col_list_sql} FROM `{spec.table}` "
            f"WHERE {' AND '.join(where_clauses)} "
            f"ORDER BY `{time_col}` ASC"
        )

        try:
            with self.engine.connect() as conn:
                df = pd.read_sql(query, conn, params=params)
        except Exception:
            logger.exception("Failed to fetch packet from table '%s'", spec.table)
            raise

        if df.empty:
            logger.warning("No rows returned for table '%s' in range [%s, %s]", spec.table, start_epoch, end_epoch)
            return pd.DataFrame(columns=["time", *mapped_fields.keys()])

        if time_col in df.columns:
            df[time_col] = pd.to_numeric(df[time_col], errors="coerce")
            df = df.dropna(subset=[time_col]).copy()
            df[time_col] = pd.to_datetime(df[time_col], unit="s", utc=True)

        rename_map = {db_col: canonical for canonical, db_col in mapped_fields.items()}
        rename_map[time_col] = "time"
        df = df.rename(columns=rename_map)
        df = df[["time", *mapped_fields.keys()]]
        df = _reorder_scalar_last_quaternions(df)
        if invert_quaternion_direction:
            df = _invert_quaternion_rotation_direction(df)
        df = _convert_km_to_m(df)
        return df

    def load(
        self,
        start_time: str | datetime | int | float,
        end_time: str | datetime | int | float,
        satellite_id: str | None = None,
        packets: list[str] | None = None,
        merge_tolerance_sec: float = DEFAULT_MERGE_TOLERANCE_SEC,
        interpolate_gaps: bool = True,
        invert_quaternion_direction: bool = True,
    ) -> pd.DataFrame:
        """start_time/end_time: KST 날짜/시각 문자열, UTC ISO8601, 또는 epoch seconds.
        satellite_id: 위성 구분자(O1A/O1B 등) - hk1~hk6 테이블 프리픽스 선택에 사용.
        invert_quaternion_direction: 기본 True(이 프로젝트의 Body->ECI 방향). Orekit/Rugged
            기반 소비자는 False가 필요 - README "Quaternion semantics" 참고.
        """
        start_epoch = _normalize_query_time(start_time, is_end=False)
        end_epoch = _normalize_query_time(end_time, is_end=True)
        schema = get_hk_packet_schema(satellite_id)
        packet_names = packets or list(schema.keys())

        def _fetch(name: str) -> pd.DataFrame:
            spec = schema[name]
            return self._fetch_packet(spec, satellite_id, start_epoch, end_epoch, invert_quaternion_direction)

        # 패킷별 조회는 서로 독립적인 DB 왕복(SHOW COLUMNS + SELECT)이므로 병렬 실행.
        # executor.map은 입력 순서대로 결과를 반환하므로(완료 순서가 아님) 아래 dict의
        # 삽입 순서는 순차 실행과 동일하게 packet_names 순서를 유지 -> merge_packets
        # 결과(중복 컬럼 접미사 처리 등)는 이전과 동일하다.
        if len(packet_names) > 1:
            with ThreadPoolExecutor(max_workers=min(len(packet_names), 8)) as executor:
                fetched = list(executor.map(_fetch, packet_names))
        else:
            fetched = [_fetch(name) for name in packet_names]

        packet_frames: dict[str, pd.DataFrame] = dict(zip(packet_names, fetched))

        if MASTER_PACKET not in packet_frames or packet_frames[MASTER_PACKET].empty:
            raise ValueError(
                f"Master packet '{MASTER_PACKET}' has no data in the requested range. "
                "Cannot establish a common timebase."
            )

        tolerance_overrides = _build_tolerance_overrides(schema, packet_names, merge_tolerance_sec)

        merged = merge_packets(
            packet_frames,
            master_key=MASTER_PACKET,
            tolerance_sec=merge_tolerance_sec,
            interpolate_gaps=interpolate_gaps,
            tolerance_overrides=tolerance_overrides,
        )

        merged = slice_time_range(merged, start_epoch, end_epoch)
        return merged


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Load and print HK packet data for a satellite window.")
    parser.add_argument("--connection-url", default=None, help="SQLAlchemy connection string, e.g. mysql+pymysql://user:pass@host:3306/db")
    parser.add_argument("--satellite-id", default=None, help="Satellite ID registered in config/satellites.toml")
    parser.add_argument("--start-time", required=True, help="Start time in ISO8601 format, e.g. 2026-08-01T00:00:00Z")
    parser.add_argument("--end-time", required=True, help="End time in ISO8601 format")
    parser.add_argument("--packets", nargs="*", default=None, help="Subset of packet names to load, e.g. hk1 hk2 hk3")
    parser.add_argument("--merge-tolerance-sec", type=float, default=DEFAULT_MERGE_TOLERANCE_SEC)
    parser.add_argument("--no-interpolate", action="store_true", help="Disable time-based interpolation gap filling")
    parser.add_argument("--attitude-only", action="store_true", help="Export only the essential attitude columns: timestamp, px, py, pz, vx, vy, vz, q0, q1, q2, q3")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print diagnostic info about detected HK columns when using --attitude-only")
    parser.add_argument("--max-rows", type=int, default=20, help="Number of rows to print in preview; 0 or negative prints all rows")
    parser.add_argument(
        "--output",
        default=None,
        help="Path to the saved output file. Use .txt for a text report or .csv for raw HK table data.",
    )
    parser.add_argument(
        "--output-format",
        choices=["txt", "csv", "czml"],
        default="csv",
        help="Output format for the saved file. Default is csv; use txt for a text report or czml for CZML export.",
    )
    return parser


def _default_output_path(
    start_time: str | datetime | int | float,
    end_time: str | datetime | int | float,
    output_format: str = "txt",
    prefix: str = "hk",
) -> str:
    try:
        start_dt = pd.Timestamp(_normalize_query_time(start_time, is_end=False), unit="s", tz="UTC")
        end_dt = pd.Timestamp(_normalize_query_time(end_time, is_end=True), unit="s", tz="UTC")
    except Exception:
        start_dt = pd.Timestamp.now("UTC")
        end_dt = pd.Timestamp.now("UTC")

    start_label = start_dt.strftime("%Y%m%dT%H%M%S")
    end_label = end_dt.strftime("%Y%m%dT%H%M%S")
    ext = {"csv": ".csv", "czml": ".czml"}.get(output_format, ".txt")
    output_dir = Path("hk_output")
    output_dir.mkdir(parents=True, exist_ok=True)
    return str(output_dir / f"{prefix}_{start_label}_{end_label}{ext}")


def _write_text_output(path: str, *, df: pd.DataFrame, max_rows: int) -> None:
    preview = df if max_rows <= 0 else df.head(max_rows)
    lines = [
        f"Loaded HK DataFrame: {len(df)} rows x {len(df.columns)} columns",
        f"Columns: {list(df.columns)}",
        preview.to_string(index=False),
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _write_csv_output(path: str, *, df: pd.DataFrame) -> None:
    df.to_csv(path, index=False)


def extract_attitude_columns(df: pd.DataFrame, verbose: bool = False) -> pd.DataFrame:
    """Extract the essential spacecraft attitude state columns.

    The standard export columns are:
      timestamp, px, py, pz, vx, vy, vz, q0, q1, q2, q3
    """
    if df.empty:
        return pd.DataFrame(columns=["timestamp", "px", "py", "pz", "vx", "vy", "vz", "q0", "q1", "q2", "q3"])

    # normalize column lookup to be case-insensitive
    cols_lc = {c.lower(): c for c in df.columns}

    time_candidates = ["time", "timestamp", "timeutc", "time_utc", "epoch"]
    time_col = next((cols_lc[c] for c in time_candidates if c in cols_lc), None)
    if time_col is None:
        raise ValueError("No valid timestamp column found to build attitude-only export.")

    out = pd.DataFrame(index=df.index)
    out["timestamp"] = pd.to_datetime(df[time_col], utc=True)

    # helper: find explicit triplet by explicit candidate lists (case-insensitive)
    def _find_by_candidates_lc(triplet_names_list):
        # helper to match candidate names against available columns with relaxed rules
        def _match_name(nl):
            # exact
            if nl in cols_lc:
                return cols_lc[nl]
            # startswith or contains
            for col_lc, orig in cols_lc.items():
                if col_lc.startswith(nl) or col_lc.startswith(nl + "_") or nl in col_lc:
                    return orig
            return None

        for names in triplet_names_list:
            if isinstance(names, (list, tuple)):
                found = []
                for n in names:
                    nl = n.lower()
                    match = _match_name(nl)
                    if match:
                        found.append(match)
                    else:
                        break
                if len(found) == len(names):
                    return tuple(found)
        return None

    # explicit common name patterns
    pos_triplet = _find_by_candidates_lc([
        ("px", "py", "pz"),
        ("pos_eci_x", "pos_eci_y", "pos_eci_z"),
        ("pos_ecef_x", "pos_ecef_y", "pos_ecef_z"),
        ("pos_itrf_x", "pos_itrf_y", "pos_itrf_z"),
        ("pos_x", "pos_y", "pos_z"),
        ("position_x", "position_y", "position_z"),
        # O1B HK2 naming convention: posWrtEci1..3
        ("poswrteci1", "poswrteci2", "poswrteci3"),
        ("poswrteci_1", "poswrteci_2", "poswrteci_3"),
        ("pos_wrt_eci1", "pos_wrt_eci2", "pos_wrt_eci3"),
        ("pos_wrt_eci_1", "pos_wrt_eci_2", "pos_wrt_eci_3"),
    ])

    # auto-discover triplet by shared prefix (columns that end with _x/_y/_z), case-insensitive
    if pos_triplet is None:
        for col_lc, orig in cols_lc.items():
            if col_lc.endswith("_x"):
                base = col_lc[:-2]
                y = base + "_y"
                z = base + "_z"
                if y in cols_lc and z in cols_lc:
                    pos_triplet = (cols_lc[base + "_x"], cols_lc[y], cols_lc[z])
                    break

    # handle single array column like 'pos_eci' -> expand
    if pos_triplet is None:
        for cand in ["pos_eci", "pos_ecef", "pos_itrf", "pos"]:
            if cand in cols_lc:
                colname = cols_lc[cand]
                sample = df[colname].iloc[0]
                try:
                    # assume iterable of length 3
                    if hasattr(sample, "__iter__") and len(sample) == 3:
                        out[["px", "py", "pz"]] = pd.DataFrame(df[colname].tolist(), index=df.index)
                        pos_triplet = ("px", "py", "pz")
                        break
                except Exception:
                    pass

    if pos_triplet is not None:
        out["px"] = df[pos_triplet[0]]
        out["py"] = df[pos_triplet[1]]
        out["pz"] = df[pos_triplet[2]]
    if verbose:
        print(f"[attitude-extract] pos_triplet detected: {pos_triplet}")

    # velocities: similar logic
    vel_triplet = _find_by_candidates_lc([
        ("vx", "vy", "vz"),
        ("vel_eci_x", "vel_eci_y", "vel_eci_z"),
        ("vel_ecef_x", "vel_ecef_y", "vel_ecef_z"),
        ("vel_x", "vel_y", "vel_z"),
        ("velocity_x", "velocity_y", "velocity_z"),
        # O1B HK2 naming convention: velWrtEci1..3
        ("velwrteci1", "velwrteci2", "velwrteci3"),
        ("velwrteci_1", "velwrteci_2", "velwrteci_3"),
        ("vel_wrt_eci1", "vel_wrt_eci2", "vel_wrt_eci3"),
        ("vel_wrt_eci_1", "vel_wrt_eci_2", "vel_wrt_eci_3"),
    ])
    if vel_triplet is None:
        for col_lc, orig in cols_lc.items():
            if col_lc.endswith("_x"):
                base = col_lc[:-2]
                y = base + "_y"
                z = base + "_z"
                if y in cols_lc and z in cols_lc:
                    # if these columns are same as pos_triplet, skip (already used)
                    cand_trip = (cols_lc[base + "_x"], cols_lc[y], cols_lc[z])
                    if not (pos_triplet and cand_trip == pos_triplet):
                        vel_triplet = cand_trip
                        break
    if vel_triplet is None:
        for cand in ["vel_eci", "vel_ecef", "vel", "velocity"]:
            if cand in cols_lc:
                colname = cols_lc[cand]
                sample = df[colname].iloc[0]
                try:
                    if hasattr(sample, "__iter__") and len(sample) == 3:
                        out[["vx", "vy", "vz"]] = pd.DataFrame(df[colname].tolist(), index=df.index)
                        vel_triplet = ("vx", "vy", "vz")
                        break
                except Exception:
                    pass

    if vel_triplet is not None:
        out["vx"] = df[vel_triplet[0]]
        out["vy"] = df[vel_triplet[1]]
        out["vz"] = df[vel_triplet[2]]
    if verbose:
        print(f"[attitude-extract] vel_triplet detected: {vel_triplet}")

    # quaternions (various naming conventions)
    quat_candidates = [
        ["q0", "q1", "q2", "q3"],
        ["q_eci2body_1", "q_eci2body_2", "q_eci2body_3", "q_eci2body_4"],
        ["qbody_wrt_eci1", "qbody_wrt_eci2", "qbody_wrt_eci3", "qbody_wrt_eci4"],
        ["q_body_wrt_eci_1", "q_body_wrt_eci_2", "q_body_wrt_eci_3", "q_body_wrt_eci_4"],
    ]
    quat_map = None
    for candidate in quat_candidates:
        if all(name in df.columns for name in candidate):
            quat_map = candidate
            break
    if quat_map is None:
        raise ValueError(
            "Quaternion columns were not found. Expected one of: "
            "q0,q1,q2,q3 or q_eci2body_1..4 or qbody_wrt_eci1..4"
        )

    q0, q1, q2, q3 = quat_map
    out["q0"] = df[q0]
    out["q1"] = df[q1]
    out["q2"] = df[q2]
    out["q3"] = df[q3]
    if verbose:
        print(f"[attitude-extract] quat_map used: {quat_map}")

    # Ensure a stable column order and include NaN for any missing attitude fields
    final_cols = ["timestamp", "px", "py", "pz", "vx", "vy", "vz", "q0", "q1", "q2", "q3"]
    for c in final_cols:
        if c not in out.columns:
            out[c] = pd.NA

    # If only timestamp and quaternions are missing (shouldn't happen for quaternions as they are required),
    # still return the standardized frame with NaNs so downstream tools have consistent columns.
    return out[final_cols].copy()


def _sanitize_value(v: Any) -> Any:
    """Convert pandas/numpy values to JSON-serializable Python types for CZML."""
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass

    # pandas Timestamp
    if isinstance(v, pd.Timestamp):
        try:
            return v.tz_convert("UTC").isoformat()
        except Exception:
            return pd.to_datetime(v, utc=True).isoformat()

    # numpy scalar
    if hasattr(v, "item") and not isinstance(v, (str, bytes, bytearray)):
        try:
            val = v.item()
            if isinstance(val, (numbers.Number, str, bool)):
                return val
            # fallthrough to str
        except Exception:
            pass

    if isinstance(v, (numbers.Number, str, bool)):
        return v

    # bytes / bytearray
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", errors="ignore")

    # fallback
    return str(v)


def df_to_czml(df: pd.DataFrame, *, id_prefix: str = "hk", time_col: str = "time") -> list:
    """Convert a merged HK DataFrame to a CZML list suitable for saving as a .czml file.
    """
    columns = list(df.columns)
    time_pos = columns.index(time_col)
    other_cols = [(pos, col) for pos, col in enumerate(columns) if col != time_col]

    czml = [{"id": "document", "version": "1.0"}]
    # iterrows()는 행마다 혼합 dtype Series를 새로 생성해 느리므로, 원본 dtype을
    # 그대로 보존하는 itertuples(name=None)로 순회(core.coordinates.build_cesium_track_czml와 동일 방식).
    for i, row in enumerate(df.itertuples(index=False, name=None)):
        pkt: dict[str, Any] = {"id": f"{id_prefix}_{i}"}
        t = row[time_pos]
        if isinstance(t, pd.Timestamp):
            time_iso = t.tz_convert("UTC").isoformat()
        else:
            time_iso = pd.to_datetime(t, utc=True).isoformat()
        pkt["time"] = time_iso
        for pos, col in other_cols:
            pkt[col] = _sanitize_value(row[pos])
        czml.append(pkt)
    return czml


def _write_czml_output(path: str, *, df: pd.DataFrame, id_prefix: str = "hk") -> None:
    czml = df_to_czml(df, id_prefix=id_prefix)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(czml, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = _build_cli_parser()
    args = parser.parse_args()

    if args.connection_url and args.satellite_id:
        parser.error("Specify either --connection-url or --satellite-id, not both.")

    if args.output and args.output.lower().endswith(".csv"):
        args.output_format = "csv"

    if args.output is None:
        prefix = "hk_attitude" if args.attitude_only else "hk"
        args.output = _default_output_path(args.start_time, args.end_time, output_format=args.output_format, prefix=prefix)

    try:
        if args.connection_url:
            loader = HKLoader(args.connection_url)
        elif args.satellite_id:
            loader = HKLoader.for_satellite(args.satellite_id)
        else:
            loader = HKLoader.from_env()
    except ValueError as exc:
        parser.exit(
            2,
            "\nDB connection configuration is missing or incomplete.\n"
            "1) Copy '.env.example' to '.env' and fill in the real MYSQL_* values\n"
            "2) Or provide --connection-url / --satellite-id\n"
            f"Details: {exc}\n",
        )

    df = loader.load(
        start_time=args.start_time,
        end_time=args.end_time,
        satellite_id=args.satellite_id,
        packets=args.packets,
        merge_tolerance_sec=args.merge_tolerance_sec,
        interpolate_gaps=not args.no_interpolate,
    )

    if args.attitude_only:
        df = extract_attitude_columns(df, verbose=args.verbose)

    preview = df if args.max_rows <= 0 else df.head(args.max_rows)

    if args.output_format == "csv":
        _write_csv_output(args.output, df=df)
    elif args.output_format == "czml":
        _write_czml_output(args.output, df=df)
    else:
        _write_text_output(args.output, df=df, max_rows=args.max_rows)

    print(f"Saved HK data to: {args.output}")
    print(f"Loaded HK DataFrame: {len(df)} rows x {len(df.columns)} columns")
    print(f"Columns: {list(df.columns)}")
    print(preview.to_string(index=False))


if __name__ == "__main__":
    main()
