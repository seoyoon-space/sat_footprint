"""Mission schedule for the current server — sourced via hk_api's /mission/schedule,
not a direct DB connection from this process.

hk_api (hk_api/core/mission/mce_db.py) does the actual read-only access to the
EP/MCE server's own backing database (o1b_mce_server.TB_Selected_Mission_Schedule) and
the camera ON/OFF window computation — this module is now just a thin HTTP client so
app.py/mps_db.py's existing get_missions(satellite_id, start_iso, end_iso) call sites
don't need to change. See secrets.toml's [mce_db] comment / hk_api/.env for the actual
DB credentials (now only read by hk_api, not by this process).
"""
from __future__ import annotations

import os

import requests

HK_API_BASE_URL = os.environ.get("HK_API_BASE_URL", "http://localhost:8001")


def get_missions(satellite_id: str, start_iso: str, end_iso: str) -> list[dict]:
    """Mission rows from the current server (hk_api -> MCE DB, ground truth)."""
    resp = requests.post(
        f"{HK_API_BASE_URL}/mission/schedule",
        json={"satellite_id": satellite_id, "start_time": start_iso, "end_time": end_iso},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json().get("missions", [])
