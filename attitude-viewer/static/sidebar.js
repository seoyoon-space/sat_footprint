/**
 * Sidebar — global AOI list + mission history browser.
 *
 * Lets the user pick a worldwide AOI (candidate site) or a scheduled mission
 * (EP server) instead of the hardcoded Paju target, then drives the existing
 * Cesium orbit loader + 2D footprint map for that location/time window.
 */
(function () {
  var selected = null; // {type: 'aoi'|'mission', name, lat, lon, start, end, satellite, raw}
  var aoiItems = [];
  var lastGeojson = null; // most recently computed footprint's GeoJSON — for download

  function $(id) { return document.getElementById(id); }
  function apiUrl(path) { return (window.APP_BASE_PATH || '') + path; }

  // ── Tabs ──
  function wireTabs() {
    var tabs = document.querySelectorAll('.sidebar-tab');
    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        tabs.forEach(function (t) { t.classList.remove('active'); });
        tab.classList.add('active');
        document.querySelectorAll('.sidebar-pane').forEach(function (p) { p.classList.remove('active'); });
        var pane = $('pane-' + tab.dataset.tab);
        if (pane) pane.classList.add('active');
      });
    });
  }

  // ── AOI list ──
  function loadAoiList() {
    var listEl = $('aoi-list');
    fetch(apiUrl('/api/ep/aoi'))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          listEl.innerHTML = '<div class="sidebar-empty">' + escapeHtml(data.error) + '</div>';
          return;
        }
        aoiItems = data.items || [];
        renderAoiList(aoiItems);
      })
      .catch(function (err) {
        listEl.innerHTML = '<div class="sidebar-empty">AOI 목록 로드 실패: ' + escapeHtml(err.message) + '</div>';
      });
  }

  function renderAoiList(items) {
    var listEl = $('aoi-list');
    if (!items.length) {
      listEl.innerHTML = '<div class="sidebar-empty">AOI가 없습니다.</div>';
      return;
    }
    listEl.innerHTML = '';
    items.forEach(function (it) {
      var el = document.createElement('div');
      el.className = 'sidebar-item';
      el.innerHTML =
        '<div class="item-name">' + escapeHtml(it.name || it.englishName || '') + '</div>' +
        '<div class="item-meta">' + fmtLatLon(it.latitude, it.longitude) +
        (it.dueDate ? ('  ·  due ' + it.dueDate.slice(0, 10)) : '') + '</div>';
      el.addEventListener('click', function () {
        document.querySelectorAll('#aoi-list .sidebar-item').forEach(function (e) { e.classList.remove('active'); });
        el.classList.add('active');
        selectAoi(it);
      });
      listEl.appendChild(el);
    });
  }

  function selectAoi(it) {
    selected = {
      type: 'aoi',
      name: it.name || it.englishName || 'AOI',
      lat: it.latitude,
      lon: it.longitude,
      raw: it,
    };
    renderSelected();
    if (window.map2d) window.map2d.centerOnTarget({ name: selected.name, lat: selected.lat, lon: selected.lon }, 6);
    if (window.hkCesium) {
      window.hkCesium.flyTo(selected.lat, selected.lon, 2500000);
      window.hkCesium.setTarget(selected.lat, selected.lon, selected.name);
    }
  }

  // ── Mission history ──
  // Filtered to the satellite picked on the landing page ("MT" = imaging mission;
  // GS (ground-station contact) entries have no imaging target). Orbit/footprint HK
  // computation is still O1A-only — see api_footprint_compute's guard — but mission
  // browsing itself works for any satellite in TB_Selected_Mission_Schedule.
  function currentSatellite() { return window.APP_SATELLITE || 'O1A'; }

  // Defaults the two date inputs to [today-30d, today] (UTC) on first load, unless
  // the user has already touched them (e.g. a re-render after switching satellites).
  function ensureDateRangeDefaults() {
    var startEl = $('mission-start-date');
    var endEl = $('mission-end-date');
    if (!startEl || !endEl) return;
    if (startEl.value && endEl.value) return;
    var now = new Date();
    var past = new Date(now.getTime() - 30 * 86400000);
    function fmt(d) { return d.toISOString().slice(0, 10); }
    endEl.value = fmt(now);
    startEl.value = fmt(past);
  }

  function loadMissions() {
    var listEl = $('mission-list');
    listEl.innerHTML = '<div class="sidebar-empty">Loading...</div>';
    ensureDateRangeDefaults();
    var params = new URLSearchParams({ satellite: currentSatellite(), type: 'MT' });
    var startVal = $('mission-start-date') && $('mission-start-date').value;
    var endVal = $('mission-end-date') && $('mission-end-date').value;
    if (startVal) params.set('start', startVal + 'T00:00:00Z');
    if (endVal) params.set('end', endVal + 'T23:59:59Z');
    fetch(apiUrl('/api/ep/missions?' + params.toString()))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) {
          listEl.innerHTML = '<div class="sidebar-empty">' + escapeHtml(data.error) + '</div>';
          return;
        }
        renderMissionList(data.missions || []);
      })
      .catch(function (err) {
        listEl.innerHTML = '<div class="sidebar-empty">미션 목록 로드 실패: ' + escapeHtml(err.message) + '</div>';
      });
  }

  // AOI(촬영 대상지)의 사람이 읽을 수 있는 이름. clientData.address가 있으면 그게 가장
  // 정확(스케줄링 시점 지오코딩 결과)하고, 없으면 LocationName을 대신 쓴다 — 단, 일부
  // 미션(주로 MXC 등 특정 오퍼레이션)은 LocationName에 실제 이름 대신 scheduleId가
  // 그대로 들어있어서 그 경우는 이름이 없는 것으로 취급한다.
  function missionAoiName(m) {
    var addr = m.clientData && m.clientData.address && (m.clientData.address.ko || m.clientData.address.en);
    if (addr) return addr;
    if (m.location && m.location !== m.scheduleId) return m.location;
    return null;
  }

  // MissionStatus 코드 -> 사람이 읽을 수 있는 라벨.
  var MISSION_STATUS_LABELS = {
    0: 'New',
    1: 'Ready',
    2: 'Passed',
    3: 'Not uploaded',
    4: 'Success',
    5: 'None',
  };
  var MISSION_STATUS_CLASS = {
    4: 'status-success',   // Success
    1: 'status-pending',   // Ready
    3: 'status-info',      // Not uploaded
    2: 'status-info',      // Passed
  };
  function missionStatusBadge(m) {
    var code = m.missionStatus;
    var label = MISSION_STATUS_LABELS.hasOwnProperty(code) ? MISSION_STATUS_LABELS[code] : null;
    var cls = MISSION_STATUS_CLASS[code] || 'status-none';
    if (label) return { text: label, cls: cls, title: null };
    // Unrecognized code — show the raw value rather than hiding it.
    return { text: 'MS' + (code == null ? '?' : code), cls: 'status-none', title: '알 수 없는 MissionStatus 코드값' };
  }

  function renderMissionList(missions) {
    var listEl = $('mission-list');
    if (!missions.length) {
      listEl.innerHTML = '<div class="sidebar-empty">조회된 미션이 없습니다.</div>';
      return;
    }
    // Most recent first.
    missions.sort(function (a, b) { return (b.eventStart || '').localeCompare(a.eventStart || ''); });
    listEl.innerHTML = '';
    missions.forEach(function (m) {
      var el = document.createElement('div');
      el.className = 'sidebar-item';
      var aoiName = missionAoiName(m);
      var status = missionStatusBadge(m);
      // Order: satellite badge, then the scheduleId (the actual identifier — reads
      // better leading the line), then status badges.
      var satBadge = '<span class="item-badge">' + escapeHtml(m.satelliteId || '') + '</span>';
      var statusBadge = '<span class="item-badge ' + status.cls + '"' +
        (status.title ? ' title="' + escapeHtml(status.title) + '"' : '') + '>' +
        escapeHtml(status.text) + '</span>';
      var pendingBadge = isHkPending(m.eventStart)
        ? '<span class="item-badge status-pending" title="최근 미션으로 HK 텔레메트리가 아직 처리 중일 수 있음">처리대기중</span>'
        : '';
      el.innerHTML =
        '<div class="item-name">' + satBadge + escapeHtml(m.scheduleId || m.location || ('#' + m.id)) +
        ' ' + statusBadge + pendingBadge + '</div>' +
        (aoiName ? '<div class="item-aoi">' + escapeHtml(aoiName) + '</div>' : '') +
        '<div class="item-meta">' + fmtUtcTime(m.eventStart) +
        '  ·  ' + fmtLatLon(m.latitude, m.longitude) + '</div>';
      el.addEventListener('click', function () {
        document.querySelectorAll('#mission-list .sidebar-item').forEach(function (e) { e.classList.remove('active'); });
        el.classList.add('active');
        selectMission(m);
      });
      listEl.appendChild(el);
    });
  }

  function selectMission(m) {
    // Mission windows are only a few seconds (imaging duration) — pad generously so
    // there are enough HK telemetry samples for orbit interpolation (Orekit needs >=6).
    var start = addSeconds(m.eventStart, -180);
    var end = addSeconds(m.eventEnd, 180);
    var results = m.results || null;
    // Same AOI-name resolution as the mission list's .item-aoi line (clientData.address
    // first, falling back to LocationName when it's not just a copy of scheduleId) —
    // was only checking clientData.address before, missing that fallback case.
    var address = missionAoiName(m);

    // latitude/longitude now comes straight from the EP/MCE server's own DB (the
    // scheduling-time target), confirmed accurate against real place names.
    var lat = m.latitude;
    var lon = m.longitude;

    selected = {
      type: 'mission',
      name: m.scheduleId || m.location || ('Mission #' + m.id),
      lat: lat,
      lon: lon,
      address: address,
      start: start,
      end: end,
      satellite: m.satelliteId,
      missionStatus: m.missionStatus,
      results: results,
      raw: m,
    };
    renderSelected();
    // The marker/label on the map shows the AOI name (e.g. the target place), not the
    // scheduleId — much more useful at a glance than "O1A_15658_GGD". Falls back to
    // the scheduleId only when the mission has no resolvable AOI name.
    var markerName = missionAoiName(m) || selected.name;
    // The map only shows our own computed footprint (from the button below) — the
    // scheduled/estimated/actual-capture overlays were dropped as visual clutter.
    if (window.map2d) window.map2d.centerOnTarget({ name: markerName, lat: lat, lon: lon }, 8);
    if (window.hkCesium) {
      window.hkCesium.flyTo(lat, lon, 1200000);
      window.hkCesium.setTarget(lat, lon, markerName);
    }
  }

  // ── Selected panel ──
  function renderSelected() {
    var panel = $('sidebar-selected');
    if (!selected) { panel.style.display = 'none'; return; }
    panel.style.display = 'flex';
    if (window.map2d && window.map2d.reveal) window.map2d.reveal();
    $('selected-name').textContent = selected.name;

    // AOI name goes on its own line right under the name header, ahead of lat/lon.
    var metaLines = [];
    if (selected.address) {
      metaLines.push('<span class="verified-note">' + escapeHtml(selected.address) + '</span>');
    }
    metaLines.push(fmtLatLon(selected.lat, selected.lon));
    if (selected.type === 'mission') {
      metaLines.push(selected.satellite + '  ' + fmtUtcTime(selected.start, true) + ' ~ ' + fmtUtcTime(selected.end));
      metaLines.push('상태: ' + missionStatusBadge(selected).text);
      if (selected.raw && isHkPending(selected.raw.eventStart)) {
        metaLines.push('<span class="pending-note">⚠ 최근 미션으로 HK 텔레메트리가 아직 처리 중일 수 있음</span>');
      }
    } else {
      metaLines.push('AOI (촬영 예정 후보지 — 시간대 미확정)');
    }
    $('selected-meta').innerHTML = metaLines.join('<br>');

    var computeBtn = $('btn-compute-footprint');
    computeBtn.disabled = selected.type !== 'mission' || !window.APP_HK_ENABLED;
    computeBtn.title = window.APP_HK_ENABLED ? '' :
      (currentSatellite() + '는 아직 HK 텔레메트리 DB 연동이 없어 궤도/footprint 계산을 지원하지 않습니다.');
    // A new selection invalidates any previously computed footprint's download.
    lastGeojson = null;
    $('btn-download-geojson').disabled = true;
    $('btn-download-geojson').classList.remove('primary');
    setStatus('');
  }

  function setStatus(text, isError) {
    var el = $('selected-status');
    el.textContent = text || '';
    el.classList.toggle('error', !!isError);
  }

  // ── Actions ──
  function wireActions() {
    $('mission-refresh').addEventListener('click', loadMissions);

    $('btn-download-geojson').addEventListener('click', function () {
      if (!lastGeojson) return;
      var blob = new Blob([JSON.stringify(lastGeojson, null, 2)], { type: 'application/geo+json' });
      var url = URL.createObjectURL(blob);
      var a = document.createElement('a');
      var safeName = (selected && selected.name || 'footprint').replace(/[^A-Za-z0-9_-]+/g, '_');
      a.href = url;
      a.download = safeName + '.geojson';
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    });

    // Single action: loads the orbit (so the playback timeline reflects this
    // mission's real pass time, not whatever was loaded before) and computes the
    // footprint together. The orbit is held paused until both finish, so it doesn't
    // start moving while the result is still mid-computation.
    $('btn-compute-footprint').addEventListener('click', function () {
      if (!selected || selected.type !== 'mission') return;
      setStatus('궤도/Footprint 계산 중... (DEM/Java 파이프라인, 수십 초 소요될 수 있음)');
      if (window.hkCesium) window.hkCesium.setPlaying(false);
      startFakeProgress();

      // start/end (padded ±3min) is only needed so Orekit has enough HK samples to fit
      // the orbit — but drawing the whole padded window as "the footprint" makes the
      // strip absurdly long (a LEO satellite covers ~2800km in that time). The map
      // strip uses a smaller ±15s margin around the real imaging window.
      //
      // Two overlays mark what's real within that strip:
      //   - pink   (geojson_start/end)  = eventStart~eventEnd, the mission's scheduled
      //                                   pass window
      //   - purple (capture_start/end)  = camStart~camEnd, the real camera ON~OFF
      //                                   instant (mce_db.py compute_camera_window,
      //                                   typically only ~10.4s) — this is also what
      //                                   the drawn strip itself is centered on.
      var rawMission = (selected.raw || {});
      var LINE_MARGIN_SEC = 60; // testing a wider margin around camStart/camEnd (was 15s)
      var captureStart = rawMission.camStart;
      var captureEnd = rawMission.camEnd;
      var scanStart = rawMission.eventStart;
      var scanEnd = rawMission.eventEnd;
      var lineBaseStart = captureStart || scanStart;
      var lineBaseEnd = captureEnd || scanEnd;
      var lineStart = addSeconds(lineBaseStart, -LINE_MARGIN_SEC) || selected.start;
      var lineEnd = addSeconds(lineBaseEnd, LINE_MARGIN_SEC) || selected.end;
      var geojsonStart = scanStart || lineStart;
      var geojsonEnd = scanEnd || lineEnd;

      // Load the orbit for the same window too, so the playback bar/3D clock actually
      // scrubs through this mission's real pass time instead of staying on whatever
      // was loaded before (e.g. the default Paju 08/08 window).
      var orbitPromise = window.loadCzml({
        start: selected.start,
        end: selected.end,
        target_name: selected.name,
        target_lat: selected.lat,
        target_lon: selected.lon,
      }).catch(function () { return null; });

      var params = new URLSearchParams({
        start: selected.start,
        end: selected.end,
        line_start: lineStart,
        line_end: lineEnd,
        geojson_start: geojsonStart,
        geojson_end: geojsonEnd,
        target_name: selected.name,
        target_lat: selected.lat,
        target_lon: selected.lon,
        satellite: selected.satellite || currentSatellite(),
      });
      if (captureStart && captureEnd) {
        params.set('capture_start', captureStart);
        params.set('capture_end', captureEnd);
      }
      var footprintPromise = fetch(apiUrl('/api/footprint/compute?' + params.toString()))
        .then(function (r) {
          if (!r.ok) {
            return r.text().then(function (text) {
              throw new Error('서버 오류 (HTTP ' + r.status + '): ' + text.slice(0, 200));
            });
          }
          return r.json();
        });

      Promise.all([orbitPromise, footprintPromise])
        .then(function (results) {
          var data = results[1];
          stopFakeProgress(!data.error);
          if (data.error) {
            setStatus(data.error, true);
          } else {
            setStatus('완료 — 궤도 로드 + Footprint 계산 (' + data.sampled + '/' + data.total + ' lines).');
          }
          // Download the real camera ON~OFF window (purple) when available — it's
          // the actual captured area, not just the eventStart~eventEnd pass window
          // (pink). Falls back to the pass window when a mission has no
          // MissionParameterJson to derive camStart/camEnd from.
          lastGeojson = data.geojson_capture || data.geojson || null;
          $('btn-download-geojson').disabled = !lastGeojson;
          // Brighter (same accent as the primary action button) once it's actually
          // downloadable, instead of staying visually identical to its disabled state.
          $('btn-download-geojson').classList.toggle('primary', !!lastGeojson);
          if (window.map2d) window.map2d.loadFromData(data);
          if (window.hkCesium) window.hkCesium.setPlaying(true);
        })
        .catch(function (err) {
          stopFakeProgress(false);
          setStatus('Footprint 계산 실패: ' + err.message, true);
          if (window.hkCesium) window.hkCesium.setPlaying(true);
        });
    });
  }

  // ── Progress estimate (footprint compute has no real progress feed — the Java/DEM
  // pipeline is a single blocking subprocess call — so this eases toward 90% over a
  // typical run's duration and only ever snaps to 100% once the real result lands) ──
  var _progressTimer = null;
  function setProgressPct(pct) {
    $('progress-fill').style.width = pct + '%';
    $('progress-label').textContent = Math.round(pct) + '%';
  }
  function startFakeProgress() {
    if (_progressTimer) clearInterval(_progressTimer);
    var bar = $('progress-bar');
    bar.style.display = 'block';
    bar.title = '실제 서버 진행률이 아니라 일반적인 소요 시간 기준 추정치입니다.';
    var pct = 0;
    setProgressPct(pct);
    _progressTimer = setInterval(function () {
      pct += (90 - pct) * 0.08;
      setProgressPct(pct);
    }, 500);
  }
  function stopFakeProgress(success) {
    if (_progressTimer) { clearInterval(_progressTimer); _progressTimer = null; }
    setProgressPct(success ? 100 : 0);
    setTimeout(function () { $('progress-bar').style.display = 'none'; }, success ? 800 : 0);
  }

  // ── Search filter (AOI) ──
  function wireSearch() {
    $('aoi-search').addEventListener('input', function () {
      var q = this.value.trim().toLowerCase();
      if (!q) { renderAoiList(aoiItems); return; }
      renderAoiList(aoiItems.filter(function (it) {
        return (it.name || '').toLowerCase().includes(q) ||
               (it.englishName || '').toLowerCase().includes(q) ||
               (it.description || '').toLowerCase().includes(q);
      }));
    });
  }

  // ── Helpers ──
  // Ground-station -> HK DB ingestion lag — heuristic warning threshold, not a live
  // check, so it can be off by a day or so. Just meant to warn before the user hits
  // the "no HK data" error on a too-recent mission.
  var HK_LAG_DAYS = 4;
  function isHkPending(eventStartIso) {
    var t = Date.parse(eventStartIso);
    if (isNaN(t)) return false;
    return (Date.now() - t) < HK_LAG_DAYS * 86400000;
  }

  function fmtLatLon(lat, lon) {
    if (lat === undefined || lat === null || lon === undefined || lon === null) return '--';
    return Number(lat).toFixed(4) + '°  ' + Number(lon).toFixed(4) + '°';
  }

  // All mission timestamps from the server (eventStart/camStart/etc.) are UTC ISO8601.
  // This project's convention is to always display UTC explicitly (never silently show
  // KST) so times here are directly comparable to the 3D viewer's UTC clock/playback
  // bar. noSuffix=true skips the trailing " UTC" label (for chaining a start~end range
  // where only the end needs it).
  function fmtUtcTime(iso, noSuffix) {
    if (!iso) return '--';
    var s = iso.replace('T', ' ').replace(/\.\d+/, '').replace('Z', '');
    return noSuffix ? s : s + ' UTC';
  }

  function addSeconds(isoStr, secs) {
    if (!isoStr) return isoStr;
    var d = new Date(isoStr.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(isoStr) ? isoStr : isoStr + 'Z');
    d.setSeconds(d.getSeconds() + secs);
    return d.toISOString();
  }

  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function init() {
    wireTabs();
    wireActions();
    wireSearch();
    loadMissions();
    loadAoiList();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
