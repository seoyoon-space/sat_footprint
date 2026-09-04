#!/usr/bin/env bash
#
# 사용법:
#   ssh <user>@192.168.0.82
#   git clone https://github.com/seoyoon-space/sat_footprint.git && cd sat_footprint
#   git checkout doeun-space
#   bash deploy/deploy.sh
#
# .env / config/satellites.toml에 실제 값이 안 채워져 있으면 실행을 멈추고 안내만 하고 끝난다
# (여기 스크립트가 비밀번호 등을 대신 채워 넣지 않는다 - 직접 채워야 함).
#
# 기본 포트가 8081인 이유: 192.168.0.82는 이미 EP(Event Planner) 서버가 8080을 쓰고 있는
# 팀 개발 서버라(docs 참고), sat_simulation_api는 그 뒤에 다른 포트를 잡아 띄운다.

set -euo pipefail
cd "$(dirname "$0")/.."

HOST_PORT="${HOST_PORT:-8081}"
IMAGE_NAME="sat-simulation-api"
CONTAINER_NAME="sat-api"

echo "== 1) 설정 파일 확인 =="
missing=0
if [ ! -f .env ]; then
  cp .env.example .env
  echo "  .env 생성함(.env.example 복사) - MYSQL_*, API_KEY, CORS_ALLOWED_ORIGINS를 채워야 함"
  missing=1
fi
if [ ! -f config/satellites.toml ]; then
  cp config/satellites.example.toml config/satellites.toml
  echo "  config/satellites.toml 생성함(example 복사) - 위성별 DB 접속정보를 채워야 함"
  missing=1
fi
if [ "$missing" -eq 1 ]; then
  echo ""
  echo "!! .env / config/satellites.toml에 실제 값을 채운 뒤 이 스크립트를 다시 실행하세요."
  echo "   (실제 DB 비밀번호 등은 절대 git에 커밋하지 말 것 - 둘 다 .gitignore에 등록되어 있음)"
  exit 1
fi

echo "== 2) 이미지 빌드 =="
docker build -t "$IMAGE_NAME" .

echo "== 3) 기존 컨테이너 정리 =="
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

echo "== 4) 컨테이너 기동 (host:$HOST_PORT -> container:8000) =="
docker run -d --restart unless-stopped \
  -p "${HOST_PORT}:8000" \
  --env-file .env \
  -v "$(pwd)/config/satellites.toml:/app/config/satellites.toml:ro" \
  --name "$CONTAINER_NAME" \
  "$IMAGE_NAME"

echo "== 5) 헬스체크 =="
sleep 2
curl -sf "http://localhost:${HOST_PORT}/health" && echo "" && echo "OK: http://$(hostname -I 2>/dev/null | awk '{print $1}'):${HOST_PORT}"
