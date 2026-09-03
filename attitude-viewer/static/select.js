/**
 * Satellite selection landing page — Earth with O1A/O1B propagated from their
 * current TLE (EP server), click either satellite to jump into the detail viewer.
 *
 * Orbit propagation is SGP4 (satellite.js) purely for this landing visualization —
 * unrelated to the HK-telemetry-based orbit/footprint pipeline used once inside
 * /viewer, which currently only has real data wired up for O1A.
 */
(function () {
  var SAT_COLORS = {
    O1A: '#a84bff',
    O1B: '#00e5ff',
  };
  var DEFAULT_COLOR = '#ffffff';

  function $(id) { return document.getElementById(id); }
  function apiUrl(path) { return (window.APP_BASE_PATH || '') + path; }

  function goToViewer(satId) {
    window.location.href = apiUrl('/viewer?satellite=' + encodeURIComponent(satId));
  }

  function renderCards(satellites) {
    var wrap = $('select-cards-list');
    wrap.innerHTML = '';
    satellites.forEach(function (satId) {
      var card = document.createElement('div');
      card.className = 'sat-card';
      card.style.setProperty('--sat-color', SAT_COLORS[satId] || DEFAULT_COLOR);
      card.innerHTML =
        '<div class="sat-card-dot"></div>' +
        '<div class="sat-card-name">' + satId + '</div>' +
        '<div class="sat-card-info" id="sat-info-' + satId + '">위치 확인 중...</div>' +
        '<div class="sat-card-sub">클릭하여 선택</div>';
      card.addEventListener('click', function () { goToViewer(satId); });
      wrap.appendChild(card);
    });
  }

  function fmtLatLon(lat, lon) {
    var ns = lat >= 0 ? 'N' : 'S';
    var ew = lon >= 0 ? 'E' : 'W';
    return Math.abs(lat).toFixed(2) + '°' + ns + '  ' + Math.abs(lon).toFixed(2) + '°' + ew;
  }

  function fmtUTC(d) {
    var pad = function (n) { return String(n).padStart(2, '0'); };
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()) + '  ' +
      pad(d.getUTCHours()) + ':' + pad(d.getUTCMinutes()) + ':' + pad(d.getUTCSeconds()) + ' UTC';
  }

  function parseTle(raw) {
    // "0 HEADER\r\n1 ...\r\n2 ..." — header line optional, tolerate \n or \r\n.
    var lines = String(raw || '').trim().split(/\r?\n/).filter(Boolean);
    if (lines.length < 2) return null;
    var l1 = lines[lines.length - 2];
    var l2 = lines[lines.length - 1];
    if (l1[0] !== '1' || l2[0] !== '2') return null;
    return { line1: l1, line2: l2 };
  }

  function buildOrbitPath(satrec, startDate) {
    // Sample one full period at ~180 steps for a smooth static polyline.
    var periodMin = satrec.no ? (2 * Math.PI / satrec.no) : 95;
    var steps = 180;
    var positions = [];
    for (var i = 0; i <= steps; i++) {
      var t = new Date(startDate.getTime() + (periodMin * 60000 * i) / steps);
      var pv = satellite.propagate(satrec, t);
      if (!pv || !pv.position) continue;
      var gmst = satellite.gstime(t);
      var geo = satellite.eciToGeodetic(pv.position, gmst);
      positions.push(Cesium.Cartesian3.fromRadians(geo.longitude, geo.latitude, Math.max(0, geo.height * 1000)));
    }
    return { positions: positions, periodMin: periodMin };
  }

  // True real-time position — recomputed from the actual system clock on every render,
  // independent of Cesium's simulation clock, so this always reflects where the
  // satellite genuinely is right now (not a sped-up/slowed-down demo animation).
  function buildLivePosition(satrec) {
    return new Cesium.CallbackProperty(function () {
      var t = new Date();
      var pv = satellite.propagate(satrec, t);
      if (!pv || !pv.position) return undefined;
      var gmst = satellite.gstime(t);
      var geo = satellite.eciToGeodetic(pv.position, gmst);
      return Cesium.Cartesian3.fromRadians(geo.longitude, geo.latitude, Math.max(0, geo.height * 1000));
    }, false);
  }

  function addSatelliteToViewer(viewer, satId, tle, startDate) {
    var satrec = satellite.twoline2satrec(tle.line1, tle.line2);
    if (!satrec) return null;

    var built = buildOrbitPath(satrec, startDate);
    if (!built.positions.length) return null;

    var color = Cesium.Color.fromCssColorString(SAT_COLORS[satId] || DEFAULT_COLOR);

    viewer.entities.add({
      id: 'orbit-' + satId,
      polyline: {
        positions: built.positions,
        width: 1.5,
        material: color.withAlpha(0.55),
      },
    });

    var posProp = buildLivePosition(satrec);
    viewer.entities.add({
      id: 'sat-' + satId,
      position: posProp,
      point: {
        pixelSize: 10,
        color: color,
        outlineColor: Cesium.Color.WHITE,
        outlineWidth: 1.5,
      },
      label: {
        text: satId,
        font: 'bold 13pt "Segoe UI", system-ui, sans-serif',
        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
        fillColor: Cesium.Color.WHITE,
        outlineColor: Cesium.Color.BLACK,
        outlineWidth: 2,
        verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
        pixelOffset: new Cesium.Cartesian2(0, -14),
      },
    });

    return satrec;
  }

  // Ticks once a second: updates the UTC clock and each tracked satellite's
  // current lat/lon/alt readout on its card, straight from the same real-time
  // SGP4 propagation driving the 3D dot (see buildLivePosition).
  function startInfoLoop(satrecs) {
    var clockEl = $('select-clock');
    function tick() {
      var now = new Date();
      if (clockEl) clockEl.textContent = fmtUTC(now);

      Object.keys(satrecs).forEach(function (satId) {
        var infoEl = $('sat-info-' + satId);
        if (!infoEl) return;
        var satrec = satrecs[satId];
        var pv = satellite.propagate(satrec, now);
        if (!pv || !pv.position) { infoEl.textContent = '위치 계산 실패'; return; }
        var gmst = satellite.gstime(now);
        var geo = satellite.eciToGeodetic(pv.position, gmst);
        var lat = Cesium.Math.toDegrees(geo.latitude);
        var lon = Cesium.Math.toDegrees(geo.longitude);
        infoEl.textContent = fmtLatLon(lat, lon) + '  ·  ' + geo.height.toFixed(0) + 'km';
      });
    }
    tick();
    setInterval(tick, 1000);
  }

  function wirePicking(viewer, satellites) {
    var handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
    handler.setInputAction(function (movement) {
      var picked = viewer.scene.pick(movement.position);
      if (!picked || !picked.id || !picked.id.id) return;
      var id = picked.id.id;
      satellites.forEach(function (satId) {
        if (id === 'sat-' + satId) goToViewer(satId);
      });
    }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
  }

  function buildViewer(container, token) {
    if (token) Cesium.Ion.defaultAccessToken = token;
    var viewer = new Cesium.Viewer(container, {
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
      shouldAnimate: true,
    });
    viewer.scene.globe.enableLighting = true;
    viewer.scene.skyAtmosphere.show = true;
    viewer.scene.backgroundColor = Cesium.Color.BLACK;
    viewer.scene.screenSpaceCameraController.minimumZoomDistance = 3000000;
    viewer.camera.setView({
      destination: Cesium.Cartesian3.fromDegrees(127.0, 25.0, 32000000),
    });
    return viewer;
  }

  function loadTle(satId) {
    return fetch(apiUrl('/api/tle/' + satId))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        var parsed = parseTle(data.currentTle);
        if (!parsed) throw new Error('TLE 파싱 실패');
        return parsed;
      });
  }

  function init() {
    var satellites = window.APP_SATELLITES || ['O1A', 'O1B'];
    renderCards(satellites);

    var container = $('select-cesium-container');
    var statusEl = $('select-status');

    fetch(apiUrl('/cesium-token'))
      .then(function (r) { return r.json(); })
      .then(function (data) { return data.token || ''; })
      .catch(function () { return ''; })
      .then(function (token) {
        var viewer = buildViewer(container, token);
        var startDate = new Date();
        var satrecs = {};
        var loaded = 0;

        // `return`ed (not fire-and-forget) so a bug anywhere in here — like the
        // Cesium.ScreenSpaceEventHandler.MouseClick.LEFT_CLICK typo that used to live
        // in wirePicking() — surfaces through the outer .catch() instead of dying as a
        // silent unhandled rejection that leaves every card stuck on "위치 확인 중...".
        return Promise.all(satellites.map(function (satId) {
          return loadTle(satId)
            .then(function (tle) {
              var satrec = addSatelliteToViewer(viewer, satId, tle, startDate);
              if (satrec) { satrecs[satId] = satrec; loaded++; }
              return satrec;
            })
            .catch(function (err) {
              console.error('TLE load failed for ' + satId, err);
              // Otherwise this card is left showing "위치 확인 중..." forever with no
              // indication anything went wrong — surface the failure right on the card
              // instead of only in the console (which nobody's watching normally).
              var infoEl = $('sat-info-' + satId);
              if (infoEl) infoEl.textContent = 'TLE 로딩 실패: ' + (err && err.message ? err.message : err);
              return null;
            });
        })).then(function () {
          if (loaded > 0) {
            statusEl.style.display = 'none';
            // Position display is the core feature — start it even if picking (a nice-
            // to-have click shortcut; the cards below are always clickable too) fails.
            startInfoLoop(satrecs);
            try {
              wirePicking(viewer, satellites);
            } catch (err) {
              console.error('wirePicking failed (3D click-to-select disabled)', err);
            }
          } else {
            statusEl.textContent = 'TLE 로딩 실패 — 아래 카드에서 위성을 선택하세요.';
          }
        });

        function forceResize() {
          viewer.canvas.width = container.clientWidth;
          viewer.canvas.height = container.clientHeight;
          viewer.resize();
          viewer.scene.requestRender();
        }
        setTimeout(forceResize, 50);
        setTimeout(forceResize, 300);
        if (typeof ResizeObserver !== 'undefined') {
          new ResizeObserver(function () { forceResize(); }).observe(container);
        }
      })
      .catch(function (err) {
        // Nothing upstream of this had a .catch — a throw anywhere in buildViewer()
        // (e.g. the Cesium CDN script failed to load, so `Cesium` is undefined) would
        // otherwise die as a silent unhandled rejection, leaving every card stuck on
        // "위치 확인 중..." forever with no clue why. Surface it instead.
        console.error('Landing page init failed', err);
        statusEl.textContent = '초기화 실패: ' + (err && err.message ? err.message : err);
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
