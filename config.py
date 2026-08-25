# 전역 설정 및 물리 상수 (위성 관성모멘트 J, 휠 한계치 등)

"""
환경설정.

satellites.toml 위치는 SATELLITE_CONFIG_PATH 환경변수로 override 가능
(기본값: config/satellites.toml).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value not in (None, ""):
            return value
    return None


@dataclass(frozen=True)
class SatelliteDBConfig:
    db_host: str
    db_port: int
    db_user: str
    db_password: str
    db_name: str

    @property
    def connection_url(self) -> str:
        return (
            f"mysql+pymysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    api_title: str = "Attitude Reconstruction & Validation API"
    api_version: str = "0.1.0"

    satellite_config_path: str = "config/satellites.toml"

    mysql_host: str | None = None
    mysql_port: int = 3306
    mysql_user: str | None = None
    mysql_password: str | None = None
    mysql_db: str | None = None
    mysql_schema: str | None = None
    mysql_connection_url: str | None = None


settings = Settings()


def build_mysql_connection_url(
    *,
    host: str | None = None,
    port: int | None = None,
    user: str | None = None,
    password: str | None = None,
    db: str | None = None,
    schema: str | None = None,
    connection_url: str | None = None,
) -> str:
    """환경변수/explicit 값으로 MySQL 연결 URL 생성."""
    if connection_url:
        return connection_url

    host = host or settings.mysql_host or _env_first("MYSQL_HOST")
    port = port if port is not None else settings.mysql_port or int(_env_first("MYSQL_PORT") or 3306)
    user = user or settings.mysql_user or _env_first("MYSQL_USER")
    password = password if password is not None else settings.mysql_password or _env_first("MYSQL_PASSWORD")
    db = db or settings.mysql_db or _env_first("MYSQL_DB", "MYSQL_DATABASE")
    schema = schema or settings.mysql_schema or _env_first("MYSQL_SCHEMA")

    if not host or not user or not db:
        raise ValueError(
            "MySQL env is incomplete. Set MYSQL_HOST, MYSQL_USER, MYSQL_DB (or MYSQL_CONNECTION_URL)."
        )

    # MySQL에서 schema와 database는 보통 같은 값입니다.
    # satellite_id(O1A)와 같은 값은 DB 이름으로 사용하면 안 됩니다.
    # 실제 운영 DB는 nstanl 같은 database 이름을 사용해야 하며,
    # 별도 schema를 가진 구조라면 그 값이 db와 동일해야 합니다.
    if db and schema and schema not in {db, ""}:
        db = db
    elif not db and schema:
        db = schema

    return f"mysql+pymysql://{user}:{password or ''}@{host}:{port}/{db}"


def _load_satellite_configs(path: str | Path) -> dict[str, SatelliteDBConfig]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"Satellite DB config not found at '{path}'. "
            "Copy config/satellites.example.toml to config/satellites.toml "
            "and fill in real values (this file must never be committed)."
        )

    with open(path, "rb") as f:
        raw = tomllib.load(f)

    return {
        sat_id: SatelliteDBConfig(
            db_host=section["db_host"],
            db_port=int(section["db_port"]),
            db_user=section["db_user"],
            db_password=section["db_password"],
            db_name=section["db_name"],
        )
        for sat_id, section in raw.items()
    }


class SatelliteRegistry:
    """satellites.toml을 지연 로딩(lazy load)해서 satellite_id -> DB 설정을 제공."""

    def __init__(self, path: str | Path | None = None):
        self._path = path or os.environ.get("SATELLITE_CONFIG_PATH", settings.satellite_config_path)
        self._configs: dict[str, SatelliteDBConfig] | None = None

    def _ensure_loaded(self) -> dict[str, SatelliteDBConfig]:
        if self._configs is None:
            self._configs = _load_satellite_configs(self._path)
        return self._configs

    def get(self, satellite_id: str) -> SatelliteDBConfig:
        configs = self._ensure_loaded()
        if satellite_id not in configs:
            raise KeyError(
                f"No DB config found for satellite_id='{satellite_id}'. "
                f"Available: {list(configs.keys())}"
            )
        return configs[satellite_id]

    def available_satellites(self) -> list[str]:
        return list(self._ensure_loaded().keys())


satellite_registry = SatelliteRegistry()