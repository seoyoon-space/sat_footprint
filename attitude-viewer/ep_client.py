"""EP (Event Planner) Server API client.

docs/ep-server-api-reference.txt 참고. 내부망 서버라 인증 없음.
"""
import os

import requests

EP_SERVER_URL = os.environ.get("EP_SERVER_URL", "http://192.168.0.82:8080")
TIMEOUT = 10


def get_aoi_list(page_size: int = 1000) -> list[dict]:
    """전체 AOI 목록을 반환 (필요시 페이지를 순회해 병합)."""
    items: list[dict] = []
    page = 1
    while True:
        r = requests.get(
            f"{EP_SERVER_URL}/api/AOI/list",
            params={"pageNumber": page, "pageSize": page_size},
            timeout=TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        items.extend(data.get("items", []))
        total_pages = data.get("totalPages", 1)
        if page >= total_pages:
            break
        page += 1
    return items

def get_tle(satellite_id: str) -> dict:
    """현재 TLE(2-line element) 조회. currentTle 필드는 "0 HEADER\\r\\n1 ...\\r\\n2 ..." 3줄 포맷."""
    r = requests.get(f"{EP_SERVER_URL}/api/TLE/status/{satellite_id}", timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# Note: mission data (Mission/selected) is no longer fetched from this HTTP API —
# see mce_db.py. The HTTP API was found to return stale/wrong latitude/longitude and
# status for several scheduleIds versus its own backing DB.
