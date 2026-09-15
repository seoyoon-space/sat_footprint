# API Key 기반 인증 의존성 (FastAPI Depends)

from __future__ import annotations

from fastapi import Header, HTTPException

from config import settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """settings.api_key(환경변수 API_KEY)가 설정된 경우에만 X-API-Key 헤더를 검사한다.

    미설정 시(로컬 개발 기본값) 인증을 건너뛴다 - 운영 배포에서는 반드시
    API_KEY를 설정해 이 검사가 실제로 동작하도록 해야 한다(README 참고).
    """
    if not settings.api_key:
        return
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key header")
