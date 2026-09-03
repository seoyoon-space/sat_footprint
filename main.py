# API 진입 (어플리케이션 선언 및 라우터 등록)# 요청(Request) 및 응답(Response) 데이터 직렬화/검증 (Pydantic)

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from api.czml_routes import router as czml_router
from api.footprint_routes import router as footprint_router
from api.routes import router as telemetry_router
from api.validator_routes import router as validator_router
from config import settings

app = FastAPI(title=settings.api_title, version=settings.api_version)

# CORS_ALLOWED_ORIGINS 미설정 시 허용 origin이 빈 리스트가 되어, 이 미들웨어를 추가해도
# 브라우저 cross-origin 요청은 여전히 차단됨(서버-서버 호출은 영향 없음) - 안전한 기본값.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(telemetry_router)
app.include_router(czml_router)  # same prefix '/telemetry'
app.include_router(footprint_router)
app.include_router(validator_router)


@app.get("/health")
def health():
    return {"status": "ok"}