/* SundaySignal web app: games list, Watch screen and Settings.
   Hash routes: #/ (games), #/watch/<key>, #/settings, #/settings/<page>.
   The <video> element is created once and never re-rendered, so polling
   for scores and streams never interrupts playback. */
(function () {
  'use strict';

  const CONFIG = window.SUNDAYSIGNAL || {};
  const $ = (id) => document.getElementById(id);
  const app = $('app');
  const video = $('video');
  const wideQuery = window.matchMedia('(min-width: 1024px)');

  // How far behind the true live edge playback deliberately sits. These
  // are scraped third-party mirrors, not a broadcast-grade low-latency
  // origin, so a small cushion isn't enough to absorb a normal blip —
  // riding 20s back gives hls.js a real buffer to draw from instead of
  // stalling the moment a segment fetch is slow.
  const LIVE_EDGE_CUSHION_SECONDS = 20;
  const FAST_POLL_MS = 30000;
  const SLOW_POLL_MS = 300000;
  const DETAIL_POLL_MS = 30000;
  const THEME_KEY = 'sundaysignal.theme';
  const LAST_WATCHED_KEY = 'sundaysignal.lastWatched';

  const state = {
    data: null,
    games: [],
    byKey: new Map(),
    loadedAt: 0,
    loadError: null,
    view: 'games',
    settingsPage: 'root',
    currentKey: null,
    tab: 'plays',
    scores: new Map(),
    details: new Map(),
  };

  // ---------- helpers ----------
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function storageGet(store, key) {
    try { return store.getItem(key); } catch (_) { return null; }
  }

  function storageSet(store, key, value) {
    try { store.setItem(key, value); } catch (_) {}
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) {}
    // Plain http on a LAN IP isn't a secure context, so the async
    // clipboard API is unavailable there — fall back to a scratch textarea.
    try {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.setAttribute('readonly', '');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      return ok;
    } catch (_) {
      return false;
    }
  }

  let toastTimer = null;
  function toast(text) {
    $('toastText').textContent = text;
    $('toast').hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $('toast').hidden = true; }, 4000);
  }

  function isWide() { return wideQuery.matches; }

  // ---------- game model ----------
  function gameKey(g) {
    return String(g.id || g.espn_id || g.slug || g.title || '');
  }

  function isChannel(g) {
    return g.always_live === true || g.is_matchup === false;
  }

  function gameState(g) {
    const s = g.status_state || '';
    if (s === 'in' || g.live || g.always_live) return 'live';
    if (s === 'post' || g.ended) return 'final';
    return 'later';
  }

  function sourcesFor(g) {
    return (g.streams || [])
      .map(s => ({
        media: s.play_url || (s.media_url ? ('/proxy?url=' + encodeURIComponent(s.media_url)) : null),
        name: s.name || 'Live',
      }))
      .filter(s => s.media);
  }

  function teams(g) {
    const away = {
      name: g.display_left_team || g.away_team || '',
      logo: g.display_left_logo || g.away_logo,
      abbr: g.away_abbr || '',
      score: g.away_score,
      color: g.away_color,
    };
    const home = {
      name: g.display_right_team || g.home_team || '',
      logo: g.display_right_logo || g.home_logo,
      abbr: g.home_abbr || '',
      score: g.home_score,
      color: g.home_color,
    };
    return { away, home };
  }

  function shortName(name) {
    const words = String(name || '').trim().split(/\s+/);
    return words[words.length - 1] || '';
  }

  function gameTitle(g) {
    if (isChannel(g)) return g.display_title || g.title || 'Channel';
    const { away, home } = teams(g);
    if (away.name && home.name) return shortName(away.name) + ' at ' + shortName(home.name);
    return g.display_title || g.title || g.slug || 'Game';
  }

  function hasScore(g) {
    return gameState(g) !== 'later' && !isChannel(g) && g.away_score != null && g.home_score != null;
  }

  function kickoffText(g) {
    const d = g.start_time ? new Date(g.start_time) : null;
    if (d && !Number.isNaN(d.getTime())) {
      const now = new Date();
      const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
      if (d.toDateString() === now.toDateString()) return time.toUpperCase();
      if (Math.abs(d - now) < 6 * 86400000) {
        return (d.toLocaleDateString(undefined, { weekday: 'short' }) + ' ' + time).toUpperCase();
      }
      return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }).toUpperCase();
    }
    return (g.kickoff_local || 'TBD').toUpperCase();
  }

  function clockText(g) {
    const st = gameState(g);
    if (isChannel(g)) return st === 'live' ? 'LIVE' : 'OFF AIR';
    if (st === 'live') {
      const detail = g.status_detail || '';
      if (/half/i.test(detail)) return 'HALF';
      const q = g.period ? (g.period > 4 ? 'OT' : 'Q' + g.period) : '';
      if (/^end/i.test(detail) && q) return 'END ' + q;
      if (q) return g.display_clock ? q + ' ' + g.display_clock : q;
      return 'LIVE';
    }
    if (st === 'final') return g.period > 4 ? 'FINAL/OT' : 'FINAL';
    return kickoffText(g);
  }

  function logoHtml(team, cls, size) {
    if (team.logo) {
      return `<img class="${cls}" src="${esc(team.logo)}" alt="" width="${size}" height="${size}" loading="lazy" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'tile ${cls}',textContent:${esc(JSON.stringify((team.abbr || team.name.slice(0, 3)).toUpperCase()))}}))" />`;
    }
    return `<span class="tile ${cls}">${esc((team.abbr || team.name.slice(0, 3)).toUpperCase())}</span>`;
  }

  // A fixed 12px signal glyph: one to three bars lit by source count.
  function barsIcon(count) {
    const lit = Math.min(count, 3);
    const bar = (i, x, h) =>
      `<rect x="${x}" y="${12 - h}" width="3" height="${h}" fill="currentColor" opacity="${i < lit ? 1 : 0.28}"/>`;
    return `<svg class="bars" viewBox="0 0 12 12" width="12" height="12" aria-hidden="true">${bar(0, 0, 5)}${bar(1, 4.5, 8)}${bar(2, 9, 12)}</svg>`;
  }

  function sourceLabel(count) {
    if (!count) return `<span class="src none">${barsIcon(0)}Not available</span>`;
    return `<span class="src">${barsIcon(count)}Watch · ${count} source${count === 1 ? '' : 's'}</span>`;
  }

  function withAlpha(hex, alpha) {
    return /^#[0-9a-f]{6}$/i.test(hex || '') ? hex + alpha : null;
  }

  // ---------- games list ----------
  function rowHtml(g) {
    const key = gameKey(g);
    const st = gameState(g);
    const count = sourcesFor(g).length;
    const cls = ['game-row', 'tap'];
    if (st === 'live') cls.push('is-live');
    if (st === 'final') cls.push('is-final');
    if (key === state.currentKey) cls.push('active');
    const status = `<span class="status"><span class="clock cond">${esc(clockText(g))}</span>${sourceLabel(count)}</span>`;
    const href = '#/watch/' + encodeURIComponent(key);
    if (isChannel(g)) {
      cls.push('is-channel');
      const team = { name: g.display_title || g.title || '', logo: g.display_left_logo || g.away_logo, abbr: '' };
      return `<a class="${cls.join(' ')}" href="${href}" data-key="${esc(key)}">
        ${logoHtml(team, 'logo span2', 28)}
        <span class="team span2" style="grid-column:2 / span 2">${esc(team.name)}</span>
        ${status}</a>`;
    }
    const { away, home } = teams(g);
    const scored = hasScore(g);
    const final = st === 'final' && scored;
    const awayLost = final && away.score < home.score;
    const homeLost = final && home.score < away.score;
    const line = (t, lost) => `
      ${logoHtml(t, 'logo' + (lost ? ' lost' : ''), 28)}
      <span class="team${lost ? ' lost' : ''}">${esc(t.name)}</span>
      <span class="score cond${lost ? ' lost' : ''}">${scored ? esc(t.score) : ''}</span>`;
    return `<a class="${cls.join(' ')}" href="${href}" data-key="${esc(key)}"
      aria-label="${esc(away.name + ' at ' + home.name + ', ' + clockText(g))}">
      ${line(away, awayLost)}${line(home, homeLost)}${status}</a>`;
  }

  function featuredGame() {
    const live = state.games.filter(g => gameState(g) === 'live' && !isChannel(g));
    return live.find(g => sourcesFor(g).length) || live[0];
  }

  function renderHero() {
    const g = featuredGame();
    const hero = $('hero');
    if (!g) { hero.innerHTML = ''; return; }
    const { away, home } = teams(g);
    const scored = hasScore(g);
    const awayLead = scored && away.score > home.score;
    const homeLead = scored && home.score > away.score;
    const washes = [];
    const a = withAlpha(away.color, '40');
    const h = withAlpha(home.color, '40');
    if (a) washes.push(`linear-gradient(90deg, ${a}, transparent 60%)`);
    if (h) washes.push(`linear-gradient(270deg, ${h}, transparent 60%)`);
    const net = g.broadcast ? `<span class="net">${esc(g.broadcast.toUpperCase())}</span>` : '';
    const teamLine = (t, lead) => `<div class="hero-team">${logoHtml(t, '', 40)}
      <span class="name wide">${esc(shortName(t.name))}</span>
      <span class="score cond${lead ? ' lead' : ''}">${scored ? esc(t.score) : ''}</span></div>`;
    const count = sourcesFor(g).length;
    hero.innerHTML = `<div class="hero"${washes.length ? ` style="background-image:${washes.join(',')}"` : ''}>
      <div class="hero-top"><span class="tag tag-live"><span class="dot"></span>IN PROGRESS</span>${net}
        <span class="hero-clock cond">${esc(clockText(g))}</span></div>
      ${teamLine(away, awayLead)}${teamLine(home, homeLead)}
      ${count
        ? `<a class="btn btn-primary btn-block tap" href="#/watch/${encodeURIComponent(gameKey(g))}">WATCH LIVE · ${count} SOURCE${count === 1 ? '' : 'S'}</a>`
        : `<a class="btn btn-block tap" href="#/watch/${encodeURIComponent(gameKey(g))}">GAME DETAILS</a>`}
    </div>`;
  }

  function renderAvailability() {
    const watchable = state.games.filter(g => sourcesFor(g).length).length;
    $('liveCount').hidden = !watchable;
    $('liveCountText').textContent = watchable + ' WATCHABLE';
  }

  function renderList() {
    const list = $('gameList');
    if (state.loadError && !state.games.length) {
      list.innerHTML = `<div class="empty">Couldn't load games (${esc(state.loadError)}). Retrying shortly.</div>`;
      return;
    }
    if (!state.data) { list.innerHTML = ''; return; }
    const games = state.games;
    if (!state.games.length) {
      list.innerHTML = `<div class="empty">No games listed yet. The schedule may be unreachable. Check the crawler logs${CONFIG.isAdmin ? ', or run Refresh streams from Settings' : ''}.</div>`;
      return;
    }
    const groups = [];
    const push = (label, items) => { if (items.length) groups.push({ label, items }); };
    const featured = featuredGame();
    const live = games.filter(g => gameState(g) === 'live');
    push('In progress', live.filter(g => !isChannel(g) && g !== featured));
    push('Channels', live.filter(isChannel));
    push('Upcoming', games.filter(g => gameState(g) === 'later'));
    push('Final', games.filter(g => gameState(g) === 'final'));
    list.innerHTML = groups.map(gr => `<section class="group"><h2 class="eyebrow">${esc(gr.label)}</h2>
      ${gr.items.map(rowHtml).join('')}</section>`).join('');
  }

  function renderStatus() {
    if (!state.loadedAt) return;
    const secs = Math.max(0, Math.round((Date.now() - state.loadedAt) / 1000));
    const ago = secs < 60 ? secs + 's' : Math.round(secs / 60) + 'm';
    const withStreams = state.games.filter(g => sourcesFor(g).length).length;
    let msg = `Updated ${ago} ago · ${state.games.length} game${state.games.length === 1 ? '' : 's'}, ${withStreams} with streams`;
    const attempt = (state.data && state.data.last_attempt) || {};
    // The catalog timestamp only moves on a successful write, so without
    // this a crawler that runs but finds nothing looks like a dead one.
    const stale = attempt.kept_previous && attempt.scraped_at && attempt.scraped_at !== state.data.scraped_at;
    if (state.loadError) msg = `Couldn't refresh (${state.loadError}) · showing the last list`;
    else if (stale) msg += ' · last scrape found nothing, showing the previous list';
    // The list carries this line on phones; the header carries it on wide screens.
    [$('statusLine'), $('headerStatus')].forEach(line => {
      line.lastElementChild.textContent = msg;
      line.classList.toggle('fresh', secs < 90 && !state.loadError && !stale);
      line.classList.toggle('warn', Boolean(state.loadError || stale));
      if (state.data && state.data.scraped_at) line.title = 'Catalog scraped ' + new Date(state.data.scraped_at).toLocaleString();
    });
  }

  function renderResume() {
    const bar = $('resume');
    const key = storageGet(sessionStorage, LAST_WATCHED_KEY);
    const g = key && state.byKey.get(key);
    if (!g || state.view !== 'games' || isWide() || gameState(g) !== 'live' || !sourcesFor(g).length) {
      bar.hidden = true;
      return;
    }
    bar.hidden = false;
    bar.href = '#/watch/' + encodeURIComponent(key);
    $('resumeTitle').textContent = gameTitle(g);
    const scored = hasScore(g);
    $('resumeClock').textContent = (scored ? g.away_score + '–' + g.home_score + '  ' : '') + clockText(g);
  }

  function renderGames() {
    renderAvailability();
    renderHero();
    renderList();
    renderStatus();
    renderResume();
  }

  // ---------- data loading ----------
  let pollTimer = null;
  let loading = false;

  function schedulePoll() {
    clearTimeout(pollTimer);
    const anyLive = state.games.some(g => gameState(g) === 'live' && !isChannel(g));
    const delay = (anyLive || state.view === 'watch') ? FAST_POLL_MS : SLOW_POLL_MS;
    pollTimer = setTimeout(() => {
      if (document.visibilityState === 'visible') load();
      else schedulePoll();
    }, delay);
  }

  async function load() {
    if (loading) return;
    loading = true;
    try {
      const res = await fetch('/api/streams?_=' + Date.now(), { cache: 'no-store', credentials: 'same-origin' });
      if (res.status === 401) { window.location.href = '/login'; return; }
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const payload = await res.json();
      state.data = payload;
      state.games = payload.games || [];
      state.byKey = new Map(state.games.map(g => [gameKey(g), g]));
      state.loadedAt = Date.now();
      state.loadError = null;
      const flashed = [];
      state.games.forEach(g => {
        const key = gameKey(g);
        const score = hasScore(g) ? g.away_score + '-' + g.home_score : null;
        const prev = state.scores.get(key);
        if (prev !== undefined && score && prev !== score) flashed.push(key);
        state.scores.set(key, score);
      });
      renderGames();
      flashed.forEach(key => {
        const row = document.querySelector(`.game-row[data-key="${CSS.escape(key)}"]`);
        if (!row) return;
        row.classList.add('flash');
        setTimeout(() => row.classList.remove('flash'), 1600);
      });
      if (state.currentKey) renderWatch(false);
    } catch (e) {
      state.loadError = e.message || String(e);
      renderGames();
    } finally {
      loading = false;
      schedulePoll();
    }
  }

  // ---------- player ----------
  let hls = null;
  let playGeneration = 0;
  let playingKey = null;
  let currentSources = [];
  let currentSourceIndex = -1;
  let failedSourceIndexes = new Set();

  function stopPlayer() {
    playGeneration += 1;
    if (hls) { hls.destroy(); hls = null; }
    video.removeAttribute('src');
    video.load();
    $('goLive').hidden = true;
    $('sourceTag').hidden = true;
  }

  function jumpToLiveEdge() {
    try {
      if (hls && hls.liveSyncPosition != null) {
        video.currentTime = hls.liveSyncPosition;
        video.play().catch(() => {});
        return;
      }
      // Native HLS (Safari) or VOD-style duration. Land on the same
      // cushioned offset playback starts at, not the bleeding edge —
      // catching up shouldn't trade the buffer margin away again.
      if (video.seekable && video.seekable.length > 0) {
        const end = video.seekable.end(video.seekable.length - 1);
        video.currentTime = Math.max(0, end - LIVE_EDGE_CUSHION_SECONDS);
        video.play().catch(() => {});
        return;
      }
      if (isFinite(video.duration) && video.duration > 0) {
        video.currentTime = video.duration;
        video.play().catch(() => {});
      }
    } catch (e) {
      console.warn('live edge seek failed', e);
    }
  }

  function atLiveEdge() {
    try {
      if (hls && hls.liveSyncPosition != null) return video.currentTime >= hls.liveSyncPosition - 4;
      if (video.seekable && video.seekable.length > 0) {
        const end = video.seekable.end(video.seekable.length - 1);
        return end - video.currentTime <= LIVE_EDGE_CUSHION_SECONDS + 4;
      }
    } catch (_) {}
    return false;
  }

  video.addEventListener('timeupdate', () => {
    $('goLive').classList.toggle('at-edge', atLiveEdge());
  });
  $('goLive').addEventListener('click', jumpToLiveEdge);

  function logPlayback(text) {
    $('playLog').textContent = text;
  }

  function playMedia(url) {
    stopPlayer();
    const myGeneration = playGeneration;
    $('player').classList.remove('no-stream');
    $('playerEmpty').hidden = true;
    $('btnRetryPlayback').hidden = true;
    $('goLive').hidden = false;
    $('sourceTag').hidden = false;
    $('sourceTag').textContent = 'SOURCE ' + (currentSourceIndex + 1);

    if (video.canPlayType('application/vnd.apple.mpegurl')) {
      // Safari's native player has no buffer-target knob, so establish
      // the same cushion with one seek right after metadata loads, then
      // let its own buffering take over from there.
      video.src = url;
      video.play().catch(() => {});
      video.addEventListener('loadedmetadata', function onMeta() {
        video.removeEventListener('loadedmetadata', onMeta);
        try {
          if (video.seekable && video.seekable.length > 0) {
            const end = video.seekable.end(video.seekable.length - 1);
            video.currentTime = Math.max(0, end - LIVE_EDGE_CUSHION_SECONDS);
          }
        } catch (e) { /* live edge not seekable yet; play from default position */ }
      });
      video.addEventListener('error', function onError() {
        video.removeEventListener('error', onError);
        if (myGeneration !== playGeneration) return; // stale: user already moved on
        tryNextSource('Playback error');
      });
      return;
    }
    if (window.Hls && Hls.isSupported()) {
      hls = new Hls({
        enableWorker: true,
        // These are third-party mirrors, not real low-latency HLS —
        // chasing the live edge just means playing right up against
        // whatever's already downloaded, so a normal network hiccup
        // empties the buffer and stalls playback. A fixed seconds-based
        // target holds steady regardless of segment length.
        lowLatencyMode: false,
        liveSyncDuration: LIVE_EDGE_CUSHION_SECONDS,
        // hls.js's own guidance: keep this well above (3-4x) the sync
        // duration, or playback breaks and flushes constantly instead
        // of settling into the cushion.
        liveMaxLatencyDuration: LIVE_EDGE_CUSHION_SECONDS * 4,
        backBufferLength: 60,
        maxBufferLength: 60,
        maxMaxBufferLength: 180,
        // Mirror CDNs blip more than a real broadcast origin; retry
        // segment/playlist fetches instead of treating a single failed
        // request as fatal and jumping to the next source.
        fragLoadingMaxRetry: 8,
        fragLoadingRetryDelay: 1000,
        fragLoadingMaxRetryTimeout: 20000,
        manifestLoadingMaxRetry: 4,
        levelLoadingMaxRetry: 6,
      });
      hls.loadSource(url);
      hls.attachMedia(video);
      hls.on(Hls.Events.MANIFEST_PARSED, () => {
        video.play().catch(() => {});
      });
      hls.on(Hls.Events.ERROR, (_, d) => {
        if (d.fatal) {
          if (myGeneration !== playGeneration) return; // stale: user already moved on
          tryNextSource(`HLS error: ${d.type} / ${d.details}`);
        }
      });
    } else {
      logPlayback('This browser cannot play HLS streams.');
    }
  }

  function playSourceAtIndex(idx) {
    if (idx < 0 || idx >= currentSources.length) return;
    currentSourceIndex = idx;
    renderSources();
    playMedia(currentSources[idx].media);
  }

  function tryNextSource(reason) {
    failedSourceIndexes.add(currentSourceIndex);
    const nextIdx = currentSources.findIndex((_, i) => i > currentSourceIndex && !failedSourceIndexes.has(i));
    if (nextIdx === -1) {
      $('btnRetryPlayback').hidden = false;
      renderSources();
      logPlayback(`${reason}. No more sources for this game${CONFIG.isAdmin ? '. Try Refresh streams in Settings.' : '.'}`);
      return;
    }
    logPlayback(`${reason}. Switched to source ${nextIdx + 1}.`);
    playSourceAtIndex(nextIdx);
  }

  function playGame(g) {
    $('btnRetryPlayback').hidden = true;
    playingKey = gameKey(g);
    currentSources = sourcesFor(g);
    failedSourceIndexes = new Set();
    currentSourceIndex = -1;
    logPlayback('');
    if (!currentSources.length) {
      stopPlayer();
      showPlayerEmpty(g);
      renderSources();
      return;
    }
    storageSet(sessionStorage, LAST_WATCHED_KEY, playingKey);
    playSourceAtIndex(0);
  }

  function showPlayerEmpty(g) {
    const empty = $('playerEmpty');
    $('player').classList.toggle('no-stream', !!g);
    empty.hidden = false;
    if (!g) {
      empty.className = 'empty-state pick';
      empty.innerHTML = '<strong>Pick a game</strong><span>Choose a game from the list to start watching.</span>';
      return;
    }
    const final = gameState(g) === 'final';
    empty.className = 'empty-state';
    empty.innerHTML = final
      ? '<strong>No stream</strong><span>No stream was found for this game before it ended.</span>'
      : '<strong>Stream not available yet</strong><span>We’ll keep checking automatically. Streams usually appear closer to kickoff.</span>';
  }

  // ---------- watch screen ----------
  function renderScoreboard(g) {
    const st = gameState(g);
    if (isChannel(g)) {
      const team = { name: g.display_title || g.title || '', logo: g.display_left_logo || g.away_logo, abbr: '' };
      $('scoreboard').innerHTML = `<div class="scoreboard"><div class="sb-team">${logoHtml(team, '', 44)}
        <span class="name wide">${esc(team.name)}</span></div>
        <div class="sb-status cond">${esc(clockText(g))}</div></div>`;
      return;
    }
    const { away, home } = teams(g);
    const scored = hasScore(g);
    const final = st === 'final' && scored;
    const line = (t, other) => {
      const lead = scored && t.score > other.score;
      const lost = final && t.score < other.score;
      return `<div class="sb-team${lost ? ' lost' : ''}">${logoHtml(t, '', 44)}
        <span class="name wide">${esc(shortName(t.name) || t.name)}</span>
        <span class="score cond${lead && st === 'live' ? ' lead' : ''}">${scored ? esc(t.score) : ''}</span></div>`;
    };
    const parts = [clockText(g)];
    if (g.broadcast) parts.push(g.broadcast.toUpperCase());
    $('scoreboard').innerHTML = `<div class="scoreboard">${line(away, home)}${line(home, away)}
      <div class="sb-status cond${st === 'live' ? ' live' : ''}">${esc(parts.join(' · '))}</div></div>`;
  }

  // Yard line 0-100 measured from the away team's end zone (drawn on the left).
  function fieldX(spot, g) {
    const m = String(spot || '').match(/([A-Za-z]{2,4})?\s*(\d{1,2})\s*$/);
    if (!m) return null;
    const n = Number(m[2]);
    if (n === 50 || !m[1]) return n;
    const away = (g.away_abbr || '').toLowerCase();
    return m[1].toLowerCase() === away ? n : 100 - n;
  }

  function renderSituation(g) {
    const box = $('situation');
    const sit = g.situation;
    if (gameState(g) !== 'live' || !sit || isChannel(g)) { box.innerHTML = ''; return; }
    const x = fieldX(sit.spot, g);
    const pct = (v) => (8 + 0.84 * Math.max(0, Math.min(100, v))) + '%';
    let first = '';
    let ball = '';
    if (x != null) {
      ball = `<span class="ball" style="left:${pct(x)}"></span>`;
      const dir = sit.possession === 'away' ? 1 : sit.possession === 'home' ? -1 : 0;
      const dm = String(sit.down_distance || '').match(/&\s*(\d+|goal)/i);
      if (dir && dm) {
        const target = /goal/i.test(dm[1]) ? (dir > 0 ? 100 : 0) : x + dir * Number(dm[1]);
        first = `<span class="first" style="left:${pct(target)}"></span>`;
      }
    }
    const { away, home } = teams(g);
    const ez = (side, color) => `<span class="ez" style="${side}:0;background:${esc(color || 'var(--raise)')}"></span>`;
    const holder = sit.possession ? (sit.possession === 'away' ? away : home) : null;
    const spotText = [sit.spot ? 'Ball on ' + sit.spot : '', holder ? shortName(holder.name) + ' ball' : ''].filter(Boolean).join(' · ');
    box.innerHTML = `<div class="situation">
      <div class="top"><span class="down wide">${esc(sit.down_distance || '')}${sit.red_zone ? ' <span class="tag tag-amber">RED ZONE</span>' : ''}</span>
        <span class="spot">${esc(spotText)}</span></div>
      <div class="field" aria-hidden="true">${ez('left', away.color)}${ez('right', home.color)}<span class="mid"></span>${first}${ball}</div>
      ${sit.last_play ? `<div class="last">${esc(sit.last_play)}</div>` : ''}
    </div>`;
  }

  function renderSources() {
    const box = $('sources');
    const g = state.byKey.get(state.currentKey);
    if (!g || playingKey !== state.currentKey) { box.innerHTML = ''; $('edgeNote').hidden = true; return; }
    if (!currentSources.length) { box.innerHTML = ''; $('edgeNote').hidden = true; return; }
    const slots = currentSources.length;
    let html = '';
    for (let i = 0; i < slots; i++) {
      const s = currentSources[i];
      if (!s) {
        html += `<button type="button" class="source-btn" disabled aria-hidden="true"><span class="n">—</span></button>`;
        continue;
      }
      const active = i === currentSourceIndex;
      const failed = failedSourceIndexes.has(i);
      const health = failed ? 'Failed' : active ? 'Playing' : 'Ready';
      html += `<button type="button" class="source-btn tap${failed ? ' failed' : ''}" role="radio" aria-checked="${active}" data-idx="${i}">
        <span class="n">SOURCE ${i + 1}</span><span class="h"><i></i>${health}</span></button>`;
    }
    box.innerHTML = `<h2 class="eyebrow">Sources</h2><div class="source-grid" role="radiogroup" aria-label="Sources">${html}</div>`;
    const note = $('edgeNote');
    note.hidden = !currentSources.length;
    note.textContent = `Running about ${LIVE_EDGE_CUSHION_SECONDS}s behind live for smoother playback. Tap LIVE to catch up, or switch sources if it stalls.`;
  }

  $('sources').addEventListener('click', (ev) => {
    const btn = ev.target.closest('.source-btn[data-idx]');
    if (!btn) return;
    logPlayback('');
    playSourceAtIndex(Number(btn.dataset.idx));
  });

  function parseStat(v) {
    const s = String(v == null ? '' : v).trim();
    let m = s.match(/^(\d+):(\d{2})$/);
    if (m) return Number(m[1]) * 60 + Number(m[2]);
    m = s.match(/^(\d+)-(\d+)$/);
    if (m) return Number(m[2]) ? Number(m[1]) / Number(m[2]) : 0;
    const n = parseFloat(s.replace(/,/g, ''));
    return Number.isFinite(n) ? n : 0;
  }

  function playsHtml(g, detail) {
    const plays = detail.plays || [];
    if (!plays.length) return `<div class="tab-empty">${gameState(g) === 'later' ? 'Plays show up here once the game starts.' : 'No plays reported yet.'}</div>`;
    const { away, home } = teams(g);
    return plays.map((p, i) => {
      const team = p.side === 'away' ? away : p.side === 'home' ? home : null;
      const tagCls = p.tag === 'TD' ? 'tag-accent' : p.tag === 'BIG PLAY' ? 'tag-amber' : 'tag-outline';
      const q = p.period ? (p.period > 4 ? 'OT' : 'Q' + p.period) : '';
      const score = p.away_score != null && p.home_score != null ? `<span class="pscore cond">${esc(p.away_score)}–${esc(p.home_score)}</span>` : '';
      const meta = (team && team.logo ? `<img src="${esc(team.logo)}" alt="" width="20" height="20" loading="lazy" />` : '') +
        (p.tag ? `<span class="tag ${tagCls}">${esc(p.tag)}</span>` : '') + score;
      return `<div class="play${i === 0 ? ' latest' : ''}">
        <span class="when cond">${esc(q)}<br />${esc(p.clock || '')}</span>
        <div class="body">${meta ? `<div class="meta">${meta}</div>` : ''}<div class="text">${esc(p.text)}</div></div></div>`;
    }).join('');
  }

  function statsHtml(g, detail) {
    const stats = detail.stats || [];
    if (!stats.length) return `<div class="tab-empty">${gameState(g) === 'later' ? 'Team stats show up here once the game starts.' : 'No stats reported yet.'}</div>`;
    const { away, home } = teams(g);
    const rows = stats.map(s => {
      const a = parseStat(s.away);
      const h = parseStat(s.home);
      const lowerWins = s.key === 'turnovers';
      const aLead = lowerWins ? a < h : a > h;
      const hLead = lowerWins ? h < a : h > a;
      const max = Math.max(a, h) || 1;
      return `<div class="stat"><div class="vals"><span class="v cond${aLead ? ' lead' : ''}">${esc(s.away)}</span>
        <span class="label">${esc(s.label)}</span><span class="v h cond${hLead ? ' lead' : ''}">${esc(s.home)}</span></div>
        <div class="bars"><span><b class="${aLead ? 'lead' : ''}" style="width:${(a / max) * 100}%"></b></span>
        <span><b class="${hLead ? 'lead' : ''}" style="width:${(h / max) * 100}%"></b></span></div></div>`;
    }).join('');
    return `<div class="stats"><div class="head">${logoHtml(away, '', 28)}<h2 class="eyebrow">Team stats</h2>${logoHtml(home, '', 28)}</div>${rows}</div>`;
  }

  function infoHtml(g, detail) {
    const rows = [];
    const row = (k, v, small) => { if (v) rows.push(`<div class="info-row"><span class="k">${esc(k)}</span><span class="v">${esc(v)}${small ? `<small>${esc(small)}</small>` : ''}</span></div>`); };
    row('Stadium', detail.venue || g.venue, detail.city);
    if (g.start_time) {
      const d = new Date(g.start_time);
      if (!Number.isNaN(d.getTime())) {
        row('Kickoff', d.toLocaleString(undefined, { weekday: 'long', hour: 'numeric', minute: '2-digit' }),
          d.toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' }));
      }
    } else if (g.kickoff_local) row('Kickoff', g.kickoff_local);
    row('TV', detail.broadcast || g.broadcast);
    row('Weather', detail.weather);
    if (detail.attendance) row('Attendance', Number(detail.attendance).toLocaleString());
    if (!rows.length) return '<div class="tab-empty">No game info for this listing.</div>';
    return `<div class="info">${rows.join('')}</div>`;
  }

  function renderTab() {
    const g = state.byKey.get(state.currentKey);
    const panel = $('tabpanel');
    document.querySelectorAll('.tabs [data-tab]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === state.tab)));
    if (!g) { panel.innerHTML = ''; return; }
    if (isChannel(g) || !g.espn_id) {
      panel.innerHTML = state.tab === 'info' ? infoHtml(g, {}) : '<div class="tab-empty">No play-by-play for this listing.</div>';
      return;
    }
    const cached = state.details.get(g.espn_id);
    if (!cached) { panel.innerHTML = '<div class="tab-empty">Loading game details…</div>'; return; }
    if (!cached.data) {
      panel.innerHTML = state.tab === 'info' ? infoHtml(g, {}) : '<div class="tab-empty">Game details are unavailable right now.</div>';
      return;
    }
    const render = { plays: playsHtml, stats: statsHtml, info: infoHtml }[state.tab];
    panel.innerHTML = render(g, cached.data);
  }

  document.querySelector('.tabs').addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-tab]');
    if (!btn) return;
    state.tab = btn.dataset.tab;
    renderTab();
  });

  let detailTimer = null;
  async function loadDetail(force) {
    clearTimeout(detailTimer);
    const g = state.byKey.get(state.currentKey);
    if (!g || !g.espn_id || isChannel(g)) return;
    const id = g.espn_id;
    const cached = state.details.get(id);
    if (!force && cached && Date.now() - cached.at < DETAIL_POLL_MS - 1000) {
      renderTab();
    } else {
      try {
        const res = await fetch('/api/game/' + encodeURIComponent(id), { cache: 'no-store', credentials: 'same-origin' });
        const body = await res.json();
        state.details.set(id, { at: Date.now(), data: res.ok && body.ok ? body.game : (cached && cached.data) || null });
      } catch (_) {
        state.details.set(id, { at: Date.now(), data: (cached && cached.data) || null });
      }
      if (state.currentKey && state.byKey.get(state.currentKey) === g) renderTab();
    }
    if (gameState(g) === 'live' && state.view === 'watch') {
      detailTimer = setTimeout(() => loadDetail(true), DETAIL_POLL_MS);
    }
  }

  function renderDock() {
    const others = state.games.filter(g => gameState(g) === 'live' && gameKey(g) !== state.currentKey);
    const dock = $('dock');
    dock.hidden = !state.currentKey || !others.length;
    $('dockList').innerHTML = others.map(g => {
      const scored = hasScore(g);
      const count = sourcesFor(g).length;
      return `<a class="dock-card tap" href="#/watch/${encodeURIComponent(gameKey(g))}">
        <span class="t">${esc(gameTitle(g))}</span>
        <span class="l cond">${scored ? esc(g.away_score + '–' + g.home_score + '  ') : ''}${esc(clockText(g))}</span>
        ${sourceLabel(count).replace('class="src', 'class="row-sub src')}</a>`;
    }).join('');
  }

  function renderWatch(isNewSelection) {
    const g = state.byKey.get(state.currentKey);
    if (!g) {
      $('watchTitle').textContent = '';
      $('watchScroll').hidden = true;
      $('dock').hidden = true;
      if (state.data && state.currentKey) {
        // The listing dropped off the catalog: stop and say so.
        stopPlayer();
        playingKey = null;
        showPlayerEmpty(null);
      } else if (!playingKey) {
        showPlayerEmpty(null);
      }
      return;
    }
    $('watchTitle').textContent = gameTitle(g);
    $('watchScroll').hidden = false;
    const fresh = isNewSelection || playingKey !== state.currentKey;
    if (fresh) {
      playGame(g);
      state.tab = isChannel(g) ? 'info' : (gameState(g) === 'later' ? 'info' : 'plays');
      $('watchScroll').scrollTop = 0;
    } else if (!currentSources.length && sourcesFor(g).length) {
      // A stream turned up while this game's Watch screen was open.
      playGame(g);
    } else {
      // Keep playing; just refresh the list of sources behind the strip.
      const fresh = sourcesFor(g);
      if (fresh.length > currentSources.length) currentSources = currentSources.concat(fresh.slice(currentSources.length));
    }
    renderScoreboard(g);
    renderSituation(g);
    renderSources();
    renderDock();
    renderTab();
    if (fresh) loadDetail(false);
  }

  // ---------- settings ----------
  const SETTINGS_TITLES = { root: 'Settings', account: 'Account', theme: 'Theme', feeds: 'Feeds' };

  function renderSettings() {
    const page = SETTINGS_TITLES[state.settingsPage] ? state.settingsPage : 'root';
    document.querySelectorAll('#settingsPane [data-page]').forEach(el => { el.hidden = el.dataset.page !== page; });
    $('settingsTitle').textContent = SETTINGS_TITLES[page];
    const back = $('settingsBack');
    back.href = page === 'root' ? '#/' : '#/settings';
    back.classList.toggle('to-games', page === 'root');
  }

  function applyTheme(key) {
    const btn = document.querySelector(`[data-theme-key="${CSS.escape(key)}"]`) || document.querySelector('[data-theme-key]');
    if (!btn) return;
    key = btn.dataset.themeKey;
    if (key === 'signal') delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = key;
    document.querySelectorAll('[data-theme-key]').forEach(b => b.setAttribute('aria-checked', String(b === btn)));
    $('themeName').textContent = btn.dataset.themeLabel;
  }

  $('themeGrid').addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-theme-key]');
    if (!btn) return;
    storageSet(localStorage, THEME_KEY, btn.dataset.themeKey);
    applyTheme(btn.dataset.themeKey);
  });

  document.querySelectorAll('[data-feed]').forEach(el => { el.textContent = window.location.origin + el.dataset.feed; });
  document.querySelectorAll('[data-copy]').forEach(btn => {
    btn.addEventListener('click', async () => {
      const ok = await copyText(window.location.origin + btn.dataset.copy);
      btn.textContent = ok ? 'COPIED' : 'COPY FAILED';
      btn.classList.toggle('done', ok);
      setTimeout(() => { btn.textContent = 'COPY'; btn.classList.remove('done'); }, 1500);
    });
  });

  const btnRescrape = $('btnRescrape');
  let rescrapePoll = null;

  function setRescrapeRunning(running, text) {
    if (!btnRescrape) return;
    btnRescrape.disabled = running;
    btnRescrape.innerHTML = running ? '<span class="spin"></span>' : 'REFRESH';
    if (text) $('rescrapeSub').textContent = text;
  }

  async function rescrape() {
    setRescrapeRunning(true, 'Starting…');
    try {
      const res = await fetch('/api/rescrape', { method: 'POST', headers: { 'X-CSRF-Token': CONFIG.csrf }, credentials: 'same-origin' });
      if (!res.ok) {
        setRescrapeRunning(false, res.status === 403 ? 'Refused. Reload the page and try again.' : 'Failed to start (HTTP ' + res.status + ').');
        return;
      }
    } catch (e) {
      setRescrapeRunning(false, 'Failed to start: ' + e);
      return;
    }
    clearInterval(rescrapePoll);
    let tries = 0;
    rescrapePoll = setInterval(async () => {
      tries += 1;
      try {
        const j = await (await fetch('/api/health?_=' + Date.now(), { cache: 'no-store' })).json();
        if (!j.rescrape || !j.rescrape.running) {
          clearInterval(rescrapePoll);
          await load();
          let msg = 'Done. The list is up to date.';
          if (j.rescrape && j.rescrape.last_error) msg = 'Refresh streams failed: ' + j.rescrape.last_error;
          // Say so explicitly — otherwise a scrape that resolved nothing
          // looks like the button simply did nothing.
          else if (j.rescrape && j.rescrape.last_kept_previous) msg = 'Found no playable streams, so the previous list was kept.';
          setRescrapeRunning(false, msg);
          toast(msg);
          return;
        }
        setRescrapeRunning(true, 'Running… ' + tries * 2 + 's');
      } catch (_) {}
      if (tries > 180) {
        clearInterval(rescrapePoll);
        setRescrapeRunning(false, 'Timed out. Check the crawler logs.');
      }
    }, 2000);
  }

  if (btnRescrape) btnRescrape.addEventListener('click', rescrape);

  // ---------- routing ----------
  function route() {
    const parts = (window.location.hash || '#/').replace(/^#\/?/, '').split('/');
    const prevView = state.view;
    const prevKey = state.currentKey;
    if (parts[0] === 'watch' && parts[1]) {
      state.view = 'watch';
      state.currentKey = decodeURIComponent(parts[1]);
    } else if (parts[0] === 'settings') {
      state.view = 'settings';
      state.settingsPage = parts[1] || 'root';
    } else {
      state.view = 'games';
    }
    app.dataset.view = state.view;
    // A phone shows one screen at a time; leaving Watch shouldn't leave
    // the game playing out of sight.
    const watchVisible = state.view === 'watch' || (isWide() && state.view === 'games');
    if (!watchVisible && !video.paused) video.pause();
    if (state.view === 'settings') renderSettings();
    if (state.view === 'watch') {
      renderWatch(prevKey !== state.currentKey || !playingKey);
      if (prevView !== 'watch') loadDetail(false);
    } else {
      clearTimeout(detailTimer);
    }
    document.querySelectorAll('.game-row').forEach(r => r.classList.toggle('active', r.dataset.key === state.currentKey));
    renderResume();
    if (prevView !== state.view) schedulePoll();
  }

  $('btnRetryPlayback').addEventListener('click', () => {
    const game = state.byKey.get(state.currentKey);
    if (game) playGame(game);
  });

  window.addEventListener('hashchange', route);
  wideQuery.addEventListener('change', route);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && Date.now() - state.loadedAt > FAST_POLL_MS) load();
  });
  setInterval(renderStatus, 1000);

  applyTheme(storageGet(localStorage, THEME_KEY) || 'signal');
  showPlayerEmpty(null);
  route();
  load();
})();
