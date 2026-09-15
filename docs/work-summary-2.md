# sat_footprint 2차 코드 통합 — 3축 모듈화

브랜치: `seoyoon-astgtm` / 작성일: 2026-09-15 (1차 정리: [work-summary.md](work-summary.md), 2026-09-03)

## 1. 개요: 왜 3축 구조로 재편했는가

1차 통합(work-summary.md) 시점에는 `attitude-viewer`(신규 Flask 앱) + `python/footprint`·`java`(DEM footprint 계산) 두 축이었고, HK 텔레메트리는 `python/hk_loader`가 직접 DB에 붙어서 가져왔다.

이후 동료(도은)가 별도로 개발한 `origin/doeun-space` 브랜치(FastAPI 기반, HK 텔레메트리·미션스케줄·자체 좌표변환·궤도전파 등을 API로 제공)를 어디까지/어떻게 편입할지 검토했고, 최종적으로 **"통째로 가져와서 독립 서비스로 세우고, 우리 쪽은 그걸 호출만 한다"**는 방향으로 정했다. 그 결과 프로젝트가 역할이 뚜렷한 3개 축으로 재편됐다:

```
sat_footprint/
├── attitude-viewer/     축 1: 웹서버 + 오케스트레이션 (Flask, Cesium 3D/2D 뷰어)
├── hk_api/               축 2: HK 텔레메트리·미션스케줄·좌표변환 서비스 (FastAPI, 독립 프로세스)
└── footprint-backend/   축 3: DEM 기반 실측 footprint 계산 (Java/Orekit/Rugged + Python 래퍼)
    ├── java/
    └── python/
```

세 축은 서로 다른 실행 주체(프로세스/포트/venv)를 가지며, `attitude-viewer`가 나머지 둘을 **호출하는 쪽**이다. `hk_api`와 `footprint-backend`는 서로를 직접 알지 못한다 — 아직은 (§4.5 참고).

## 2. 각 축 상세

### 2.1 `attitude-viewer` — 웹서버 + 오케스트레이션

포트 8080, `/sat_footprint` prefix로 서빙. 사용자가 보는 유일한 진입점.

| 파일 | 역할 |
|---|---|
| [app.py](../attitude-viewer/app.py) | Flask 엔트리포인트 — 전체 라우팅, hk_api/footprint-backend 호출 오케스트레이션 |
| [czml_generator.py](../attitude-viewer/czml_generator.py) | 자세/궤도 데이터 → CZML(Cesium 시계열 포맷) 변환 |
| [mce_db.py](../attitude-viewer/mce_db.py) | 현서버 미션 스케줄 — **hk_api `/mission/schedule`을 부르는 얇은 HTTP wrapper**로 재작성 (이전엔 자체 DB 직결) |
| [mps_db.py](../attitude-viewer/mps_db.py) | `/old` 레거시 미션서버 미러 — 여전히 자체 DB(`secrets.toml`의 `[mission_db]`) 직결. hk_api와 무관한 별도 서버라 그대로 둠 |
| [ep_client.py](../attitude-viewer/ep_client.py) | EP 서버(AOI) 프록시 클라이언트 |
| `static/`, `templates/` | Cesium 3D 뷰어, 2D 지도, 사이드바 UI |

**API 엔드포인트** (`app.py`):

- `GET /`, `/viewer` — 현서버(O1A/O1B) 랜딩·3D 뷰어
- `GET /old`, `/old/viewer` — 레거시 미션서버 미러
- `GET /api/czml` — HK 자세/궤도 → CZML (`coord_model` 파라미터로 좌표변환 모델 선택 — §4.4)
- `GET /api/footprint/compute`, `/api/footprint/progress` — footprint-backend(Java) 실시간 계산
- `GET /api/ep/aoi`, `/api/ep/missions` — AOI/미션 조회
- `GET /api/tle/<sat>`, `/api/sensor-calibration/<sat>`, `/cesium-token` — 보조 데이터
- **`ANY /api/hk/<subpath>` — hk_api로의 리버스 프록시** (§3.1)

### 2.2 `hk_api` — HK 텔레메트리 · 미션스케줄 · 좌표변환 서비스

`origin/doeun-space`를 `git archive`로 통째로 벤더링한 **독립 FastAPI 서비스**. 포트 8001, `127.0.0.1`만 바인딩(외부 비공개), 별도 Python 3.12 venv(`hk_api/.venv`)에서 자체 실행. `attitude-viewer`가 `/api/hk/*` 프록시를 통해서만 접근한다.

라우터 구성 (`main.py`):

| 라우터 | 엔드포인트 | 상태 |
|---|---|---|
| `telemetry` | `/telemetry/query`, `/telemetry/mission-hk`, `/telemetry/czml` | **사용 중** — `_load_attitude_or_error`, `coord_model=hkapi` 경로 |
| `footprint` | `/footprint/compute`, `/footprint/czml`, `/footprint/rays`, `/footprint/track(/czml)`, `/footprint/line/track(/geojson|/czml)` | 미연동 — footprint-backend(Java/DEM)와 별개 구현, 통합 방식은 미정 (§5) |
| `propagation` | `/propagation/track(/czml)` | 보존만, 미사용 |
| `validator` | `/validator/ops-status` | 보존만, 미사용 |
| `mission` | `/mission/schedule` | **사용 중** — `mce_db.py`가 호출 |

핵심 모듈: [core/coordinates.py](../hk_api/core/coordinates.py) — 자체 IAU-76/FK5 축약 모델(세차 full + 장동 Meeus 저정밀도 4항 + GMST/GAST)로 ECI↔ECEF 변환. `build_cesium_track_czml()`이 `coordinate_frame` 파라미터(`"eci"`/`"ecef"`)로 **position만** 선택적으로 변환하고, **orientation은 항상 body→ECEF로 변환**(CZML 스펙이 그렇게 정의하기 때문 — coordinates.py:389 주석).

### 2.3 `footprint-backend` — DEM 기반 실측 footprint 계산

Java(Orekit/Rugged) + Python 래퍼. hk_api·attitude-viewer와 독립적으로, **실측 DEM(ASTGTM) 지형 교차 기반 정밀 footprint**를 계산하는 유일한 경로. 이번 라운드에 `java/`, `python/` 두 최상위 폴더를 이 밑으로 재편했다 (§3.6).

```
footprint-backend/
├── java/    Orekit + Rugged, Main.java(CLI), FootprintCalculator.java 등
└── python/footprint/   io_adapter.py, pipeline.py(Java 서브프로세스 호출), dem_tiles.py, response.py
```

`attitude-viewer`의 `/api/footprint/compute`가 이 파이프라인을 실시간 호출한다 (HK 조회 → DEM 타일 on-demand 확보 → Java 서브프로세스).

## 3. 이번 라운드 구현 사항

### 3.1 hk_api 벤더링 + 리버스 프록시

`origin/doeun-space` 전체를 `hk_api/`로 벤더링, 독립 실행 가능한 서비스로 세움. `attitude-viewer/app.py`의 `/api/hk/<subpath>` 라우트가 모든 메서드를 그대로 프록시 (`app.py:104`). 별도 포트(`/api/`와 겹치는 `/api/footprint/compute`가 이미 있어서 `/api/hk/` 하위로 분리 배치).

### 3.2 텔레메트리 완전 이전 — `python/hk_loader` 삭제

기존에는 `python/hk_loader`가 HK DB에 직접 붙어 있었는데, hk_api의 `/telemetry/query`로 완전히 대체하고 `python/hk_loader` 디렉터리 자체를 삭제했다. `app.py`의 `_load_attitude_or_error()`가 유일한 진입점.

### 3.3 `mce_db.py` → HTTP wrapper 전환

기존엔 현서버 미션 DB에 직접 연결했는데, hk_api의 `/mission/schedule`을 호출하는 얇은 wrapper로 재작성. 함수 시그니처는 그대로 유지해서 호출부(`app.py`, `mps_db.py`) 변경 없음.

### 3.4 쿼터니언 방향 버그 근본 원인 수정

hk_api의 `hk_loader.py`가 DB 원본 쿼터니언을 스칼라 재정렬 후 **한 번 더 방향을 뒤집는(`_invert_quaternion_rotation_direction`) 로직**을 갖고 있었는데, 이게 실제로는 불필요한(오히려 틀린) 변환이었음을 두 가지 독립적 실측으로 확인:

1. Java/Orekit/Rugged footprint 파이프라인에 뒤집은 값 vs 안 뒤집은 값을 각각 흘려보내는 A/B 테스트 — 뒤집으면 300초+ 타임아웃/메모리 폭증, 안 뒤집으면 실제 타겟과 1.756km 이내로 일치
2. hk_api 자체 `core/coordinates.py`의 ray-ellipsoid 교차 계산으로 직접 검증 — 뒤집으면 지구조차 안 맞는 방향(no intersection), 안 뒤집으면 실제 타겟 27.5km 이내

**수정 위치**: 다운스트림(우리 쪽)에서 보정하는 대신, [hk_api/core/loader/hk_loader.py](../hk_api/core/loader/hk_loader.py)에서 `_invert_quaternion_rotation_direction()` **호출 자체를 제거** — 근본 원인을 소스에서 고침.

### 3.5 좌표변환 모델 비교: Cesium 내장 모델 vs hk_api(도은) 자체 모델

`hk_api/core/coordinates.py`가 자체 IAU-76/FK5 ECI↔ECEF 모델을 갖고 있는 게 확인되면서, 우리가 지금까지 써온 "Cesium 내장 변환(`referenceFrame: INERTIAL`, 브라우저가 렌더링 시점에 자동 변환)"과 실제로 얼마나 차이 나는지 실측 비교했다.

**검증 방법**: 실제 CesiumJS 1.119(CDN에서 그대로 로드)를 헤드리스 Edge로 띄워, `Cesium.Transforms.computeIcrfToFixedMatrix()`(Cesium 내장 모델)로 변환한 결과와 hk_api `/telemetry/czml?coordinate_frame=ecef`(자체 모델)의 결과를 같은 위성/시간구간(O1A, 2026-08-08)으로 직접 대조.

**결과**:

| 항목 | 차이 |
|---|---|
| 위치(position) | 평균 4.7m, 최대 5.5m |
| 자세(orientation) | 평균 0.0006°, 최대 0.002° |

→ 두 모델이 사실상 동일한 결과를 낸다는 게 실측으로 확정됨. (참고: 검증 과정에서 두 차례 자체 스크립트 실수를 거쳤다 — ① `coordinate_frame`이 orientation에는 적용 안 된다는 걸 모르고 이미 ECEF인 값에 변환을 중복 적용, ② `qbody_wrt_eci1..4`가 scalar-first(w,x,y,z)인 걸 모르고 Cesium 쿼터니언 생성자에 순서 그대로 넣음. 둘 다 hk_api 코드가 아니라 검증 스크립트 쪽 버그였고, hk_api 자체 수학은 self-consistency 테스트로 별도 검증 완료.)

**구현**: `GET /api/czml`에 `coord_model=cesium|hkapi` 파라미터 추가 (기본값 `cesium`, 기존 동작 불변).

- `cesium` — hk_api `/telemetry/query`(원본 ECI) → `referenceFrame: INERTIAL` 태깅 → Cesium이 렌더링 시점에 자동 변환
- `hkapi` — hk_api `/telemetry/czml?coordinate_frame=ecef`(자체 변환 완료본) → `referenceFrame` 생략(CZML 기본값 FIXED)

`/viewer?satellite=O1A&coord_model=hkapi`처럼 페이지 URL에 붙이면 뷰어 전체가 그 모드로 로드된다 (`templates/index.html`의 `window.APP_COORD_MODEL` → `cesium-viewer.js`의 `loadCzml()`이 자동으로 실어 보냄).

**발견 및 수정한 실사용 버그**: `coord_model=hkapi`로 실제 테스트했을 때 위성이 타겟 위를 안 지나가는 문제 발견. 원인은 hk_api가 `coordinate_frame` 값과 무관하게 **orientation은 항상 body→ECEF로 변환**해서 내려주는데, 클라이언트(`cesium-viewer.js`)의 FOV 피라미드/보어사이트 계산 로직(`bodyToEci()` → `eciDirToEcef()`)이 orientation을 항상 body→ECI로 가정하고 ICRF→Fixed 회전을 **한 번 더** 적용하고 있었던 것 — 이중회전 버그. `eciDirToEcef()`에 `window.APP_COORD_MODEL === 'hkapi'`일 때 변환을 건너뛰는 분기를 추가해 해결 (FOV 코너·FOV 중심점·Z축 화살표 3곳 모두 이 함수 하나를 거치므로 한 곳만 수정하면 전부 해결됨). 수정 후 재검증: 두 모드의 FOV 중심 지상점 차이 1.8km(샘플링 타이밍 오차 수준)로 수렴.

### 3.6 `java/`, `python/` → `footprint-backend/`로 재구조화

1차 통합 시점엔 `java/`, `python/`이 최상위에 나란히 있었는데, 사실상 하나의 백엔드(Java가 실계산, Python은 그 위의 얇은 래퍼)라 `hk_api/`, `attitude-viewer/`와 대칭되도록 `footprint-backend/{java,python}/`로 묶었다. `git mv`로 이동해 히스토리 보존, `app.py`의 `sys.path.insert`·`JAVA_PROJECT_DIR` 경로 수정, `.gitignore`의 `java/target/` 규칙 갱신, 실제 Flask 재시작 + Java 서브프로세스 호출(574라인 실계산)까지 재검증 완료.

### 3.7 죽은 코드 정리

- `app.py`의 레거시 `/api/footprint`, `/api/capture-events`(옛 Paju CSV 고정 경로) 라우트 제거 — 실제 호출부(`map2d.js`의 `loadData()`)도 죽어있던 걸 확인 후 같이 제거
- `czml_generator.py`의 서버사이드 FOV 피라미드 생성 로직(`generate_fov_pyramid_eci()`, `sensor_fov` CZML 패킷) 제거 — 호출부가 항상 `show_fov=False`로 고정 호출해서 절대 실행되지 않던 코드
- `/api/czml`의 무의미한 `fov_angle`/`show_fov` 쿼리파라미터 파싱 제거

## 4. 이전 라운드(work-summary.md) 이후 추가된 그 외 개선 (참고)

- **`/old` 레거시 미러 좌표 정확도**: 좌표 카탈로그(`data/old_server_data.xlsx`) + 현서버 DB `scanStart` 교차참조로 미션 위경도 보정
- **FOV 시각화 실측 반영**: 인공적인 along-track FOV 스프레드(비현실적) 대신, 실제 센서 하드웨어 스펙(포컬플레인 row offset, pixel pitch)으로 PAN/BLUE/GREEN/RED/RED_EDGE1/NIR 6개 밴드를 시간축 오프셋으로 환산해 표시
- **FOV 피라미드-실측 footprint 시각 정합**: sphere→WGS84 타원체 교차(`Cesium.IntersectionTests.rayEllipsoid`)로 교체

## 5. 남은 작업 / 보류 중인 설계 결정

- **hk_api `/footprint/*`와 footprint-backend(Java/DEM) 통합 방식** — 사용자 요청으로 "생각해보자"로 보류 중. hk_api의 `/footprint/rays`, `/footprint/line/track`이 우리 Java 파이프라인과 개념적으로 겹치는 부분이 있어 정리 필요
- **`propagation`/`validator` 라우터** — 살려두되 아직 미사용. TLE 기반 궤도전파(HK 텔레메트리 없는 위성/구간)에 활용 가능성
- **AOI 자체 관리 기능** — 현재는 EP 서버 프록시 수준

## 6. 핵심 설계 포인트 요약

- **쿼터니언 컨벤션**: `qbody_wrt_eci1..4`는 scalar-first(w,x,y,z), body→ECI가 정본. CZML/Cesium에 넣을 때만 scalar-last(x,y,z,w)로 재배열 — **부호 반전은 어디서도 하지 않음**(과거 이 지점에서 반복적으로 버그가 났던 곳)
- **CZML orientation은 스펙상 항상 body→fixed** — 우리 `czml_generator.py`(coord_model=cesium)는 관례적으로 raw ECI를 그대로 태우고 Cesium 클라이언트가 알아서 처리하게 하는 비표준 사용법이고, hk_api(coord_model=hkapi)는 스펙대로 서버에서 미리 ECEF로 변환. 이 차이를 인지하지 못하면 클라이언트 쪽에서 이중변환 버그가 남 (§3.5)
- **위치(position)는 Cesium이 항상 자동 처리**, **자세(orientation)는 우리가 `coord_model`을 보고 수동 분기**해야 하는 구조 — `cesium-viewer.js`의 `eciDirToEcef()`가 그 유일한 분기점
