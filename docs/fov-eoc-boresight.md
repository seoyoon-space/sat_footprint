# 3D 뷰어 FOV/Z축 보어사이트 계산 원리

작성일: 2026-09-08 / 브랜치: `seoyoon-astgtm`

이 문서는 attitude-viewer의 Cesium 3D 화면에 표시되는 **FOV(자홍색 피라미드)**와 **Z축(파란 화살표)**이
서버의 실제 footprint 계산(Java/Rugged)과 같은 방향을 가리키도록 맞춘 작업을 코드 레벨로 정리한 것.

## 1. 배경 — 무엇이 문제였나

서버(Rugged)가 실제로 footprint를 계산할 때 쓰는 카메라 보어사이트 방향과, 3D 화면에 그려지는 FOV/Z축 화살표의
방향은 원래 서로 다른 계산 경로였다.

- **서버**: `body +Z` 방향에 "EOC(카메라) 마운팅 보정"이라는 실측(as-built) 회전을 적용해서 계산.
- **클라이언트(3D 화면)**: 이 보정을 전혀 모른 채 순수 `body +Z`만 그려서 표시.

O1A는 보정값이 `(0,0,0)`(무보정)이라 두 경로가 우연히 일치했지만, **O1B는 실측 보정값이 실제로 존재**해서
두 경로의 결과가 어긋나 있었다. 이 문서는 그 어긋남을 없앤 작업을 다룬다.

## 2. EOC 마운팅 보정이란

카메라가 위성 몸체에 완벽하게 수직으로 장착되지 않고, 아주 미세하게 틀어진 채로 조립된다. 이 실측 틀어짐을
`data/sensor_calibration.json`에 위성별 unit vector로 저장해 두고, 서버 계산 시 "nominal boresight(body +Z)를
이 벡터 방향으로 틀어주는 최소 회전"을 적용한다.

```json
// data/sensor_calibration.json
{
  "O1A": { "eoc_misalignment_unit_vector": [0.0, 0.0, 0.0] },       // 보정값 없음 → 무보정
  "O1B": { "eoc_misalignment_unit_vector": [0.012514, -0.003196, 0.999917] }  // 실측값
}
```

값의 출처는 `AC_TBL_UNIT_VECTOR_X/Y/Z (Index 8) EOC Misalignment_fixed` 파라미터 테이블 — 벡터가 정확히
`(0,0,1)`이 아니라 X/Y에 작은 성분이 섞여 있는 것 자체가 "카메라가 몸체 Z축에서 살짝 기울어져 있다"는 실측
결과다. O1B의 경우 이 기울기는 약 **0.74°**이고, 궤도고도 약 500km 기준으로 환산하면 지상에서 약 **6.5km**
위치 오차에 해당한다.

### 2.1 서버(Java) 쪽 계산 — `SensorCalibration.java` + `FootprintCalculator.java`

[SensorCalibration.java](../footprint-backend/java/src/main/java/footprint/SensorCalibration.java)가 위 JSON을 읽어
`Vector3D`로 반환한다 (파일/위성 항목/필드가 없으면 `Vector3D.ZERO`, 즉 무보정).

[FootprintCalculator.java](../footprint-backend/java/src/main/java/footprint/FootprintCalculator.java)의 생성자에서
픽셀별 LOS(Line-of-Sight) fan을 만든 뒤, 두 회전을 순서대로 적용한다:

```java
// 1) mounting 보정 — 현재 두 위성 모두 mountingErrorDeg=0.0(SensorSpec.java)이라 사실상 no-op
losBuilder.addTransform(new FixedRotation("mounting", Vector3D.PLUS_I,
        FastMath.toRadians(sensor.mountingErrorDeg)));

// 2) EOC 마운팅 보정 — (0,0,1)을 eocMisalignmentUnitVector로 보내는 "최소 회전"
//    norm이 0에 가까우면(O1A, 또는 EOC 보정 OFF 요청 시) 아예 적용하지 않는다.
if (eocMisalignmentUnitVector != null && eocMisalignmentUnitVector.getNorm() > 1e-9) {
    Rotation misalignment = new Rotation(Vector3D.PLUS_K, eocMisalignmentUnitVector.normalize());
    losBuilder.addTransform(new FixedRotation("eoc_misalignment",
            misalignment.getAxis(RotationConvention.VECTOR_OPERATOR), misalignment.getAngle()));
}
```

`new Rotation(Vector3D.PLUS_K, eocVector)`는 Hipparchus(Orekit이 쓰는 수학 라이브러리)의 API로,
**"Z축 `(0,0,1)`을 `eocVector` 방향으로 돌리는, 가능한 회전 중 가장 작은 회전"**을 만든다. 이후 이 회전으로
LOS fan 전체(픽셀별 방향 벡터들)를 일괄 회전시켜서, 실제 계산은 항상 이 보정된 방향 기준으로 이루어진다.

이 회전이 3D 뷰어에서 재현해야 하는 핵심 수식이다.

## 3. 클라이언트(3D 뷰어) 쪽 — 서버와 같은 회전을 JS로 재현

### 3.1 보정 벡터를 서버에서 가져오기

[app.py](../attitude-viewer/app.py)에 `sensor_calibration.json`을 그대로 노출하는 엔드포인트를 추가:

```python
@app.get("/api/sensor-calibration/<satellite_id>")
def api_sensor_calibration(satellite_id):
    calibration = json.loads(SENSOR_CALIBRATION_PATH.read_text(encoding="utf-8"))
    vector = calibration.get(satellite_id.upper(), {}).get("eoc_misalignment_unit_vector", [0.0, 0.0, 0.0])
    return jsonify({"eoc_misalignment_unit_vector": vector})
```

[cesium-viewer.js](../attitude-viewer/static/cesium-viewer.js)는 뷰어 생성 시 현재 위성(`window.APP_SATELLITE`)
기준으로 이 값을 한 번 fetch해서 `_eocVectorRaw`에 저장한다(`initCesium()` 안).

### 3.2 Java의 `Rotation(PLUS_K, eocVector)`를 JS 쿼터니언으로 재현

Java 코드와 수학적으로 동일한 "Z축 → eocVector 최소 회전"을 Cesium 쿼터니언으로 계산하는 함수:

```js
// cesium-viewer.js
function _eocRotationQuaternion(vec) {
  var normSq = vec[0]*vec[0] + vec[1]*vec[1] + vec[2]*vec[2];
  if (normSq < 1e-9 * 1e-9) return Cesium.Quaternion.IDENTITY.clone(...); // 무보정

  var z = new Cesium.Cartesian3(0, 0, 1);
  var v = Cesium.Cartesian3.normalize(new Cesium.Cartesian3(vec[0], vec[1], vec[2]), ...);
  var dot = Cesium.Math.clamp(Cesium.Cartesian3.dot(z, v), -1, 1);
  var axis = Cesium.Cartesian3.normalize(Cesium.Cartesian3.cross(z, v, ...), ...);
  return Cesium.Quaternion.fromAxisAngle(axis, Math.acos(dot), ...);  // z와 v 사이 각/축으로 회전 생성
}

function _rotateBodyDir(x, y, z, eocQuat) {
  var m = Cesium.Matrix3.fromQuaternion(eocQuat, ...);
  return Cesium.Matrix3.multiplyByVector(m, new Cesium.Cartesian3(x, y, z), ...);
}
```

원리: 두 단위벡터 `z=(0,0,1)`과 `v=eocVector`가 있을 때, 회전축은 `cross(z, v)`, 회전각은
`acos(dot(z, v))` — 이 축·각으로 만든 회전이 정확히 `z`를 `v`로 보내는 최소 회전이다. Java의
`new Rotation(Vector3D.PLUS_K, eocVector)`와 동일한 정의.

`_eocCorrectionEnabled` 플래그로 EOC 보정 on/off 체크박스와 연동되고(§3.5), 실제 사용되는 벡터는 항상
`_effectiveEocVector()`를 거쳐서 나온다(꺼져 있으면 `[0,0,0]` = 무보정).

### 3.3 지구 표면 교차 — 구(sphere)에서 WGS84 타원체로

FOV/Z축이 "어느 방향을 향하는지"가 정해지고 나면, 그 방향의 광선을 지구와 교차시켜야 실제 위도/경도/고도
좌표가 나온다. 이 교차 계산은 이번 EOC 작업 이전부터 이미 완전한 구(sphere)가 아니라 **WGS84 타원체**를
기준으로 하고 있었다 — 관련 코드/주석:

```js
// cesium-viewer.js — _rayEarthIntersect
// WGS84 (equatorial radius 6378137m, polar radius ~6356752m) — a sphere puts the
// ground point up to ~21km off at high latitudes; the real footprint pipeline
// (Rugged) intersects the actual DEM against this same ellipsoid, so matching the
// ellipsoid here (still no terrain) is the correct first-order fix for this pyramid.
function _rayEarthIntersect(pos, dir) {
  var ellipsoid = (viewer.scene.globe && viewer.scene.globe.ellipsoid) || Cesium.Ellipsoid.WGS84;
  var interval = Cesium.IntersectionTests.rayEllipsoid(new Cesium.Ray(pos, dir), ellipsoid);
  ...
}
```

- 지구를 완전한 구(고정 반지름)로 근사하면, 적도 반지름(~6378km)과 극 반지름(~6357km)의 차이 때문에
  고위도로 갈수록 지면 교점이 최대 약 **21km**까지 어긋난다.
- 서버(Rugged)는 실제 DEM(지형고도)을 이 WGS84 타원체 위에 얹어서 계산하므로, 3D 화면도 최소한 같은
  타원체를 기준으로 삼아야 "지형까지는 아니어도" 서버 결과와 1차적으로 어긋나지 않는다.
- 이 부분은 이번 세션 이전부터 이미 WGS84로 되어 있었다. 다만 git 히스토리상 attitude-viewer 전체가 하나의
  통합 커밋(`f287784`)으로 들어와 있어서, "구에서 WGS84로 바뀐 시점"을 별도 커밋/날짜로 특정할 수는 없다 —
  코드 주석에 이유만 남아 있는 상태.
- 정리하면 화면에 보이는 FOV/Z축은 두 단계가 합쳐진 결과다: **①(이번 세션) 어느 방향으로 광선을 쏠지 —
  EOC 마운팅 보정 반영(§2~§3.2, §4)**, **②(기존) 그 광선이 지구와 어디서 만나는지 — 구 대신 WGS84
  타원체 사용**.

### 3.4 FOV 피라미드/중앙점에 적용

기존에는 body 프레임에서 만든 방향 벡터(모서리 4개 + 중앙 `(0,0,1)`)를 그대로 `bodyToEci()`에 넣었는데,
이제 그 전에 `_rotateBodyDir()`로 한 번 돌리고 넣는다:

```js
// 모서리 4개 (FOV 피라미드 기둥)
function cornerDirsBody(effectiveAngleDeg) {
  var raw = [...];  // 기존과 동일한 4개의 body-frame 방향
  var eocQuat = _eocRotationQuaternion(_effectiveEocVector());
  return raw.map(function(d) {
    var r = _rotateBodyDir(d[0], d[1], d[2], eocQuat);
    return [r.x, r.y, r.z];
  });
}

// 중앙점
var eocQuat = _eocRotationQuaternion(_effectiveEocVector());
var centerBody = _rotateBodyDir(0, 0, 1, eocQuat);   // O1B는 eocVector 그 자체와 같아짐
var dirEci = bodyToEci(centerBody.x, centerBody.y, centerBody.z, ori);
```

`bodyToEci()`(자세 쿼터니언으로 body→ECI 변환) → `eciDirToEcef()`(ECI→ECEF) → `_rayEarthIntersect()`
(WGS84 타원체와 광선 교차, §3.3)로 이어지는 나머지 파이프라인은 기존 그대로다. 즉 **"어떤 방향을 쏠 것인가"만
EOC 보정이 반영되도록 바꾸고, 그 방향을 지구와 교차시켜 화면 좌표로 바꾸는 부분은 손대지 않았다.**

### 3.5 EOC on/off 토글 연동

`index.html`의 체크박스(`#chk-eoc-correction`)는 원래 footprint 계산(Java) 요청에만 쓰였는데, 3D 뷰어에도
실시간으로 반영되도록 [sidebar.js](../attitude-viewer/static/sidebar.js)에 연결했다:

```js
function wireEocToggle() {
  var chk = $('chk-eoc-correction');
  window.hkCesium.setEocCorrectionEnabled(chk.checked);          // 초기 상태 동기화
  chk.addEventListener('change', function () {
    window.hkCesium.setEocCorrectionEnabled(chk.checked);        // 체크박스 바뀔 때마다 즉시 반영
  });
}
```

`cesium-viewer.js`의 `setEocCorrectionEnabled(enabled)`는 `_eocCorrectionEnabled` 변수만 바꾼다 — FOV는
매 프레임 `CallbackProperty`로 다시 계산되므로, footprint를 다시 계산하지 않아도 체크박스만 껐다 켜면 화면이
즉시 순수 `+Z` ↔ 보정된 방향으로 바뀐다.

## 4. Z축(파란 화살표)도 같은 방식으로 통일

### 4.1 기존 방식의 문제

기존에는 Z축 화살표를 서버([czml_generator.py](../attitude-viewer/czml_generator.py))가 미리 계산해서
CZML(시계열 JSON) 안에 "구워서" 내려보냈다 — HK 샘플마다 `position + R(quaternion) @ (0,0,500km)`를
계산해 그 결과 좌표만 담아 보내는 방식. 이 계산은:

1. EOC 보정을 전혀 모른다 (순수 body +Z만 사용).
2. 샘플 사이 구간은 "회전"이 아니라 이미 계산된 좌표를 **직선으로 이어서** 보간한다 — FOV처럼 매 프레임 자세
   쿼터니언을 다시 보간하는 방식과 수학적으로 다르다.

1번은 O1B에서 실제로 어긋남이 보였다(FOV는 보정 후 화면 반영, Z축은 그대로 무보정).

### 4.2 수정 — Z축도 클라이언트에서 실시간 계산

`czml_generator.py`에서 Z축(axis_z) 샘플/엔티티 생성을 아예 제거했다 (X/Y축은 그대로 유지 — 몸체의 기준축일
뿐 카메라 보정과 무관하므로 손대지 않음). 대신 `cesium-viewer.js`에 FOV 중앙점과 **완전히 같은 계산식**을
쓰는 새 엔티티(`_zAxisEntity`)를 추가했다:

```js
_zAxisEntity = viewer.entities.add({
  polyline: {
    positions: new Cesium.CallbackProperty(function(time) {
      // ... _fovCenterEntity와 동일하게 eocQuat 적용한 방향(dirEcef)을 구한 뒤
      var groundHit = _rayEarthIntersect(pos, dirEcef);
      var len = groundHit ? Math.min(500000.0, Cesium.Cartesian3.distance(pos, groundHit)) : 500000.0;
      var tip = Cesium.Cartesian3.add(pos, Cesium.Cartesian3.multiplyByScalar(dirEcef, len, ...), ...);
      return [pos, tip];
    }, false),
    material: new Cesium.PolylineArrowMaterialProperty(Cesium.Color.fromCssColorString('#3232FF')),
  },
});
```

같은 함수(`_eocRotationQuaternion`, `_rotateBodyDir`, `bodyToEci`, `eciDirToEcef`)를 FOV 중앙점과 공유하므로
**Z축과 FOV는 이제 구조적으로 절대 어긋날 수 없다** (계산식이 하나이기 때문).

### 4.3 부수적으로 발견/수정한 문제 — Z축이 지구를 뚫고 들어감

Z축 화살표 길이는 원래(서버가 굽던 시절부터) 고정 **500km**였는데, O1A/O1B의 실제 궤도 고도는 약
**444km**다. O1A는 `+Z`가 지구 방향(nadir)이므로, 고정 500km 직선을 그으면 지면(444km 지점)을 지나쳐 약
**56km를 더 뚫고 내려간다**.

수정: FOV 중앙점이 쓰는 지구-교차 계산(`_rayEarthIntersect`)을 재사용해서, 실제 지면까지 거리가 500km보다
짧으면 그 지점에서 선을 멈추도록 클램프했다 (§4.2 코드의 `groundHit`/`len` 부분). 지구를 안 향하는 방향(자세가
심하게 틀어져 광선이 지구를 벗어나는 경우)에는 기존처럼 500km로 표시된다.

## 5. 결과 요약

| 위성 | EOC 벡터 | body Z축과의 각도 | X축과의 직교 편차 | Y축과의 직교 편차 |
|---|---|---|---|---|
| O1A | `(0,0,0)` (무보정) | 0° | 0° (정확히 90°) | 0° (정확히 90°) |
| O1B | `(0.012514,-0.003196,0.999917)` | 약 0.74° | 약 0.72° | 약 0.18° |

- O1A: FOV/Z축/서버 계산이 모두 순수 `+Z` — 화면과 서버가 항상 일치.
- O1B: FOV/Z축이 이제 서버와 같은 보정된 방향을 가리킴. 단, 이 보정 벡터 자체가 body X/Y축과 정확히
  직교하지 않는(위 표의 편차) 실측값이므로, 화면상 파란 Z축 화살표가 빨강/초록 화살표와 완벽한 90°를
  이루지 않는 것은 **버그가 아니라 실제 마운팅 오차를 그대로 시각화한 것**이다.

## 6. 알아둘 한계 / 범위

- **지면 높이는 여전히 WGS84 타원체 근사다.** 실제 DEM(지형고도) 반영은 서버(Rugged) 계산에만 있고,
  브라우저로 타일 데이터를 통째로 보낼 수 없어 3D 화면에는 반영하지 않았다 — 이번 수정은 "보어사이트
  방향"만 서버와 일치시킨 것이고, 구 대신 WGS84 타원체를 쓰는 것(§3.3) 자체는 이번 세션 이전부터 있던
  별개의 개선사항이다.
- `mountingErrorDeg`(SensorSpec.java)는 두 위성 모두 여전히 `0.0` 하드코딩이고 JSON으로도 노출되지 않는다
  — 현재는 no-op이라 클라이언트에 반영할 실제 값이 없다. 나중에 이 값이 0이 아니게 바뀌면 서버·클라이언트
  양쪽에 추가 작업이 필요하다.
- X/Y축(빨강/초록 화살표)은 여전히 `czml_generator.py`가 서버에서 굽는 방식 그대로다 — 카메라 보정과
  무관한 몸체 기준축이라 이번 작업 범위에서 제외했다.
