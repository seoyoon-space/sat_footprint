"""
config.py(위성 DB 접속 설정 로딩) 검증.

Phase 0에서 고쳤던 "config/satellites.toml이 없으면 HKLoader.for_satellite()가
FileNotFoundError로 죽는다" 문제의 근본 로직(_load_satellite_configs,
SatelliteRegistry, build_mysql_connection_url)에 대한 회귀 테스트.
실제 DB 접속은 하지 않고, 파일 파싱/조회/에러 처리 로직만 검증한다.
"""

from __future__ import annotations

import pytest

from config import (
    MceDbConfig,
    SatelliteDBConfig,
    SatelliteRegistry,
    _load_satellite_configs,
    build_mysql_connection_url,
    get_mce_db_config,
)

VALID_TOML = """
[O1A]
db_host = "127.0.0.1"
db_port = 3306
db_user = "nstanl"
db_password = "secret"
db_name = "nstanl"

[O1B]
db_host = "10.0.0.2"
db_port = 3307
db_user = "nstanl2"
db_password = "secret2"
db_name = "nstanl2"
"""


def test_load_satellite_configs_raises_file_not_found_with_helpful_message(tmp_path):
    missing_path = tmp_path / "does_not_exist.toml"

    with pytest.raises(FileNotFoundError) as excinfo:
        _load_satellite_configs(missing_path)

    # 사용자가 바로 조치할 수 있도록 example 파일 복사 안내가 메시지에 포함되어야 함
    assert "satellites.example.toml" in str(excinfo.value)


def test_load_satellite_configs_parses_valid_toml(tmp_path):
    config_path = tmp_path / "satellites.toml"
    config_path.write_text(VALID_TOML, encoding="utf-8")

    configs = _load_satellite_configs(config_path)

    assert set(configs.keys()) == {"O1A", "O1B"}
    assert configs["O1A"] == SatelliteDBConfig(
        db_host="127.0.0.1", db_port=3306, db_user="nstanl", db_password="secret", db_name="nstanl"
    )


def test_satellite_db_config_connection_url_format():
    cfg = SatelliteDBConfig(db_host="127.0.0.1", db_port=3306, db_user="u", db_password="p", db_name="d")

    assert cfg.connection_url == "mysql+pymysql://u:p@127.0.0.1:3306/d"


def test_satellite_registry_get_returns_config_for_known_id(tmp_path):
    config_path = tmp_path / "satellites.toml"
    config_path.write_text(VALID_TOML, encoding="utf-8")
    registry = SatelliteRegistry(path=config_path)

    cfg = registry.get("O1B")

    assert cfg.db_host == "10.0.0.2"
    assert registry.available_satellites() == ["O1A", "O1B"]


def test_satellite_registry_get_raises_key_error_for_unknown_id(tmp_path):
    config_path = tmp_path / "satellites.toml"
    config_path.write_text(VALID_TOML, encoding="utf-8")
    registry = SatelliteRegistry(path=config_path)

    with pytest.raises(KeyError) as excinfo:
        registry.get("UNKNOWN")

    assert "O1A" in str(excinfo.value) and "O1B" in str(excinfo.value)


def test_satellite_registry_lazy_loads_only_once(tmp_path, monkeypatch):
    config_path = tmp_path / "satellites.toml"
    config_path.write_text(VALID_TOML, encoding="utf-8")
    registry = SatelliteRegistry(path=config_path)

    call_count = {"n": 0}
    import config as config_module

    original = config_module._load_satellite_configs

    def counting_load(path):
        call_count["n"] += 1
        return original(path)

    monkeypatch.setattr(config_module, "_load_satellite_configs", counting_load)

    registry.get("O1A")
    registry.get("O1B")
    registry.available_satellites()

    assert call_count["n"] == 1


def test_build_mysql_connection_url_uses_explicit_connection_url():
    url = build_mysql_connection_url(connection_url="mysql+pymysql://explicit/db")

    assert url == "mysql+pymysql://explicit/db"


def test_build_mysql_connection_url_uses_explicit_params():
    url = build_mysql_connection_url(host="h", port=1234, user="u", password="p", db="d")

    assert url == "mysql+pymysql://u:p@h:1234/d"


def test_build_mysql_connection_url_raises_when_incomplete(monkeypatch):
    import config as config_module

    for field in ("mysql_host", "mysql_user", "mysql_db", "mysql_schema", "mysql_connection_url"):
        monkeypatch.setattr(config_module.settings, field, None)
    for env_var in ("MYSQL_HOST", "MYSQL_USER", "MYSQL_DB", "MYSQL_DATABASE", "MYSQL_SCHEMA"):
        monkeypatch.delenv(env_var, raising=False)

    with pytest.raises(ValueError):
        build_mysql_connection_url()


def test_get_mce_db_config_raises_when_incomplete(monkeypatch):
    import config as config_module

    for field in ("mce_db_host", "mce_db_user", "mce_db_password", "mce_db_name"):
        monkeypatch.setattr(config_module.settings, field, None)
    for env_var in ("MCE_DB_HOST", "MCE_DB_USER", "MCE_DB_PASSWORD", "MCE_DB_NAME"):
        monkeypatch.delenv(env_var, raising=False)

    with pytest.raises(ValueError, match="MCE DB env is incomplete"):
        get_mce_db_config()


def test_get_mce_db_config_builds_config_from_settings(monkeypatch):
    import config as config_module

    monkeypatch.setattr(config_module.settings, "mce_db_host", "10.0.0.5")
    monkeypatch.setattr(config_module.settings, "mce_db_port", 3306)
    monkeypatch.setattr(config_module.settings, "mce_db_user", "mce_user")
    monkeypatch.setattr(config_module.settings, "mce_db_password", "mce_pass")
    monkeypatch.setattr(config_module.settings, "mce_db_name", "o1b_mce_server")

    cfg = get_mce_db_config()

    assert cfg == MceDbConfig(
        db_host="10.0.0.5", db_port=3306, db_user="mce_user", db_password="mce_pass", db_name="o1b_mce_server"
    )
