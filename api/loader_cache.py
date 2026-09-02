# 위성별 HKLoader(및 내부 SQLAlchemy Engine/connection pool) 공유 캐시.
#
# routes.py/czml_routes.py/validator_routes.py가 각자 별도의 @lru_cache _get_loader를
# 두면 동일 satellite_id에 대해 Engine이 라우터별로 중복 생성돼(DB 커넥션 풀이 여러 개
# 열리고, HKLoader._columns_cache도 라우터마다 따로 채워짐) 자원이 낭비된다. 이 모듈
# 하나로 통합해 프로세스 전체에서 satellite_id당 HKLoader 인스턴스를 하나만 유지한다.

from __future__ import annotations

from functools import lru_cache

from core.loader import HKLoader


@lru_cache(maxsize=None)
def get_loader(satellite_id: str) -> HKLoader:
    return HKLoader.for_satellite(satellite_id)
