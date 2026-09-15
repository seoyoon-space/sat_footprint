/**
 * Cesium 3D attitude viewer — standalone version.
 * Ported from hk-dashboard assets/cesium.js
 *
 * Features:
 *   - CZML orbit + orientation playback
 *   - Body axes visualization (RGB arrows, 500km)
 *   - FOV footprint (ray-sphere intersection, CallbackProperty)
 *   - Playback controls (play/pause, speed, scrubber)
 */
(function(){
  var _pendingOrbit = null;
  var _pendingOrbitView = null;
  var _pendingFlyTo = null;
  var _pendingTarget = null;
  var _pendingLiveSatId = null;
  var _pendingFootprintLines = null;
  var _fovAngleDeg      = 1.6;
  var _fovEntity        = null;
  var _fovCenterEntity  = null;
  var _fovLegEntities   = []; // 4 lines from the satellite down to each corner of the FOV footprint
  var _zAxisEntity      = null; // live EOC-corrected body +Z boresight line (500km) — see _createFovFootprint
  var _fovOverlayRetried = false;
  var _footprintLines      = null; // /api/footprint/compute's `lines` array, set by sidebar.js
  var _footprintLineTimes  = null; // Date.parse(l.t) per entry, index-aligned with _footprintLines — for _interpolateFootprintLine
  var _footprintLineEntity = null; // "current line" polyline inside the FOV, synced to the clock (PAN/reference band)
  var _bandLineEntities    = {};   // other bands' "current line" polylines — band name -> entity
  var _footprintTimeRange  = null; // {min, max} ms — the real line_start~line_end window
  // The CZML-loaded satellite entity lives in its own CzmlDataSource's entity
  // collection (ds.entities), NOT viewer.entities — so it isn't reachable via
  // viewer.entities.getById('satellite') from outside _buildViewer's closure. Kept in
  // sync with _doUpdateOrbit's local `satEntity` var so the band-line callbacks below
  // (defined at module scope, not inside _buildViewer) can still read live position.
  var _activeSatEntity = null;

  // Same closest-by-time lookup as map2d.js's updateScanLine(), so the 3D "current
  // line" and the 2D map's current line always point at the same computed sample.
  function _closestFootprintLine(lines, timeMs) {
    if (!lines || !lines.length) return null;
    var best = null, bestDist = Infinity;
    for (var i = 0; i < lines.length; i++) {
      var t = Date.parse(lines[i].t);
      if (isNaN(t)) continue;
      var d = Math.abs(t - timeMs);
      if (d < bestDist) { bestDist = d; best = lines[i]; }
    }
    return best;
  }

  // `lines` (from /api/footprint/compute) is downsampled to ≤500-ish points for payload
  // size — measured ~0.22s between samples for a typical compute window. But band-to-
  // band along-track offsets are all sub-second (order of ±0.1–0.5s, same magnitude as
  // that spacing), so _closestFootprintLine's nearest-sample snap regularly collapses
  // two *different* bands' shifted lookup times onto the exact same sample — they'd
  // render as bit-for-bit identical lines instead of showing their real, smaller
  // separation. Linear interpolation between the two bracketing samples fixes this: the
  // real DEM ground track is smooth over a fraction of a second, so this is accurate to
  // well within a meter — far finer than the ~0.22s sample grid alone can resolve.
  function _interpolateFootprintLine(lines, times, timeMs) {
    if (!lines || !lines.length || !times || !times.length) return null;
    if (timeMs <= times[0]) return lines[0];
    if (timeMs >= times[times.length - 1]) return lines[lines.length - 1];
    for (var i = 0; i < times.length - 1; i++) {
      if (timeMs < times[i] || timeMs > times[i + 1]) continue;
      var span = times[i + 1] - times[i];
      var f = span > 0 ? (timeMs - times[i]) / span : 0;
      var a = lines[i], b = lines[i + 1];
      var lerp = function(x, y) { return x + (y - x) * f; };
      return {
        ll: [lerp(a.ll[0], b.ll[0]), lerp(a.ll[1], b.ll[1])],
        rl: [lerp(a.rl[0], b.rl[0]), lerp(a.rl[1], b.rl[1])],
        la: lerp(a.la || 0, b.la || 0),
        ra: lerp(a.ra || 0, b.ra || 0),
      };
    }
    return null;
  }

  // ── Multi-band along-track offset ──
  // Each band's line array sits at a different row on the 2D focal plane
  // (PC_TBL_BANDn_START_ROW, real hardware calibration values), offset from the
  // reference band (PAN) purely in the along-track direction. One detector row = one
  // scanned ground line, so a row offset converts straight to a TIME offset via the
  // sensor's line rate — no focal-length/angle math needed for this (that's only
  // required for absolute per-band geolocation, which this isn't). So band X's
  // "current" line is just PAN's line, looked up at (now + Δt_X) instead of now,
  // reusing the same already-computed `_footprintLines` (no extra server calls).
  var BAND_START_ROW = {
    O1A: { PAN: 3188, BLUE: 4376, GREEN: 4036, RED: 3636, RED_EDGE1: 2420, RED_EDGE2: 2260, RED_EDGE3: 1888, NIR: 2852 },
    O1B: { PAN: 3534, BLUE: 4630, GREEN: 4255, RED: 3893, RED_EDGE1: 2797, RED_EDGE2: 2425, RED_EDGE3: 2055, NIR: 3163 },
  };
  var BAND_COLOR = {
    PAN: '#ffffff',
    BLUE: '#3b82f6',
    GREEN: '#22c55e',
    RED: '#ef4444',
    RED_EDGE1: '#fb923c',
    RED_EDGE2: '#f97316',
    RED_EDGE3: '#c2410c',
    NIR: '#a855f7',
  };
  var REFERENCE_BAND = 'PAN';
  // All 8 rows above are kept (real calibration data), but only these 6 are actually
  // active/used bands — RED_EDGE2/RED_EDGE3 exist in the table but aren't drawn.
  var DISPLAY_BANDS = ['PAN', 'BLUE', 'GREEN', 'RED', 'RED_EDGE1', 'NIR'];
  // Per-band show/hide, driven by the checkboxes in the FOV overlay panel (see
  // _ensureFovOverlay) — starts all-on. Combined with _fovShowNow so a band a user
  // unchecked stays hidden even while its polyline would otherwise be in range.
  var _bandVisibility = { PAN: true, BLUE: true, GREEN: true, RED: true, RED_EDGE1: true, NIR: true };

  // Same optical constants as SensorSpec.multiScape200Default() (FootprintCalculator.java)
  // — currently shared by both satellites there too, no per-satellite override.
  var SENSOR_FOCAL_LENGTH_MM = 1067.0;
  var SENSOR_PIXEL_SIZE_UM = 3.2;
  var SENSOR_FMC_GROUND_SPEED_MPS = 3906.173;

  function _lineRateAtAltitude(altitudeM) {
    var ifovRad = (SENSOR_PIXEL_SIZE_UM / 1000.0) / SENSOR_FOCAL_LENGTH_MM;
    var gsd = ifovRad * altitudeM;
    return gsd > 0 ? (SENSOR_FMC_GROUND_SPEED_MPS / gsd) : null;
  }

  function _bandTimeOffsetSec(satId, band, altitudeM) {
    var rows = BAND_START_ROW[satId];
    if (!rows || rows[band] === undefined || rows[REFERENCE_BAND] === undefined) return null;
    var lineRate = _lineRateAtAltitude(altitudeM);
    if (!lineRate) return null;
    return (rows[band] - rows[REFERENCE_BAND]) / lineRate;
  }

  // The FOV pyramid's "front"/"back" corner pairs (cornerDirsBody's along-track ±X
  // spread) aren't real sensor geometry (see the earlier finding: a real pushbroom
  // sensor has zero along-track FOV) — they're a stylized visual half-angle that
  // happens to correspond to a real ground point some time ahead/behind "now". Same
  // idea as the band time-offset above: ground distance covered by that half-angle,
  // divided by ground speed, gives that time offset — used to pull the REAL DEM
  // altitude (la/ra) from the already-computed footprint at that instant, instead of
  // the flat-ellipsoid height _rayEarthIntersect alone would give.
  function _fovAlongTrackTimeOffsetSec(effectiveAngleDeg, altitudeM) {
    var halfAngleRad = Cesium.Math.toRadians(effectiveAngleDeg / 2.0);
    var groundDistM = altitudeM * Math.tan(halfAngleRad);
    return groundDistM / SENSOR_FMC_GROUND_SPEED_MPS;
  }

  // ── EOC 마운팅 보정 (FootprintCalculator.java와 동일 로직) ──
  // 서버는 body +Z 기준 LOS를 mounting(현재 두 위성 모두 0°, no-op) 회전 후 EOC
  // misalignment 회전(Orekit의 `new Rotation(Vector3D.PLUS_K, eocVector)` — (0,0,1)을
  // eocVector로 보내는 최소 회전)을 적용해서 계산한다. 3D FOV도 같은 보정을 적용해야
  // 화면상 FOV 중앙점/피라미드가 실제 계산된 footprint(및 위 "current line")와 같은
  // 방향을 가리킨다. 지면 높이도 footprint가 계산되어 있으면 그 실제 DEM 고도(la/ra)를
  // 가져다 쓴다 (_fovAlongTrackTimeOffsetSec) — 계산 전(라이브 트래킹 등)에는 여전히
  // WGS84 타원체 근사로 표시된다.
  var _eocVectorRaw = [0, 0, 0]; // /api/sensor-calibration/<satellite>에서 로드, [0,0,0]=무보정
  var _eocCorrectionEnabled = true; // sidebar.js의 EOC 마운팅 보정 체크박스와 연동

  function _effectiveEocVector() {
    return _eocCorrectionEnabled ? _eocVectorRaw : [0, 0, 0];
  }

  function _eocRotationQuaternion(vec) {
    var normSq = vec ? (vec[0] * vec[0] + vec[1] * vec[1] + vec[2] * vec[2]) : 0;
    if (normSq < 1e-9 * 1e-9) return Cesium.Quaternion.clone(Cesium.Quaternion.IDENTITY, new Cesium.Quaternion());
    var z = new Cesium.Cartesian3(0, 0, 1);
    var v = Cesium.Cartesian3.normalize(new Cesium.Cartesian3(vec[0], vec[1], vec[2]), new Cesium.Cartesian3());
    var dot = Cesium.Math.clamp(Cesium.Cartesian3.dot(z, v), -1, 1);
    if (dot > 0.9999999) return Cesium.Quaternion.clone(Cesium.Quaternion.IDENTITY, new Cesium.Quaternion());
    if (dot < -0.9999999) {
      return Cesium.Quaternion.fromAxisAngle(new Cesium.Cartesian3(1, 0, 0), Math.PI, new Cesium.Quaternion());
    }
    var axis = Cesium.Cartesian3.normalize(Cesium.Cartesian3.cross(z, v, new Cesium.Cartesian3()), new Cesium.Cartesian3());
    return Cesium.Quaternion.fromAxisAngle(axis, Math.acos(dot), new Cesium.Quaternion());
  }

  function _rotateBodyDir(x, y, z, eocQuat) {
    var m = Cesium.Matrix3.fromQuaternion(eocQuat, new Cesium.Matrix3());
    return Cesium.Matrix3.multiplyByVector(m, new Cesium.Cartesian3(x, y, z), new Cesium.Cartesian3());
  }

  // The CZML orbit itself spans a padded window (±3min or more, so Orekit has enough
  // HK samples to interpolate) — much wider than the real computed footprint window
  // (line_start~line_end). Without this, the FOV/current-line would sit there for the
  // whole padded window instead of only appearing once playback actually enters the
  // real capture period. null (no footprint computed yet, e.g. plain orbit/live
  // tracking) means "no gating" — show the FOV as before.
  function _fovShowNow(time) {
    if (!_footprintTimeRange) return true;
    var ms = Cesium.JulianDate.toDate(time).getTime();
    return ms >= _footprintTimeRange.min && ms <= _footprintTimeRange.max;
  }

  function _doSetFootprintLines(lines) {
    var viewer = window.hkCesium.viewer;
    if (!viewer) return;
    if (_footprintLineEntity) { viewer.entities.remove(_footprintLineEntity); _footprintLineEntity = null; }
    Object.keys(_bandLineEntities).forEach(function(band) {
      viewer.entities.remove(_bandLineEntities[band]);
    });
    _bandLineEntities = {};
    _footprintLines = (lines && lines.length) ? lines : null;
    _footprintLineTimes = null;
    _footprintTimeRange = null;
    if (!_footprintLines) return;

    // Index-aligned with _footprintLines (no filtering) so _interpolateFootprintLine
    // can bracket by index; times are already ascending (server returns them sorted).
    _footprintLineTimes = _footprintLines.map(function(l) { return Date.parse(l.t); });
    var validTimes = _footprintLineTimes.filter(function(t) { return !isNaN(t); });
    if (validTimes.length) {
      _footprintTimeRange = { min: Math.min.apply(null, validTimes), max: Math.max.apply(null, validTimes) };
    }

    // PAN / reference band — uses the actual Rugged-computed left/right altitude
    // (la/ra) for height instead of clampToGround — more accurate than the FOV
    // pyramid's plain ellipsoid intersection, since it reflects the real DEM-derived
    // ground point.
    _footprintLineEntity = viewer.entities.add({
      show: true,
      polyline: {
        show: new Cesium.CallbackProperty(function(time) {
          return _fovShowNow(time) && _bandVisibility.PAN;
        }, false),
        positions: new Cesium.CallbackProperty(function(time) {
          var d = Cesium.JulianDate.toDate(time);
          var line = _interpolateFootprintLine(_footprintLines, _footprintLineTimes, d.getTime());
          if (!line) return null;
          return [
            Cesium.Cartesian3.fromDegrees(line.ll[1], line.ll[0], line.la || 0),
            Cesium.Cartesian3.fromDegrees(line.rl[1], line.rl[0], line.ra || 0),
          ];
        }, false),
        width: 3,
        material: new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString('#ff4081')),
      },
    });

    // Other bands — the same computed swath, just looked up at (now + Δt_band)
    // instead of now, per the focal-plane row-offset math above. Thinner/dimmer than
    // the PAN reference line so it stays legible when several overlap near-zero offset.
    var satId = window.APP_SATELLITE || 'O1A';
    var rows = BAND_START_ROW[satId] || {};
    DISPLAY_BANDS.forEach(function(band) {
      if (band === REFERENCE_BAND || rows[band] === undefined) return;
      _bandLineEntities[band] = viewer.entities.add({
        show: true,
        polyline: {
          show: new Cesium.CallbackProperty(function(time) {
            return _fovShowNow(time) && _bandVisibility[band];
          }, false),
          positions: new Cesium.CallbackProperty(function(time) {
            var pos = _activeSatEntity && _activeSatEntity.position && _activeSatEntity.position.getValue(time);
            if (!pos) return null;
            var altM = Cesium.Cartesian3.magnitude(pos) - 6378137.0;
            var dtSec = _bandTimeOffsetSec(satId, band, altM);
            if (dtSec === null) return null;
            var shiftedMs = Cesium.JulianDate.toDate(time).getTime() + dtSec * 1000;
            var line = _interpolateFootprintLine(_footprintLines, _footprintLineTimes, shiftedMs);
            if (!line) return null;
            return [
              Cesium.Cartesian3.fromDegrees(line.ll[1], line.ll[0], line.la || 0),
              Cesium.Cartesian3.fromDegrees(line.rl[1], line.rl[0], line.ra || 0),
            ];
          }, false),
          width: 2,
          material: new Cesium.ColorMaterialProperty(
            Cesium.Color.fromCssColorString(BAND_COLOR[band] || '#ffffff').withAlpha(0.75)
          ),
        },
      });
    });
  }

  function _doFlyTo(lat, lon, heightMeters) {
    var viewer = window.hkCesium.viewer;
    if (!viewer) return;
    viewer.trackedEntity = null;
    viewer.camera.flyTo({
      destination: Cesium.Cartesian3.fromDegrees(lon, lat, heightMeters || 2000000),
      orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
      duration: 1.5,
    });
  }

  function _doSetTarget(lat, lon, name) {
    var viewer = window.hkCesium.viewer;
    if (!viewer) return;
    var entity = viewer.entities.getById('target-marker');
    if (!entity) {
      // Created lazily on first real AOI/mission pick — no default (e.g. Paju) marker
      // is shown until the user actually selects something.
      entity = viewer.entities.add({
        id: 'target-marker',
        point: {
          color: Cesium.Color.fromCssColorString('#FFC800'),
          pixelSize: 12,
          outlineColor: Cesium.Color.WHITE,
          outlineWidth: 2,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        },
        label: {
          font: 'bold 12pt "Segoe UI", system-ui, sans-serif',
          style: Cesium.LabelStyle.FILL_AND_OUTLINE,
          fillColor: Cesium.Color.WHITE,
          outlineColor: Cesium.Color.BLACK,
          outlineWidth: 2,
          verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
          pixelOffset: new Cesium.Cartesian2(0, -16),
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        },
      });
    }
    entity.position = Cesium.Cartesian3.fromDegrees(lon, lat, 0);
    if (entity.label) entity.label.text = name || 'TARGET';
  }

  window.hkCesium = {
    viewer: null,
    // viewTarget: optional {lat, lon} — recenters the camera there instead of the
    // hardcoded Korea view, so picking a global AOI/mission also moves the 3D globe.
    updateOrbit: function(payload, viewTarget){
      if (window.hkCesium.viewer) {
        _doUpdateOrbit(payload, viewTarget);
      } else {
        _pendingOrbit = payload;
        _pendingOrbitView = viewTarget || null;
      }
    },
    // Pan/zoom the 3D globe to a location immediately (before any orbit data loads).
    flyTo: function(lat, lon, heightMeters) {
      if (window.hkCesium.viewer) {
        _doFlyTo(lat, lon, heightMeters);
      } else {
        _pendingFlyTo = { lat: lat, lon: lon, height: heightMeters };
      }
    },
    // Move the standalone yellow target marker — independent of any CZML/orbit reload.
    setTarget: function(lat, lon, name) {
      if (window.hkCesium.viewer) {
        _doSetTarget(lat, lon, name);
      } else {
        _pendingTarget = { lat: lat, lon: lon, name: name };
      }
    },
    // Show the real-time TLE-propagated position/orbit for a satellite (O1A/O1B) —
    // the default view before any historical mission CZML has been loaded. Reassigned
    // to the real implementation once the viewer exists (see _buildViewer below).
    startLiveTracking: function(satId) {
      _pendingLiveSatId = satId;
    },
    setFovAngle: function(deg) {
      var v = parseFloat(deg);
      if (!isNaN(v)) { _fovAngleDeg = v; }
    },
    setFovVisible: function(visible) {},
    // lines: the `lines` array from /api/footprint/compute — draws a polyline between
    // each sample's left/right ground point, synced to the clock (see _doSetFootprintLines
    // above), so the FOV shows which computed scan line is "current" as playback runs.
    setFootprintLines: function(lines) {
      if (window.hkCesium.viewer) {
        _doSetFootprintLines(lines);
      } else {
        _pendingFootprintLines = lines;
      }
    },
    // sidebar.js의 EOC 마운팅 보정 체크박스와 연동 — off면 FOV도 순수 body +Z로 보임.
    setEocCorrectionEnabled: function(enabled) {
      _eocCorrectionEnabled = !!enabled;
    }
  };

  function createViewer(container, token) {
    if (token) {
      Cesium.Ion.defaultAccessToken = token;
    }
    return new Cesium.Viewer(container, {
      timeline: false,
      animation: false,
      geocoder: false,
      homeButton: false,
      sceneModePicker: false,
      baseLayerPicker: false,
      navigationHelpButton: false,
      infoBox: false,
      selectionIndicator: false,
      fullscreenButton: false,
      shouldAnimate: false,
    });
  }

  function initCesium(){
    if (!window.Cesium) {
      setTimeout(initCesium, 100);
      return;
    }
    var container = document.getElementById('cesium-container');
    if (!container) {
      setTimeout(initCesium, 100);
      return;
    }
    if (window.hkCesium.viewer) return;

    fetch((window.APP_BASE_PATH || '') + '/cesium-token')
      .then(function(r){ return r.json(); })
      .then(function(data){ _buildViewer(container, data.token || ''); })
      .catch(function(){ _buildViewer(container, ''); });

    // FOV 계산이 서버(FootprintCalculator.java)와 같은 EOC 마운팅 보정 벡터를 쓰도록 —
    // 뷰어 생성과 독립적으로 로드하고, 늦게 도착해도 _effectiveEocVector()는 이후
    // CallbackProperty 호출 시점에 최신값을 읽으므로 경쟁 조건 없음.
    fetch((window.APP_BASE_PATH || '') + '/api/sensor-calibration/' + encodeURIComponent(window.APP_SATELLITE || 'O1A'))
      .then(function(r){ return r.json(); })
      .then(function(data){
        if (data && Array.isArray(data.eoc_misalignment_unit_vector)) {
          _eocVectorRaw = data.eoc_misalignment_unit_vector;
        }
      })
      .catch(function(){});
  }

  function _buildViewer(container, token) {
    var viewer = createViewer(container, token);

    viewer.scene.globe.enableLighting = false;
    viewer.scene.skyAtmosphere.show = true;
    viewer.scene.globe.depthTestAgainstTerrain = false;
    viewer.scene.globe.show = true;
    viewer.scene.backgroundColor = Cesium.Color.BLACK;
    viewer.scene.screenSpaceCameraController.minimumZoomDistance = 1000.0;

    viewer.camera.setView({
      destination: Cesium.Cartesian3.fromDegrees(128.0, 36.0, 18000000),
      orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
    });

    // Target marker (yellow pin) is created lazily by setTarget() the first time the
    // sidebar picks a real AOI/mission — no default location is shown on load.

    function forceResize(){
      viewer.canvas.width  = container.clientWidth;
      viewer.canvas.height = container.clientHeight;
      viewer.resize();
      viewer.scene.requestRender();
    }
    setTimeout(forceResize, 50);
    setTimeout(forceResize, 300);
    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(function(){ forceResize(); }).observe(container);
    }

    var czmlSource = null;
    var satEntity = null;
    var startTime = null;
    var stopTime = null;
    var playing = true;
    var progress = 0;
    var speedIndex = 4;
    var speeds = [0.1, 0.5, 1, 5, 10, 20, 50, 100];
    var lastTs = null;
    var live = null; // {satId, satEntity, orbitEntity, refreshTimer} while showing a real-time TLE track

    function setText(id, text){
      var el = document.getElementById(id);
      if (el) el.textContent = text;
    }

    function updateSpeedDisplay(){
      var v = speeds[speedIndex];
      var el = document.getElementById('speed-val');
      if (el) el.textContent = (v < 1 ? v.toFixed(1) : v) + 'x';
    }

    function updateScrubber(){
      var fill = document.getElementById('pb-fill');
      var thumb = document.getElementById('pb-thumb');
      if (fill) fill.style.width = (progress * 100).toFixed(2) + '%';
      if (thumb) thumb.style.left = (progress * 100).toFixed(2) + '%';
    }

    function setProgress(p){
      progress = Math.max(0, Math.min(1, p));
      updateScrubber();
      if (startTime && stopTime) {
        var total = Cesium.JulianDate.secondsDifference(stopTime, startTime);
        var cur = Cesium.JulianDate.addSeconds(startTime, total * progress, new Cesium.JulianDate());
        viewer.clock.currentTime = cur;
      }
    }

    function formatUTC(d){
      var pad = function(n){ return String(n).padStart(2,'0'); };
      return d.getUTCFullYear() + '-' + pad(d.getUTCMonth()+1) + '-' + pad(d.getUTCDate()) + '  ' +
        pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds()) + ' UTC';
    }

    function updateSatAtCurrentTime(){
      if (!satEntity) return;
      var pos = satEntity.position.getValue(viewer.clock.currentTime);
      if (!pos) return;
      var r = Math.sqrt(pos.x*pos.x + pos.y*pos.y + pos.z*pos.z);
      var altKm = (r - 6378137.0) / 1000.0;
      setText('alt-display', 'ALT  ' + altKm.toFixed(1) + ' km');
    }

    function updateAttitudeAtCurrentTime(){
      if (!satEntity || !satEntity.orientation || !satEntity.position) return;
      var time = viewer.clock.currentTime;
      var orientation = satEntity.orientation.getValue(time, new Cesium.Quaternion());
      var position = satEntity.position.getValue(time, new Cesium.Cartesian3());
      if (!orientation || !position) return;

      // Express the body attitude relative to the local orbital (nadir) frame.
      var before = Cesium.JulianDate.addSeconds(time, -1, new Cesium.JulianDate());
      var after = Cesium.JulianDate.addSeconds(time, 1, new Cesium.JulianDate());
      var previousPosition = satEntity.position.getValue(before, new Cesium.Cartesian3());
      var nextPosition = satEntity.position.getValue(after, new Cesium.Cartesian3());
      if (!previousPosition || !nextPosition) return;

      var radial = Cesium.Cartesian3.normalize(position, new Cesium.Cartesian3());
      var velocity = Cesium.Cartesian3.subtract(nextPosition, previousPosition, new Cesium.Cartesian3());
      var radialVelocity = Cesium.Cartesian3.multiplyByScalar(
        radial,
        Cesium.Cartesian3.dot(velocity, radial),
        new Cesium.Cartesian3()
      );
      var alongTrack = Cesium.Cartesian3.normalize(
        Cesium.Cartesian3.subtract(velocity, radialVelocity, new Cesium.Cartesian3()),
        new Cesium.Cartesian3()
      );
      var nadir = Cesium.Cartesian3.negate(radial, new Cesium.Cartesian3());
      var crossTrack = Cesium.Cartesian3.normalize(
        Cesium.Cartesian3.cross(nadir, alongTrack, new Cesium.Cartesian3()),
        new Cesium.Cartesian3()
      );

      var bodyToEci = Cesium.Matrix3.fromQuaternion(orientation, new Cesium.Matrix3());
      var bodyX = new Cesium.Cartesian3(bodyToEci[0], bodyToEci[1], bodyToEci[2]);
      var bodyY = new Cesium.Cartesian3(bodyToEci[3], bodyToEci[4], bodyToEci[5]);
      var bodyZ = new Cesium.Cartesian3(bodyToEci[6], bodyToEci[7], bodyToEci[8]);
      var relative = [
        Cesium.Cartesian3.dot(alongTrack, bodyX), Cesium.Cartesian3.dot(alongTrack, bodyY), Cesium.Cartesian3.dot(alongTrack, bodyZ),
        Cesium.Cartesian3.dot(crossTrack, bodyX), Cesium.Cartesian3.dot(crossTrack, bodyY), Cesium.Cartesian3.dot(crossTrack, bodyZ),
        Cesium.Cartesian3.dot(nadir, bodyX), Cesium.Cartesian3.dot(nadir, bodyY), Cesium.Cartesian3.dot(nadir, bodyZ)
      ];
      var pitch = Math.asin(Math.max(-1, Math.min(1, -relative[6])));
      var roll = Math.atan2(relative[7], relative[8]);
      var yaw = Math.atan2(relative[3], relative[0]);
      var formatAngle = function(angle){
        return Cesium.Math.toDegrees(angle).toFixed(1) + '°';
      };
      setText('roll-display', 'ROLL  ' + formatAngle(roll));
      setText('pitch-display', 'PITCH  ' + formatAngle(pitch));
      setText('yaw-display', 'YAW  ' + formatAngle(yaw));
    }

    function tick(ts){
      requestAnimationFrame(tick);
      if (live) {
        var now = new Date();
        setText('utc-clock', formatUTC(now));
        setText('pb-time-label', 'LIVE — ' + live.satId);
        var fillEl = document.getElementById('pb-fill');
        var thumbEl = document.getElementById('pb-thumb');
        if (fillEl) fillEl.style.width = '0%';
        if (thumbEl) thumbEl.style.left = '0%';
        var pos = live.satEntity.position.getValue(Cesium.JulianDate.now());
        if (pos) {
          var r = Math.sqrt(pos.x*pos.x + pos.y*pos.y + pos.z*pos.z);
          var altKm = (r - 6378137.0) / 1000.0;
          setText('alt-display', 'ALT  ' + altKm.toFixed(1) + ' km');
        }
        return;
      }
      if (!startTime || !stopTime) return;
      if (!lastTs) lastTs = ts;
      lastTs = ts;
      if (playing) {
        viewer.clock.multiplier = speeds[speedIndex];
        viewer.clock.shouldAnimate = true;
      } else {
        viewer.clock.shouldAnimate = false;
      }
      var total = Cesium.JulianDate.secondsDifference(stopTime, startTime);
      var current = Cesium.JulianDate.secondsDifference(viewer.clock.currentTime, startTime);
      var p = total > 0 ? (current / total) : 0;
      progress = Math.max(0, Math.min(1, p));
      updateScrubber();
      setText('utc-clock', formatUTC(Cesium.JulianDate.toDate(viewer.clock.currentTime)));
      var pbStart = formatUTC(Cesium.JulianDate.toDate(startTime));
      var pbEnd   = formatUTC(Cesium.JulianDate.toDate(stopTime)).slice(11);
      setText('pb-time-label', pbStart + ' — ' + pbEnd);
      updateSatAtCurrentTime();
      updateAttitudeAtCurrentTime();
      if (window.map2d) {
        window.map2d.setTime(Cesium.JulianDate.toDate(viewer.clock.currentTime));
      }
    }

    // ── Ray-ellipsoid intersection ──
    // WGS84 (equatorial radius 6378137m, polar radius ~6356752m) — a sphere puts the
    // ground point up to ~21km off at high latitudes; the real footprint pipeline
    // (Rugged) intersects the actual DEM against this same ellipsoid, so matching the
    // ellipsoid here (still no terrain) is the correct first-order fix for this pyramid.
    function _rayEarthIntersect(pos, dir) {
      var ray = new Cesium.Ray(pos, dir);
      var ellipsoid = (viewer.scene.globe && viewer.scene.globe.ellipsoid) || Cesium.Ellipsoid.WGS84;
      var interval = Cesium.IntersectionTests.rayEllipsoid(ray, ellipsoid);
      if (!interval || interval.start < 0.0) return null;
      return Cesium.Ray.getPoint(ray, interval.start);
    }

    // ── FOV footprint ──
    // Removes any FOV entities from a previous call — shared by _createFovFootprint
    // and every place that needs to clear the FOV display (CZML unload/error/reload).
    function _removeFovEntities() {
      if (_fovEntity)       { viewer.entities.remove(_fovEntity);       _fovEntity       = null; }
      if (_fovCenterEntity) { viewer.entities.remove(_fovCenterEntity); _fovCenterEntity = null; }
      if (_zAxisEntity)     { viewer.entities.remove(_zAxisEntity);     _zAxisEntity     = null; }
      _fovLegEntities.forEach(function(e){ if (e) viewer.entities.remove(e); });
      _fovLegEntities = [];
    }

    function _createFovFootprint(satEntity) {
      _removeFovEntities();

      function bodyToEci(bx, by, bz, q) {
        var m = Cesium.Matrix3.fromQuaternion(q, new Cesium.Matrix3());
        return new Cesium.Cartesian3(
          m[0] * bx + m[3] * by + m[6] * bz,
          m[1] * bx + m[4] * by + m[7] * bz,
          m[2] * bx + m[5] * by + m[8] * bz
        );
      }

      function eciDirToEcef(eciDir, time) {
        var m = Cesium.Transforms.computeIcrfToFixedMatrix(time, new Cesium.Matrix3());
        if (!m) return null;
        return Cesium.Cartesian3.normalize(
          Cesium.Matrix3.multiplyByVector(m, eciDir, new Cesium.Cartesian3()),
          new Cesium.Cartesian3()
        );
      }

      // The real sensor FOV is a rectangular (square) pyramid, not a circular cone —
      // nominal boresight is body +Z (O1A/O1B nadir convention), with the configured
      // angle as the full across-track width applied symmetrically to both body axes.
      // The whole fan is then rotated by the same EOC misalignment used server-side
      // (identity for O1A / when the toggle is off), so the 4 corner rays form a
      // square-based pyramid around the *actual* calibrated boresight, not the nominal one.
      function cornerDirsBody(effectiveAngleDeg) {
        var thetaRad = Cesium.Math.toRadians(effectiveAngleDeg / 2.0);
        var offset = Math.tan(thetaRad);
        var norm = Math.sqrt(offset * offset + offset * offset + 1);
        var raw = [
          [ offset / norm,  offset / norm, 1 / norm],
          [ offset / norm, -offset / norm, 1 / norm],
          [-offset / norm, -offset / norm, 1 / norm],
          [-offset / norm,  offset / norm, 1 / norm],
        ];
        var eocQuat = _eocRotationQuaternion(_effectiveEocVector());
        return raw.map(function(d) {
          var r = _rotateBodyDir(d[0], d[1], d[2], eocQuat);
          return [r.x, r.y, r.z];
        });
      }

      // Satellite position + the 4 ground intersection points for the current time,
      // or null if the geometry isn't valid right now (no pos/orientation, FOV wider
      // than the Earth limb, or a corner ray missing the Earth entirely).
      function cornerGroundPoints(time) {
        var pos = satEntity.position.getValue(time, new Cesium.Cartesian3());
        var ori = satEntity.orientation.getValue(time, new Cesium.Quaternion());
        if (!pos || !ori) return null;

        var R = 6378137.0;
        var altM = Math.max(0.0, Cesium.Cartesian3.magnitude(pos) - R);
        var limbAngleDeg = Cesium.Math.toDegrees(Math.acos(R / (R + altM)));
        var effectiveAngleDeg = Math.min(_fovAngleDeg, limbAngleDeg - 0.5);
        if (effectiveAngleDeg <= 0) return null;

        // Real DEM altitude for the "front"/"back" corner pairs, borrowed from the
        // already-computed footprint at the matching along-track-shifted instant (see
        // _fovAlongTrackTimeOffsetSec) — null when no footprint has been computed yet,
        // in which case corners just keep their flat-ellipsoid height as before.
        var nowMs = Cesium.JulianDate.toDate(time).getTime();
        var dtHalf = _fovAlongTrackTimeOffsetSec(effectiveAngleDeg, altM);
        var frontLine = _interpolateFootprintLine(_footprintLines, _footprintLineTimes, nowMs + dtHalf * 1000);
        var backLine  = _interpolateFootprintLine(_footprintLines, _footprintLineTimes, nowMs - dtHalf * 1000);
        var frontH = frontLine ? (frontLine.la + frontLine.ra) / 2 : null;
        var backH  = backLine  ? (backLine.la  + backLine.ra)  / 2 : null;

        var corners = cornerDirsBody(effectiveAngleDeg);
        var hits = [];
        for (var i = 0; i < corners.length; i++) {
          var c = corners[i];
          var dirEci = bodyToEci(c[0], c[1], c[2], ori);
          var dirEcef = eciDirToEcef(dirEci, time);
          if (!dirEcef) return null;
          var hit = _rayEarthIntersect(pos, dirEcef);
          if (!hit) return null;
          var demH = c[0] >= 0 ? frontH : backH;
          if (demH !== null) {
            var carto = Cesium.Cartographic.fromCartesian(hit);
            hit = Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, demH);
          }
          hits.push(hit);
        }
        return { pos: pos, hits: hits };
      }

      // Base: the quadrilateral where the 4 pyramid edges meet the ground.
      _fovEntity = viewer.entities.add({
        show: true,
        polyline: {
          positions: new Cesium.CallbackProperty(function(time) {
            var g = cornerGroundPoints(time);
            if (!g) return null;
            return [g.hits[0], g.hits[1], g.hits[2], g.hits[3], g.hits[0]];
          }, false),
          width: 2,
          material: new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString('#FF00FF')),
          clampToGround: true,
        },
      });

      // Legs: satellite (apex) down to each of the 4 base corners, so the pyramid
      // shape itself is visible in 3D, not just its footprint on the ground.
      _fovLegEntities = [0, 1, 2, 3].map(function(i) {
        return viewer.entities.add({
          show: true,
          polyline: {
            positions: new Cesium.CallbackProperty(function(time) {
              var g = cornerGroundPoints(time);
              if (!g) return null;
              return [g.pos, g.hits[i]];
            }, false),
            width: 1,
            material: new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString('#FF00FF').withAlpha(0.3)),
          },
        });
      });

      _fovCenterEntity = viewer.entities.add({
        show: true,
        position: new Cesium.CallbackProperty(function(time) {
          var pos = satEntity.position.getValue(time, new Cesium.Cartesian3());
          var ori = satEntity.orientation.getValue(time, new Cesium.Quaternion());
          if (!pos || !ori) return undefined;
          var eocQuat = _eocRotationQuaternion(_effectiveEocVector());
          var centerBody = _rotateBodyDir(0, 0, 1, eocQuat);
          var dirEci  = bodyToEci(centerBody.x, centerBody.y, centerBody.z, ori);
          var dirEcef = eciDirToEcef(dirEci, time);
          if (!dirEcef) return undefined;
          var hit = _rayEarthIntersect(pos, dirEcef);
          if (!hit) return undefined;
          var nowMs = Cesium.JulianDate.toDate(time).getTime();
          var centerLine = _interpolateFootprintLine(_footprintLines, _footprintLineTimes, nowMs);
          if (centerLine) {
            var carto = Cesium.Cartographic.fromCartesian(hit);
            hit = Cesium.Cartesian3.fromRadians(carto.longitude, carto.latitude, (centerLine.la + centerLine.ra) / 2);
          }
          return hit;
        }, false),
        point: {
          pixelSize: 5,
          color: Cesium.Color.fromCssColorString('#FF00FF'),
          outlineColor: Cesium.Color.WHITE.withAlpha(0.8),
          outlineWidth: 1,
          heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
        },
      });

      // Body +Z boresight line (500km, replaces czml_generator.py's old baked
      // axis_z) — computed live every frame from the same EOC-corrected direction
      // as _fovCenterEntity above, so this line and the FOV center point can never
      // drift apart the way a server-baked, uncorrected axis would for O1B.
      _zAxisEntity = viewer.entities.add({
        show: true,
        polyline: {
          positions: new Cesium.CallbackProperty(function(time) {
            var pos = satEntity.position.getValue(time, new Cesium.Cartesian3());
            var ori = satEntity.orientation.getValue(time, new Cesium.Quaternion());
            if (!pos || !ori) return null;
            var eocQuat = _eocRotationQuaternion(_effectiveEocVector());
            var centerBody = _rotateBodyDir(0, 0, 1, eocQuat);
            var dirEci  = bodyToEci(centerBody.x, centerBody.y, centerBody.z, ori);
            var dirEcef = eciDirToEcef(dirEci, time);
            if (!dirEcef) return null;
            // O1A/O1B's real altitude (~440km) is *less* than the fixed 500km arrow
            // length, so a fixed-length nadir-pointing line overshoots the ground by
            // ~60km and appears to plunge through the globe. Clamp to the actual
            // ground-intersection distance (same ray _fovCenterEntity uses) when the
            // boresight actually hits the Earth; otherwise keep the fixed 500km so the
            // line still shows something when it points away from the Earth.
            var maxLen = 500000.0;
            var groundHit = _rayEarthIntersect(pos, dirEcef);
            var len = groundHit ? Math.min(maxLen, Cesium.Cartesian3.distance(pos, groundHit)) : maxLen;
            var offset = Cesium.Cartesian3.multiplyByScalar(dirEcef, len, new Cesium.Cartesian3());
            var tip = Cesium.Cartesian3.add(pos, offset, new Cesium.Cartesian3());
            return [pos, tip];
          }, false),
          width: 10,
          arcType: Cesium.ArcType.NONE,
          material: new Cesium.PolylineArrowMaterialProperty(Cesium.Color.fromCssColorString('#3232FF')),
        },
      });
    }

    // ── FOV overlay UI ──
    function _ensureFovOverlay() {
      if (document.getElementById('fov-overlay')) return;
      var viewport = document.querySelector('.cesium-viewport');
      if (!viewport) {
        if (!_fovOverlayRetried) {
          _fovOverlayRetried = true;
          setTimeout(_ensureFovOverlay, 500);
        }
        return;
      }

      var panel = document.createElement('div');
      panel.id = 'fov-overlay';
      panel.style.display = 'none';

      var attitude = document.createElement('div');
      attitude.className = 'attitude-readout';
      attitude.innerHTML =
        '<span class="attitude-value roll" id="roll-display">ROLL  --</span>' +
        '<span class="attitude-value pitch" id="pitch-display">PITCH  --</span>' +
        '<span class="attitude-value yaw" id="yaw-display">YAW  --</span>';
      panel.appendChild(attitude);

      var label = document.createElement('div');
      label.className = 'fov-label';
      label.textContent = 'FOV  maxViewAngle';
      panel.appendChild(label);

      var sliderRow = document.createElement('div');
      sliderRow.className = 'fov-slider-row';

      var slider = document.createElement('input');
      slider.id = 'fov-slider';
      slider.type = 'range';
      slider.min = '0.1'; slider.max = '10'; slider.step = '0.1'; slider.value = '1.6';

      var display = document.createElement('span');
      display.id = 'fov-angle-display';
      display.textContent = '1.6°';

      sliderRow.appendChild(slider);
      sliderRow.appendChild(display);
      panel.appendChild(sliderRow);

      var toggleRow = document.createElement('div');
      toggleRow.className = 'fov-toggle-row';

      var btnOn = document.createElement('button');
      btnOn.id = 'fov-btn-on';
      btnOn.className = 'fov-btn active';
      btnOn.type = 'button';
      btnOn.textContent = 'ON';

      var btnOff = document.createElement('button');
      btnOff.id = 'fov-btn-off';
      btnOff.className = 'fov-btn';
      btnOff.type = 'button';
      btnOff.textContent = 'OFF';

      toggleRow.appendChild(btnOn);
      toggleRow.appendChild(btnOff);
      panel.appendChild(toggleRow);

      // ── Per-band visibility ──
      var bandLabel = document.createElement('div');
      bandLabel.className = 'fov-label';
      bandLabel.textContent = 'BANDS';
      panel.appendChild(bandLabel);

      var bandRow = document.createElement('div');
      bandRow.className = 'band-toggle-row';
      DISPLAY_BANDS.forEach(function(band) {
        var item = document.createElement('label');
        item.className = 'band-toggle-item';

        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = _bandVisibility[band] !== false;
        cb.addEventListener('change', function() {
          _bandVisibility[band] = cb.checked;
          viewer.scene.requestRender();
        });

        var swatch = document.createElement('span');
        swatch.className = 'band-swatch';
        swatch.style.background = BAND_COLOR[band] || '#ffffff';

        var text = document.createElement('span');
        text.textContent = band;

        item.appendChild(cb);
        item.appendChild(swatch);
        item.appendChild(text);
        bandRow.appendChild(item);
      });
      panel.appendChild(bandRow);

      viewport.appendChild(panel);

      slider.addEventListener('input', function() {
        var v = parseFloat(this.value);
        if (isNaN(v)) return;
        display.textContent = v.toFixed(1) + '°';
        window.hkCesium.setFovAngle(v);
        viewer.scene.requestRender();
      });
      btnOn.addEventListener('click', function() {
        btnOn.classList.add('active');
        btnOff.classList.remove('active');
        window.hkCesium.setFovVisible(true);
      });
      btnOff.addEventListener('click', function() {
        btnOff.classList.add('active');
        btnOn.classList.remove('active');
        window.hkCesium.setFovVisible(false);
      });
    }

    function _showFovOverlay(visible) {
      var el = document.getElementById('fov-overlay');
      if (!el) return;
      el.style.display = visible ? 'block' : 'none';
      if (visible) {
        var slider = document.getElementById('fov-slider');
        if (slider) { slider.value = '1.6'; }
        var disp = document.getElementById('fov-angle-display');
        if (disp) { disp.textContent = '1.6°'; }
        window.hkCesium.setFovAngle(1.6);
        var btnOn  = document.getElementById('fov-btn-on');
        var btnOff = document.getElementById('fov-btn-off');
        if (btnOn)  { btnOn.classList.add('active'); }
        if (btnOff) { btnOff.classList.remove('active'); }
      }
    }

    function ensureOverlay(){
      var el = document.getElementById('cesium-overlay');
      if (!el) {
        el = document.createElement('div');
        el.id = 'cesium-overlay';
        el.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#DDD;font-size:12px;pointer-events:none;';
        container.appendChild(el);
      }
      return el;
    }

    // ── Real-time TLE tracking (default state — no historical mission picked yet) ──
    function _liveOrbitPositions(satrec, fromDate){
      var periodMin = satrec.no ? (2 * Math.PI / satrec.no) : 95;
      var steps = 180;
      var positions = [];
      for (var i = 0; i <= steps; i++) {
        var t = new Date(fromDate.getTime() + (periodMin * 60000 * i) / steps);
        var pv = satellite.propagate(satrec, t);
        if (!pv || !pv.position) continue;
        var gmst = satellite.gstime(t);
        var geo = satellite.eciToGeodetic(pv.position, gmst);
        positions.push(Cesium.Cartesian3.fromRadians(geo.longitude, geo.latitude, Math.max(0, geo.height * 1000)));
      }
      return positions;
    }

    function _stopLive(){
      if (!live) return;
      if (live.refreshTimer) clearInterval(live.refreshTimer);
      if (live.satEntity)   viewer.entities.remove(live.satEntity);
      if (live.orbitEntity) viewer.entities.remove(live.orbitEntity);
      live = null;
    }

    function _startLive(satId){
      _stopLive();
      if (!satId || typeof satellite === 'undefined') return;
      var overlay = ensureOverlay();
      overlay.textContent = satId + ' 실시간 궤도 로딩 중...';

      fetch((window.APP_BASE_PATH || '') + '/api/tle/' + encodeURIComponent(satId))
        .then(function(r){ return r.json(); })
        .then(function(data){
          if (data.error || !data.currentTle) throw new Error(data.error || 'TLE 없음');
          var lines = String(data.currentTle).trim().split(/\r?\n/).filter(Boolean);
          if (lines.length < 2) throw new Error('TLE 파싱 실패');
          var satrec = satellite.twoline2satrec(lines[lines.length - 2], lines[lines.length - 1]);
          if (!satrec) throw new Error('TLE 파싱 실패');

          var color = Cesium.Color.fromCssColorString(satId === 'O1B' ? '#00e5ff' : '#a84bff');

          var satEntityLive = viewer.entities.add({
            id: 'live-satellite',
            position: new Cesium.CallbackProperty(function(){
              var t = new Date();
              var pv = satellite.propagate(satrec, t);
              if (!pv || !pv.position) return undefined;
              var gmst = satellite.gstime(t);
              var geo = satellite.eciToGeodetic(pv.position, gmst);
              return Cesium.Cartesian3.fromRadians(geo.longitude, geo.latitude, Math.max(0, geo.height * 1000));
            }, false),
            point: { pixelSize: 10, color: color, outlineColor: Cesium.Color.WHITE, outlineWidth: 1.5 },
            label: {
              text: satId,
              font: 'bold 12pt "Segoe UI", system-ui, sans-serif',
              style: Cesium.LabelStyle.FILL_AND_OUTLINE,
              fillColor: Cesium.Color.WHITE, outlineColor: Cesium.Color.BLACK, outlineWidth: 2,
              verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
              pixelOffset: new Cesium.Cartesian2(0, -14),
            },
          });

          var orbitEntityLive = viewer.entities.add({
            id: 'live-orbit',
            polyline: {
              positions: _liveOrbitPositions(satrec, new Date()),
              width: 1.5,
              material: color.withAlpha(0.5),
            },
          });

          var refreshTimer = setInterval(function(){
            orbitEntityLive.polyline.positions = _liveOrbitPositions(satrec, new Date());
          }, 5 * 60 * 1000);

          live = { satId: satId, satEntity: satEntityLive, orbitEntity: orbitEntityLive, refreshTimer: refreshTimer };
          overlay.textContent = '';
        })
        .catch(function(err){
          console.error(err);
          overlay.textContent = '실시간 궤도 로딩 실패: ' + err.message;
        });
    }

    function _doUpdateOrbit(payload, viewTarget){
      var overlay = ensureOverlay();
      if (!payload || !payload.czml || payload.czml.length === 0) {
        if (czmlSource) viewer.dataSources.remove(czmlSource);
        czmlSource = null; satEntity = null; _activeSatEntity = null; startTime = null; stopTime = null;
        _removeFovEntities();
        _showFovOverlay(false);
        // Fall back to the real-time TLE track instead of leaving a blank globe.
        _startLive(window.APP_SATELLITE);
        if (payload && payload.error) { setText('status-msg', payload.error); }
        return;
      }
      overlay.textContent = '';
      _stopLive();
      if (czmlSource) viewer.dataSources.remove(czmlSource);
      czmlSource = new Cesium.CzmlDataSource();
      czmlSource.load(payload.czml).then(function(ds){
        viewer.dataSources.add(ds);
        var sat = ds.entities.getById('satellite');
        satEntity = sat || null;
        _activeSatEntity = satEntity;
        if (!satEntity) {
          overlay.textContent = 'CZML loaded but no satellite entity';
        }
        if (satEntity) {
          _createFovFootprint(satEntity);
          _showFovOverlay(true);
        } else {
          _removeFovEntities();
          _showFovOverlay(false);
        }
        if (ds.clock) {
          startTime = ds.clock.startTime;
          stopTime = ds.clock.stopTime;
          viewer.clock.startTime = startTime.clone();
          viewer.clock.stopTime = stopTime.clone();
          viewer.clock.currentTime = startTime.clone();
          viewer.clock.multiplier = speeds[speedIndex];
          viewer.clock.shouldAnimate = true;
        }
        viewer.trackedEntity = null;
        var vt = viewTarget || { lat: 40.0, lon: 127.0 };
        viewer.camera.setView({
          destination: Cesium.Cartesian3.fromDegrees(vt.lon, vt.lat, 5000000),
          orientation: { heading: 0, pitch: Cesium.Math.toRadians(-90), roll: 0 },
        });
        if (viewTarget) { _doSetTarget(viewTarget.lat, viewTarget.lon, viewTarget.name); }
        setText('status-msg', '');
      }).catch(function(err){
        overlay.textContent = 'CZML load error';
        console.error(err);
        _removeFovEntities();
        _showFovOverlay(false);
      });
    }

    window.hkCesium.viewer = viewer;
    window.hkCesium.updateOrbit = function(payload, viewTarget){ _doUpdateOrbit(payload, viewTarget); };
    window.hkCesium.startLiveTracking = function(satId){ _startLive(satId); };
    // Lets the sidebar hold the orbit still while a footprint compute is in flight,
    // so playback only starts once the result has actually arrived.
    window.hkCesium.setPlaying = function(shouldPlay) {
      playing = !!shouldPlay;
      var playBtn = document.getElementById('play-btn');
      if (playBtn) playBtn.textContent = playing ? '⏸' : '▶';
    };
    window.hkCesium.setFovVisible = function(visible) {
      if (_fovEntity)       { _fovEntity.show       = visible; }
      if (_fovCenterEntity) { _fovCenterEntity.show = visible; }
      if (_zAxisEntity)     { _zAxisEntity.show      = visible; }
      _fovLegEntities.forEach(function(e){ if (e) e.show = visible; });
    };

    if (_pendingOrbit !== null) {
      _doUpdateOrbit(_pendingOrbit, _pendingOrbitView);
      _pendingOrbit = null;
      _pendingOrbitView = null;
    }
    if (_pendingFlyTo !== null) {
      _doFlyTo(_pendingFlyTo.lat, _pendingFlyTo.lon, _pendingFlyTo.height);
      _pendingFlyTo = null;
    }
    if (_pendingTarget !== null) {
      _doSetTarget(_pendingTarget.lat, _pendingTarget.lon, _pendingTarget.name);
      _pendingTarget = null;
    }
    if (_pendingLiveSatId !== null) {
      _startLive(_pendingLiveSatId);
      _pendingLiveSatId = null;
    }
    if (_pendingFootprintLines !== null) {
      _doSetFootprintLines(_pendingFootprintLines);
      _pendingFootprintLines = null;
    }

    function wireControls(){
      var playBtn    = document.getElementById('play-btn');
      var speedDown  = document.getElementById('pb-speed-down');
      var speedUp    = document.getElementById('pb-speed-up');
      var firstBtn   = document.getElementById('pb-first');
      var backBtn    = document.getElementById('pb-backward');
      var forwardBtn = document.getElementById('pb-forward');
      var lastBtn    = document.getElementById('pb-last');
      var scrubber   = document.getElementById('scrubber');

      if (playBtn) {
        playBtn.addEventListener('click', function(){
          playing = !playing;
          playBtn.textContent = playing ? '⏸' : '▶';
        });
      }
      if (speedDown) speedDown.addEventListener('click', function(){ speedIndex = Math.max(0, speedIndex - 1); updateSpeedDisplay(); });
      if (speedUp)   speedUp.addEventListener('click', function(){ speedIndex = Math.min(speeds.length - 1, speedIndex + 1); updateSpeedDisplay(); });
      if (firstBtn)   firstBtn.addEventListener('click', function(){ setProgress(0); });
      if (lastBtn)    lastBtn.addEventListener('click', function(){ setProgress(1); });
      if (backBtn)    backBtn.addEventListener('click', function(){ setProgress(progress - 0.05); });
      if (forwardBtn) forwardBtn.addEventListener('click', function(){ setProgress(progress + 0.05); });
      if (scrubber) {
        scrubber.addEventListener('mousedown', function(e){
          var rect = scrubber.getBoundingClientRect();
          setProgress((e.clientX - rect.left) / rect.width);
          var move = function(ev){ setProgress((ev.clientX - rect.left) / rect.width); };
          var up = function(){ window.removeEventListener('mousemove', move); window.removeEventListener('mouseup', up); };
          window.addEventListener('mousemove', move);
          window.addEventListener('mouseup', up);
        });
      }
      updateSpeedDisplay();
    }

    _ensureFovOverlay();
    wireControls();
    requestAnimationFrame(tick);
  }

  // ── Load CZML from server ──
  // opts: {start, end, target_name, target_lat, target_lon} — all optional. start/end
  // switch the orbit window; target_* only recenters the camera + marker client-side
  // (the marker itself isn't part of the CZML — see api_czml on the server).
  window.loadCzml = function(opts) {
    var params = new URLSearchParams();
    opts = opts || {};
    if (opts.start) params.set('start', opts.start);
    if (opts.end) params.set('end', opts.end);
    params.set('satellite', opts.satellite || window.APP_SATELLITE || 'O1A');

    var qs = params.toString();
    var url = (window.APP_BASE_PATH || '') + '/api/czml' + (qs ? ('?' + qs) : '');
    var statusEl = document.getElementById('status-msg');
    if (statusEl) statusEl.textContent = 'Loading CZML...';

    var viewTarget = (opts.target_lat !== undefined && opts.target_lat !== null &&
                      opts.target_lon !== undefined && opts.target_lon !== null)
      ? { lat: opts.target_lat, lon: opts.target_lon, name: opts.target_name }
      : null;

    return fetch(url)
      .then(function(r){
        if (!r.ok) {
          // A server-side crash returns Flask's HTML error page, not JSON —
          // surface the HTTP status instead of letting r.json() throw an opaque
          // "Unexpected token '<'" parse error.
          return r.text().then(function(text){
            throw new Error('서버 오류 (HTTP ' + r.status + '): ' + text.slice(0, 200));
          });
        }
        return r.json();
      })
      .then(function(data){
        if (data.czml && data.czml.length) {
          window.hkCesium.updateOrbit(data, viewTarget);
          if (statusEl) statusEl.textContent = '';
        } else {
          if (statusEl) statusEl.textContent = data.error || 'No CZML data returned';
        }
        return data;
      })
      .catch(function(err){
        console.error(err);
        if (statusEl) statusEl.textContent = 'Failed to load CZML: ' + err.message;
        throw err;
      });
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initCesium);
  } else {
    initCesium();
  }
})();
