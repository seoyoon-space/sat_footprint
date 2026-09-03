# sat_footprint 작업 정리

브랜치: `seoyoon-astgtm` / 작성일: 2026-09-03

## 1. 전체 목표

HK(Housekeeping) 텔레메트리로부터 위성 촬영 영역(footprint)을 **실제 지형(DEM) 기준으로 정밀하게 계산**하고,
그 결과를 EP(Event Planner) 서버의 AOI/미션 정보와 연동해 **Cesium 3D 뷰어로 실시간 시각화**하는 것.

```
HK 텔레메트리 ──▶ 자세/궤도 좌표 변환 ──▶ Orekit/Rugged DEM 교차 ──▶ Footprint 폴리곤 ──▶ 시각화/검증
   (DB)           (ECI, 쿼터니언)         (ITRF+WGS84+ASTGTM)        (lat/lon/alt)      (Cesium, 2D map)
```

## 2. 전체 데이터 흐름

```
┌────────────┐   ┌──────────────┐   ┌────────────────────┐   ┌───────────────┐
│  HK DB      │──▶│ hk_loader     │──▶│ python/footprint    │──▶│ Java           │
│ (O1A 텔레메트│   │ (자세 컬럼    │   │ io_adapter/pipeline │   │ Orekit+Rugged  │
│ 리)         │   │  추출·정규화) │   │ (CSV 변환, Java 호출)│   │ (ITRF 변환 +   │
└────────────┘   └──────────────┘   └────────────────────┘   │  DEM 교차)     │
                                                                └──────┬────────┘
                                                                       │ lat/lon/alt
                                                                       ▼
┌───────────────────┐   ┌────────────────────┐   ┌──────────────────────────┐
│ EP 서버             │──▶│ attitude-viewer      │──▶│ Cesium 3D / 2D map        │
│ (AOI/미션 목록,     │   │ (Flask: czml/footprint│   │ (실시간 궤적, footprint    │
│  o1b_mce_server DB)│   │  API, DEM on-demand) │   │  스캔라인, 촬영 이벤트)     │
└───────────────────┘   └────────────────────┘   └──────────────────────────┘
```

## 3. 컴포넌트별 작업 내용

### 3.1 DEM: GLO30 → ASTGTM 교체 (`f5a7aa4`)

- 기존 GLO30 기반 DEM 타일 공급 로직을 **ASTGTMV003** 기반으로 교체
- 관련 파일: [ASTGTMTileUpdater.java](../java/src/main/java/footprint/ASTGTMTileUpdater.java), [TileRecord.java](../java/src/main/java/footprint/TileRecord.java)

### 3.2 Python 파이프라인 보강

- **[io_adapter.py](../python/footprint/io_adapter.py)**: HK 패킷 병합 시 구간 경계에서 보간되지 않은 `NaT`/`NaN` 행이 섞여 나와 Java 쪽 CSV 파싱이 실패하던 문제 → `from_dataframe()`에서 필수 컬럼 기준으로 결측 행을 사전 제거하도록 수정
- **[pipeline.py](../python/footprint/pipeline.py)**: Java 프로세스 실패 시 `stderr`만 노출되던 것을 `stdout`도 함께 포함하도록 개선 (원인 파악 용이)
- **[dem_tiles.py](../python/footprint/dem_tiles.py)** (신규): 전세계 AOI 요청에 대응하기 위해 DEM을 전량 사전 다운로드하지 않고, 요청 좌표 주변만 `tile_index.json`에 없는 타일을 on-demand로 찾아 병합하는 모듈 (`ensure_dem_tiles`)

### 3.3 hk_loader 쿼터니언 컨벤션 자동 판별

- **[hk_loader.py](../python/hk_loader/core/loader/hk_loader.py) `extract_attitude_columns()`**: HK 원본 컬럼명이 스칼라 우선(`q0..q3`)인지, HK 고유의 스칼라 후위(`qbody_wrt_eci1..4`, 1~3=xyz·4=w)인지 자동 감지해서 항상 **출력은 스칼라 우선(q0=w)** 으로 통일하도록 재작성
  - 이전 코드는 컬럼명 후보만 나열하고 순서 변환 없이 그대로 매핑해서, 스칼라-후위 데이터가 들어오면 q0 자리에 실제로는 x축 값이 들어가는 잘못된 매핑이 될 위험이 있었음
  - `verbose=True` 시 어떤 컨벤션이 감지됐는지 로그로 출력하도록 추가

### 3.4 attitude-viewer (신규 Flask 앱)

전체가 새로 추가된 시각화/운용 서버.

| 파일 | 역할 |
|---|---|
| [app.py](../attitude-viewer/app.py) | Flask 엔트리포인트, 전체 API 라우팅 |
| [czml_generator.py](../attitude-viewer/czml_generator.py) | HK 자세/궤도 → CZML(Cesium 시계열 포맷) 변환 |
| [ep_client.py](../attitude-viewer/ep_client.py) | EP 서버 AOI API 프록시 클라이언트 |
| [mce_db.py](../attitude-viewer/mce_db.py) | EP 서버 HTTP API가 특정 케이스에서 부정확한 값을 반환하는 것이 확인되어, 백엔드 DB(`o1b_mce_server.TB_Selected_Mission_Schedule`)를 **읽기 전용**으로 직접 조회하도록 우회 구현 |
| `static/`, `templates/` | Cesium 3D 뷰어, 2D 지도(스캔라인), 사이드바(AOI/미션 선택) UI |

**API 엔드포인트:**

- `GET /api/czml` — 지정 구간 HK 데이터를 CZML로 변환해 3D 궤적/자세 스트리밍
- `GET /api/footprint`, `/api/capture-events` — 사전 계산된 footprint CSV를 2D 지도용 JSON으로 제공, 타겟 통과 이벤트 탐지
- `GET /api/ep/aoi` — EP 서버 AOI 목록 프록시
- `GET /api/ep/missions` — 미션 이력 (EP API 대신 백엔드 DB 직접 조회)
- `GET /api/footprint/compute` — **핵심 신규 기능**: 임의 좌표(AOI/미션)·임의 시간대에 대해 HK 조회 → DEM 타일 on-demand 확보 → Java/Rugged 파이프라인 호출까지 한 번에 수행해 실시간 footprint 계산

### 3.5 문서화

- [docs/ep-server-api-reference.txt](ep-server-api-reference.txt) — EP 서버(AOI/미션/TLE) API 명세 정리, `sat_footprint` 연동 시 참고용

## 4. 알아둘 설계 포인트

- **자세 컨벤션**: quaternion은 scalar-first(q0=w), body→ECI, +Z축=nadir, roll ≤15° (프로젝트 공통 컨벤션)
- **좌표계**: EME2000(관성계, 위성 궤적) → Orekit이 ITRF(지구고정계)로 변환 → WGS84 타원체 + ASTGTM DEM과 교차
- **HK API vs DEM 서버 역할 분담**: HK API는 실측 자세와 카메라 광선(ECEF) 원시 데이터 제공, 이쪽(DEM 서버)은 그 광선을 실제 지형과 교차시켜 정밀 폴리곤을 만드는 역할
- **AOI 관리**: 원본 소유는 EP 서버, 이 프로젝트는 프록시/조회만 수행 (자체 CRUD 없음)

## 5. 미구현 / TODO

- **위성 선택 + O1B 연동**: `attitude-viewer`에 랜딩 페이지(`/`, TLE 기반 O1A/O1B 궤도 애니메이션으로 위성 선택 → `/viewer?satellite=`)를 추가했고, O1B HK 텔레메트리가 O1A와 같은 DB('nstanl') 안에 `tbl_obs1b_hk1~6`로 이미 존재함을 확인해 `schema_map.py`/`hk_loader.py`를 위성별 테이블 선택이 가능하도록 일반화함 (`HK_ENABLED_SATELLITES = {"O1A", "O1B"}`). 미션 조회·CZML·footprint 계산 모두 O1B로 확인 완료.
- **궤도 전파(TLE 기반, E3T 등)**: `/api/footprint/compute`는 여전히 실측 HK 텔레메트리가 있는 위성(O1A/O1B)만 지원. EP 서버의 TLE 조회 API(`/api/TLE/status/{id}`)를 받아 Orekit `TLEPropagator`로 HK 데이터가 없는 구간·위성(E3T 등)까지 footprint를 예측하는 기능은 아직 미구현 (랜딩 페이지의 궤도 애니메이션은 이미 이 API를 쓰지만 시각화 전용이고 footprint 계산에는 아직 연결 안 됨)
- **AOI 자체 관리 기능**: 현재는 EP 서버 프록시 수준 — 필요 시 자체 저장/CRUD 확장 검토
