# sat_footprint

위성 HK(Housekeeping) 데이터로부터 촬영 영역(footprint)을 계산하는 모듈.

```
HK 자세 데이터 → Orekit/Rugged → 지상 교점 → Footprint 좌표
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Python (pipeline.py)                                           │
│                                                                 │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐ │
│  │ HK 입력   │───▶│ io_adapter│───▶│ CSV 변환  │───▶│ Java 호출 │ │
│  │ (CSV/API) │    │          │    │          │    │           │ │
│  └──────────┘    └──────────┘    └──────────┘    └─────┬─────┘ │
│                                                        │       │
│  ┌───────────────────────────────────────────────┐     │       │
│  │ 결과 파싱 + GeoJSON 출력                        │◀────┘       │
│  └───────────────────────────────────────────────┘             │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│  Java (Orekit + Rugged)                                         │
│                                                                 │
│  AttitudeRecord ──▶ HkToOrekitConverter ──▶ RuggedBuilder       │
│       (CSV)           (ECI 좌표 변환)         (ITRF + WGS84)    │
│                                                   │             │
│                           SensorSpec ──▶ LineSensor + LOS       │
│                           (FOV, pixel)       │                  │
│                                              ▼                  │
│                     GLO30TileUpdater ──▶ Rugged.directLocation  │
│                       (DEM 타일)          (ray-terrain 교점)    │
│                                              │                  │
│                                              ▼                  │
│                                       FootprintResult           │
│                                    (lat, lon, alt per line)     │
└─────────────────────────────────────────────────────────────────┘
```

## 데이터 흐름 상세

```
Boresight        Body → ECI        ECI → ECEF       Ray 교점         Footprint
(body-fixed) ──▶ (qbodyWrtEci) ──▶ (Orekit ITRF) ──▶ (WGS-84+DEM) ──▶ (lat/lon)
  센서 FOV         HK 쿼터니언        Orekit 내부      Rugged 계산       결과 출력
```

## Prerequisites

| 항목 | 버전 | 비고 |
|------|------|------|
| Java (JDK) | 17+ | Adoptium Temurin 권장 |
| Maven | 3.9+ | |
| Python | 3.10+ | |
| Orekit | 13.1.2 | pom.xml에 고정 |
| Rugged | 4.0.1 | Orekit 13.1.2와 반드시 쌍으로 사용 |

### 외부 데이터 (별도 준비 필요)

1. **Orekit 데이터**: `orekit-data-master/` — Orekit이 ITRF 변환, EOP 보정 등에 사용
   ```
   git clone https://gitlab.orekit.org/orekit/orekit-data.git orekit-data-master
   ```

2. **DEM 타일**: GLO-30 기반 바이너리 타일 + `tile_index.json`
   - Python 스크립트로 다운로드/타일링 후 생성 (별도 저장소 참조)

## Quick Start

### 1. Java 빌드

```bash
cd java
mvn compile
```

### 2. Java 단독 실행 (CSV 입력 → Footprint CSV 출력)

```bash
mvn exec:java -Dexec.mainClass="footprint.Main" \
  -Dexec.args="attitude.csv tiles/tile_index.json 2026-08-16T03:00:00 2026-08-16T03:01:00 100 output.csv"
```

인자 순서: `<attitude_csv> <tile_index> <start_utc> <end_utc> <line_step> <output_csv>`

### 3. Python 파이프라인 실행

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

## Project Structure

```
sat_footprint/
├── README.md
├── .gitignore
├── requirements.txt
│
├── python/
│   └── footprint/
│       ├── __init__.py
│       ├── models.py          # 데이터 모델 (SatelliteState, SensorConfig)
│       ├── io_adapter.py      # HK 데이터 입력 어댑터 (CSV/DataFrame/dict)
│       └── pipeline.py        # 메인 파이프라인 (Python → Java → 결과)
│
├── java/
│   ├── pom.xml
│   └── src/main/java/footprint/
│       ├── Main.java              # CLI 진입점
│       ├── FootprintCalculator.java  # Rugged 기반 footprint 계산
│       ├── HkToOrekitConverter.java  # HK → Orekit 타입 변환
│       ├── AttitudeRecord.java       # 자세 데이터 모델
│       ├── SensorSpec.java           # 센서 스펙 (FOV, pixel, lineRate)
│       ├── FootprintResult.java      # 계산 결과 모델
│       ├── GLO30TileUpdater.java     # DEM 타일 → Rugged 공급
│       └── TileRecord.java           # 타일 인덱스 레코드
│
└── examples/
    └── example_footprint.py   # 사용 예시
```

## 센서 파라미터 현황

| 파라미터 | 값 | 상태 |
|---------|-----|------|
| Focal length | 1067 mm | 확정 (MultiScape200 스펙) |
| Pixel size | 3.2 µm | 확정 |
| FOV (across-track) | 1.6° | 확정 |
| Mounting error | 0° | 미확보 (기본값 0, AOCS 보정값 확보 후 업데이트) |
| Line rate | 100 lines/s | 임시값 (GSD 확보 후 FMC속도/GSD로 계산) |

## 좌표계 규약

- **입력 (HK)**: ECI ≈ EME2000 (position km, velocity km/s)
- **Java 내부**: Orekit이 EME2000 → ITRF 정밀 변환 처리
- **출력**: WGS84 측지 좌표 (latitude°, longitude°, altitude m)

## Version Pinning

Rugged 4.0.1 + Orekit 13.1.2는 **반드시 쌍으로 사용**해야 합니다.
버전을 올릴 때는 [Rugged pom.xml](https://github.com/CS-SI/Rugged)에서 호환 Orekit 버전을 확인할 것.
