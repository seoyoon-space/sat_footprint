/**
 * 2D Footprint Map — MapLibre GL with Esri satellite imagery.
 *
 * Draws footprint strip, target marker, and current scan line
 * synced with Cesium playback.
 */
(function () {
  var map = null;
  var footprintData = null;
  var currentProgress = 0;
  var currentTimeMs = null;
  var loaded = false;

  var ESRI_TILES = 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}';

  function initMap() {
    var container = document.getElementById('map2d-container');
    if (!container) { setTimeout(initMap, 100); return; }

    map = new maplibregl.Map({
      container: 'map2d-container',
      style: {
        version: 8,
        sources: {
          'esri-imagery': {
            type: 'raster',
            tiles: [ESRI_TILES],
            tileSize: 256,
            attribution: 'Esri World Imagery',
            maxzoom: 18
          }
        },
        layers: [{
          id: 'esri-layer',
          type: 'raster',
          source: 'esri-imagery',
          minzoom: 0,
          maxzoom: 18
        }]
      },
      // No default target (used to be Paju) — starts zoomed out until the user
      // picks a worldwide AOI/mission or computes a footprint.
      center: [127.0, 20.0],
      zoom: 2,
      attributionControl: false
    });

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right');

    map.on('mousemove', function (e) {
      var infoEl = document.getElementById('map2d-info');
      if (infoEl) {
        infoEl.textContent = e.lngLat.lat.toFixed(4) + '°N  ' + e.lngLat.lng.toFixed(4) + '°E';
      }
    });

    map.on('load', function () {
      loaded = true;
      map.resize();
      addEmptySources();
      if (footprintData) drawAll();
    });

    // Ensure proper sizing after layout settles
    setTimeout(function () { if (map) map.resize(); }, 500);
    setTimeout(function () { if (map) map.resize(); }, 2000);
  }

  function addEmptySources() {
    map.addSource('footprint-strip', {
      type: 'geojson',
      data: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [[]] } }
    });
    map.addSource('footprint-left', {
      type: 'geojson',
      data: { type: 'Feature', geometry: { type: 'LineString', coordinates: [] } }
    });
    map.addSource('footprint-right', {
      type: 'geojson',
      data: { type: 'Feature', geometry: { type: 'LineString', coordinates: [] } }
    });
    map.addSource('scan-line', {
      type: 'geojson',
      data: { type: 'Feature', geometry: { type: 'LineString', coordinates: [] } }
    });
    map.addSource('target-point', {
      type: 'geojson',
      data: { type: 'FeatureCollection', features: [] }
    });
    map.addSource('scan-time-area', {
      type: 'geojson',
      data: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [[]] } }
    });
    map.addSource('capture-area', {
      type: 'geojson',
      data: { type: 'Feature', geometry: { type: 'Polygon', coordinates: [[]] } }
    });

    // Footprint fill
    map.addLayer({
      id: 'footprint-fill',
      type: 'fill',
      source: 'footprint-strip',
      paint: {
        'fill-color': '#00e5ff',
        'fill-opacity': 0.15
      }
    });

    // Left edge
    map.addLayer({
      id: 'footprint-left-line',
      type: 'line',
      source: 'footprint-left',
      paint: {
        'line-color': '#00e5ff',
        'line-width': 1.5,
        'line-opacity': 0.7
      }
    });

    // Right edge
    map.addLayer({
      id: 'footprint-right-line',
      type: 'line',
      source: 'footprint-right',
      paint: {
        'line-color': '#00e5ff',
        'line-width': 1.5,
        'line-opacity': 0.7
      }
    });

    // Mission scan time (eventStart~eventEnd, the scheduled pass window) — translucent
    // yellow fill, showing where within the wider computed strip the mission's nominal
    // window falls.
    map.addLayer({
      id: 'scan-time-area-fill',
      type: 'fill',
      source: 'scan-time-area',
      paint: {
        'fill-color': '#ffd600',
        'fill-opacity': 0.22
      }
    });
    map.addLayer({
      id: 'scan-time-area-line',
      type: 'line',
      source: 'scan-time-area',
      paint: {
        'line-color': '#ffd600',
        'line-width': 1,
        'line-opacity': 0.6
      }
    });

    // Real captured area (camStart~camEnd, actual camera ON~OFF instant) — purple,
    // drawn on top so it stands out against the wider scan-time area.
    map.addLayer({
      id: 'capture-area-fill',
      type: 'fill',
      source: 'capture-area',
      paint: {
        'fill-color': '#9b30ff',
        'fill-opacity': 0.6
      }
    });
    map.addLayer({
      id: 'capture-area-line',
      type: 'line',
      source: 'capture-area',
      paint: {
        'line-color': '#9b30ff',
        'line-width': 1.5,
        'line-opacity': 0.85
      }
    });

    // Scan line
    map.addLayer({
      id: 'scan-line-layer',
      type: 'line',
      source: 'scan-line',
      paint: {
        'line-color': '#ff4081',
        'line-width': 3,
        'line-opacity': 0.9
      }
    });

    // Target point
    map.addLayer({
      id: 'target-circle',
      type: 'circle',
      source: 'target-point',
      paint: {
        'circle-radius': 6,
        'circle-color': '#ff0',
        'circle-stroke-width': 2,
        'circle-stroke-color': '#aa0'
      }
    });
    map.addLayer({
      id: 'target-label',
      type: 'symbol',
      source: 'target-point',
      layout: {
        'text-field': ['get', 'name'],
        'text-size': 12,
        'text-font': ['Open Sans Bold'],
        'text-offset': [1.2, -0.5],
        'text-anchor': 'left'
      },
      paint: {
        'text-color': '#ff0',
        'text-halo-color': '#000',
        'text-halo-width': 1
      }
    });

  }

  function clearStrip() {
    if (!map || !loaded) return;
    map.getSource('footprint-strip').setData({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[]] } });
    map.getSource('footprint-left').setData({ type: 'Feature', geometry: { type: 'LineString', coordinates: [] } });
    map.getSource('footprint-right').setData({ type: 'Feature', geometry: { type: 'LineString', coordinates: [] } });
    map.getSource('scan-line').setData({ type: 'Feature', geometry: { type: 'LineString', coordinates: [] } });
    map.getSource('scan-time-area').setData({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[]] } });
    map.getSource('capture-area').setData({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[]] } });
  }

  function drawAll() {
    if (!map || !loaded || !footprintData) return;
    var lines = footprintData.lines;
    if (!lines || lines.length < 2) {
      // A failed/empty compute (e.g. no HK data for that window) must not leave the
      // previously drawn strip on screen looking like it's still the current result.
      clearStrip();
      if (footprintData.target) {
        map.getSource('target-point').setData({
          type: 'FeatureCollection',
          features: [{
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [footprintData.target.lon, footprintData.target.lat] },
            properties: { name: footprintData.target.name }
          }]
        });
        map.easeTo({ center: [footprintData.target.lon, footprintData.target.lat], zoom: 6, duration: 500 });
      }
      return;
    }

    // Build polygon: left edge forward, right edge backward.
    // Note: footprintData.geojson (if present) covers its own, separately-configured
    // "scan time" window for the download/export — it can be narrower or wider than
    // `lines`, so it's kept out of the on-map strip to avoid the two disagreeing.
    var leftCoords = lines.map(function (l) { return [l.ll[1], l.ll[0]]; });
    var rightCoords = lines.map(function (l) { return [l.rl[1], l.rl[0]]; });
    var polyCoords = leftCoords.concat(rightCoords.slice().reverse());
    polyCoords.push(polyCoords[0]);
    map.getSource('footprint-strip').setData({
      type: 'Feature',
      geometry: { type: 'Polygon', coordinates: [polyCoords] }
    });
    map.getSource('footprint-left').setData({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: leftCoords }
    });
    map.getSource('footprint-right').setData({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: rightCoords }
    });

    // Mission scan time (eventStart~eventEnd) — pink.
    if (footprintData.geojson && footprintData.geojson.geometry) {
      map.getSource('scan-time-area').setData(footprintData.geojson);
    } else {
      map.getSource('scan-time-area').setData({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[]] } });
    }

    // Real captured area (camStart~camEnd) — purple.
    if (footprintData.geojson_capture && footprintData.geojson_capture.geometry) {
      map.getSource('capture-area').setData(footprintData.geojson_capture);
    } else {
      map.getSource('capture-area').setData({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[]] } });
    }

    // Target marker
    if (footprintData.target) {
      var t = footprintData.target;
      map.getSource('target-point').setData({
        type: 'FeatureCollection',
        features: [{
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [t.lon, t.lat] },
          properties: { name: t.name }
        }]
      });
    }

    // Fit map to footprint with 2x zoom (tight padding)
    var bounds = new maplibregl.LngLatBounds();
    leftCoords.forEach(function (c) { bounds.extend(c); });
    rightCoords.forEach(function (c) { bounds.extend(c); });

    map.fitBounds(bounds, { padding: 20, maxZoom: 14 });

    // After fit, zoom in further and recenter on the target itself — the fitBounds
    // center is the footprint strip's centroid, not necessarily where the target is
    // (the strip is a long line; the target is usually off toward one end of it).
    map.once('moveend', function () {
      var currentZoom = map.getZoom();
      var center = (footprintData.target)
        ? [footprintData.target.lon, footprintData.target.lat]
        : map.getCenter();
      map.easeTo({ center: center, zoom: currentZoom + 2, duration: 500 });
    });

    updateScanLine();
  }

  function updateScanLine() {
    if (!map || !loaded || !footprintData || !footprintData.lines) return;
    var lines = footprintData.lines;
    var scanLineSource = map.getSource('scan-line');
    if (lines.length < 2) return;
    if (!scanLineSource) return;

    var idx;
    if (currentTimeMs !== null) {
      var closestDistance = Infinity;
      for (var i = 0; i < lines.length; i++) {
        var lineTimeText = lines[i].t;
        var lineTimeMs = Date.parse(lineTimeText);
        if (isNaN(lineTimeMs) && !/[zZ]|[+-]\d\d:?\d\d$/.test(lineTimeText)) {
          lineTimeMs = Date.parse(lineTimeText + 'Z');
        }
        if (!isNaN(lineTimeMs)) {
          var distance = Math.abs(lineTimeMs - currentTimeMs);
          if (distance < closestDistance) {
            closestDistance = distance;
            idx = i;
          }
        }
      }
    }
    if (idx === undefined) {
      idx = Math.floor(currentProgress * (lines.length - 1));
    }
    idx = Math.max(0, Math.min(lines.length - 1, idx));
    var line = lines[idx];

    scanLineSource.setData({
      type: 'Feature',
      geometry: {
        type: 'LineString',
        coordinates: [
          [line.ll[1], line.ll[0]],
          [line.rl[1], line.rl[0]]
        ]
      }
    });

    var infoEl = document.getElementById('map2d-info');
    if (infoEl) {
      var cLat = ((line.ll[0] + line.rl[0]) / 2).toFixed(4);
      var cLon = ((line.ll[1] + line.rl[1]) / 2).toFixed(4);
      infoEl.textContent = cLat + '°N  ' + cLon + '°E  alt:' + line.la + '/' + line.ra + 'm';
    }
  }


  function loadData() {
    fetch((window.APP_BASE_PATH || '') + '/api/footprint')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        footprintData = data;
        if (loaded) drawAll();
      })
      .catch(function (err) {
        console.error('Footprint load error:', err);
      });
  }

  // Feed an already-fetched footprint response (e.g. from /api/footprint/compute)
  // straight into the map, bypassing the default Paju CSV fetch.
  function loadFromData(data) {
    footprintData = data;
    if (loaded) drawAll();
  }

  // Show just a target marker (no footprint strip yet) and pan/zoom to it —
  // used when an AOI is picked before any mission time window is chosen.
  function centerOnTarget(target, zoom) {
    if (!map || !loaded) { setTimeout(function () { centerOnTarget(target, zoom); }, 200); return; }
    footprintData = { lines: [], target: target, capture_events: [] };
    map.getSource('footprint-strip').setData({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[]] } });
    map.getSource('footprint-left').setData({ type: 'Feature', geometry: { type: 'LineString', coordinates: [] } });
    map.getSource('footprint-right').setData({ type: 'Feature', geometry: { type: 'LineString', coordinates: [] } });
    map.getSource('scan-line').setData({ type: 'Feature', geometry: { type: 'LineString', coordinates: [] } });
    map.getSource('scan-time-area').setData({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[]] } });
    map.getSource('capture-area').setData({ type: 'Feature', geometry: { type: 'Polygon', coordinates: [[]] } });
    map.getSource('target-point').setData({
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [target.lon, target.lat] },
        properties: { name: target.name }
      }]
    });
    map.easeTo({ center: [target.lon, target.lat], zoom: zoom || 6, duration: 500 });
  }

  // Reveals #panel-map2d (see .visible in style.css) the first time an AOI/mission is
  // selected — the panel starts collapsed so nothing shows until there's something to
  // show. The width snaps in instantly (only opacity animates), but MapLibre still
  // rendered at 0-width until now, so nudge it with resize() once the new layout has
  // actually applied (one rAF after the class flip is enough — no animation to wait on).
  var _revealed = false;
  function reveal() {
    var panel = document.getElementById('panel-map2d');
    if (!panel || _revealed) return;
    _revealed = true;
    panel.classList.add('visible');
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { if (map) map.resize(); });
    });
  }

  function syncProgress() {
    requestAnimationFrame(syncProgress);
    var fill = document.getElementById('pb-fill');
    if (fill) {
      var pct = parseFloat(fill.style.width) / 100.0;
      if (!isNaN(pct) && Math.abs(pct - currentProgress) > 0.001) {
        currentProgress = pct;
        updateScanLine();
      }
    }
  }

  window.map2d = {
    load: loadData,
    loadFromData: loadFromData,
    centerOnTarget: centerOnTarget,
    reveal: reveal,
    setTime: function (date) {
      var timeMs = date instanceof Date ? date.getTime() : Date.parse(date);
      if (isNaN(timeMs)) return;
      currentTimeMs = timeMs;
      updateScanLine();
    }
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { initMap(); syncProgress(); });
  } else {
    initMap();
    syncProgress();
  }
})();
