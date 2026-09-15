# sat_footprint

위성 HK(Housekeeping) 텔레메트리로부터 촬영 영역(footprint)을 실제 지형(DEM) 기준으로
계산하고, Cesium 3D 뷰어로 실시간 시각화하는 프로젝트.

```
HK 텔레메트리 → 자세/궤도 → DEM 지형 교차 → Footprint 폴리곤 → 3D/2D 시각화
```

작업 상세 이력은 [docs/work-summary.md](docs/work-summary.md)(1차), [docs/work-summary-2.md](docs/work-summary-2.md)(2차) 참고.

## Architecture — 3축 구조

```
sat_footprint/
├── attitude-viewer/     웹서버 + 오케스트레이션 (Flask, Cesium 3D/2D 뷰어) — 유일한 사용자 진입점
├── hk_api/               HK 텔레메트리 · 미션스케줄 · 좌표변환 서비스 (FastAPI, 독립 프로세스)
└── footprint-backend/   DEM 기반 실측 footprint 계산 (Java/Orekit/Rugged + Python 래퍼)
    ├── java/
    └── python/
```

- **attitude-viewer**가 나머지 둘을 호출하는 쪽. `hk_api`, `footprint-backend`는 서로를 직접 알지 못한다.
- `hk_api`는 별도 포트(8001, `127.0.0.1`만 바인딩)·별도 Python venv로 독립 실행되며, `attitude-viewer`가 `/api/hk/*` 리버스 프록시로만 접근한다.
- `footprint-backend`는 `attitude-viewer`의 `/api/footprint/compute`가 실시간으로 호출하는 Java 서브프로세스 기반 파이프라인이다.

각 축의 파일별 역할, API 엔드포인트, 이번 라운드에 구현된 개선사항은 [docs/work-summary-2.md](docs/work-summary-2.md)에 자세히 정리되어 있다.

## Quick Start

### 1. hk_api 실행 (포트 8001)

```bash
cd hk_api
python -m venv .venv && .venv/Scripts/activate  # Windows
pip install -r requirements.txt
cp config/satellites.example.toml config/satellites.toml  # 실제 DB 접속정보 채우기
cp .env.example .env                                       # MYSQL_*, MCE_DB_* 채우기
uvicorn main:app --port 8001
```

### 2. footprint-backend(Java) 빌드

```bash
cd footprint-backend/java
mvn compile
```

### 3. attitude-viewer 실행 (포트 8080)

```bash
cd attitude-viewer
pip install -r ../requirements.txt
cp .env.cesium.example .env.cesium   # CESIUM_ION_TOKEN 채우기
python app.py
```

`http://localhost:8080/sat_footprint/` 접속.

## footprint-backend 상세

### Prerequisites

| 항목 | 버전 | 비고 |
|------|------|------|
| Java (JDK) | 17+ | Adoptium Temurin 권장 |
| Maven | 3.9+ | |
| Python | 3.10+ | |
| Orekit | 13.1.2 | pom.xml에 고정 |
| Rugged | 4.0.1 | Orekit 13.1.2와 반드시 쌍으로 사용 |

### 외부 데이터 (별도 준비 필요)

1. **Orekit 데이터**: `data/orekit-data-master/` — Orekit이 ITRF 변환, EOP 보정 등에 사용
   ```
   git clone https://gitlab.orekit.org/orekit/orekit-data.git data/orekit-data-master
   ```

2. **DEM 타일**: ASTGTMV003 기반 바이너리 타일 + `tile_index.json`
   - `python scripts/setup_dem_aster.py` 로 네트워크 서버에서 변환

### Java 단독 실행 (CSV 입력 → Footprint CSV 출력)

```bash
cd footprint-backend/java
mvn exec:java -Dexec.mainClass="footprint.Main" \
  -Dexec.args="attitude.csv tiles/tile_index.json 2026-08-16T03:00:00 2026-08-16T03:01:00 100 output.csv"
```

인자 순서: `<attitude_csv> <tile_index> <start_utc> <end_utc> <line_step> <output_csv>`

### Python 파이프라인 실행

```python
from footprint.pipeline import compute_footprint

result = compute_footprint(
    attitude_csv="attitude.csv",
    tile_index="path/to/tile_index.json",
    orekit_data="path/to/orekit-data-master",
    start_utc="2026-08-16T03:00:00",
    end_utc="2026-08-16T03:01:00",
)
print(result)  # DataFrame with lat/lon/alt per line
```

### 센서 파라미터 현황

| 파라미터 | 값 | 상태 |
|---------|-----|------|
| Focal length | 1067 mm | 확정 (MultiScape200 스펙) |
| Pixel size | 3.2 µm | 확정 |
| FOV (across-track) | 1.6° | 확정 |
| Mounting error | 0° | 미확보 (기본값 0, AOCS 보정값 확보 후 업데이트) |
| Line rate | 100 lines/s | 임시값 (GSD 확보 후 FMC속도/GSD로 계산) |

### 좌표계 규약

- **입력 (HK)**: ECI ≈ EME2000 (position km, velocity km/s), 쿼터니언은 scalar-first(q0=w), body→ECI
- **Java 내부**: Orekit이 EME2000 → ITRF 정밀 변환 처리
- **출력**: WGS84 측지 좌표 (latitude°, longitude°, altitude m)

### Version Pinning

Rugged 4.0.1 + Orekit 13.1.2는 **반드시 쌍으로 사용**해야 합니다.
버전을 올릴 때는 [Rugged pom.xml](https://github.com/CS-SI/Rugged)에서 호환 Orekit 버전을 확인할 것.
