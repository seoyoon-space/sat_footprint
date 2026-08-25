# API 진입 (어플리케이션 선언 및 라우터 등록)# 요청(Request) 및 응답(Response) 데이터 직렬화/검증 (Pydantic)

from fastapi import FastAPI

from api.routes import router as telemetry_router
from config import settings

app = FastAPI(title=settings.api_title, version=settings.api_version)

app.include_router(telemetry_router)

# include CZML-specific routes (same prefix '/telemetry')
from api.czml_routes import router as czml_router
app.include_router(czml_router)


@app.get("/health")
def health():
    return {"status": "ok"}