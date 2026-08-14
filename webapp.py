#!/usr/bin/env python3
"""
SundaySignal web GUI + JSON API + IPTV M3U.

- GET  /                 → browser UI
- GET  /api/streams      → latest JSON (+ team logos, relative play_url)
- GET  /api/health       → health
- POST /api/rescrape     → run crawler now (background)
- GET  /playlist.m3u     → IPTV M3U (Host-based absolute proxy URLs)

No hardcoded LAN IPs. Browser uses relative /proxy paths.
M3U uses the request Host header (or optional PUBLIC_BASE_URL).
"""

from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests

try:
    import espn_schedule
except ImportError:
    espn_schedule = None  # type: ignore

from flask import Flask, Response, jsonify, render_template_string, request

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/output"))
JSON_PATH = OUTPUT_DIR / "sundaysignal_streams.json"
PORT = int(os.environ.get("WEB_PORT", os.environ.get("SERVE_PORT", "8765")))

PROXY_REFERER = os.environ.get("PROXY_REFERER", "https://iframe.st/")
PROXY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ESPN team logo CDN (reliable PNGs). Abbreviations align with common NFL codes
# used by packages like react-nfl-logos / ESPN.
TEAM_LOGO_CDN = "https://a.espncdn.com/i/teamlogos/nfl/500/{abbr}.png"

TEAM_ABBR = {
    "arizona cardinals": "ari",
    "cardinals": "ari",
    "atlanta falcons": "atl",
    "falcons": "atl",
    "baltimore ravens": "bal",
    "ravens": "bal",
    "buffalo bills": "buf",
    "bills": "buf",
    "carolina panthers": "car",
    "panthers": "car",
    "chicago bears": "chi",
    "bears": "chi",
    "cincinnati bengals": "cin",
    "bengals": "cin",
    "cleveland browns": "cle",
    "browns": "cle",
    "dallas cowboys": "dal",
    "cowboys": "dal",
    "denver broncos": "den",
    "broncos": "den",
    "detroit lions": "det",
    "lions": "det",
    "green bay packers": "gb",
    "packers": "gb",
    "houston texans": "hou",
    "texans": "hou",
    "indianapolis colts": "ind",
    "colts": "ind",
    "jacksonville jaguars": "jax",
    "jaguars": "jax",
    "kansas city chiefs": "kc",
    "chiefs": "kc",
    "las vegas raiders": "lv",
    "oakland raiders": "lv",
    "raiders": "lv",
    "los angeles chargers": "lac",
    "chargers": "lac",
    "los angeles rams": "lar",
    "rams": "lar",
    "miami dolphins": "mia",
    "dolphins": "mia",
    "minnesota vikings": "min",
    "vikings": "min",
    "new england patriots": "ne",
    "patriots": "ne",
    "new orleans saints": "no",
    "saints": "no",
    "new york giants": "nyg",
    "giants": "nyg",
    "new york jets": "nyj",
    "jets": "nyj",
    "philadelphia eagles": "phi",
    "eagles": "phi",
    "pittsburgh steelers": "pit",
    "steelers": "pit",
    "san francisco 49ers": "sf",
    "49ers": "sf",
    "niners": "sf",
    "seattle seahawks": "sea",
    "seahawks": "sea",
    "tampa bay buccaneers": "tb",
    "buccaneers": "tb",
    "bucs": "tb",
    "tennessee titans": "ten",
    "titans": "ten",
    "washington commanders": "wsh",
    "washington football team": "wsh",
    "washington redskins": "wsh",
    "commanders": "wsh",
}

app = Flask(__name__)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": PROXY_UA})

_rescrape_lock = threading.Lock()
_rescrape_state = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_error": None,
    "last_game_count": None,
}


def load_data() -> dict:
    if not JSON_PATH.exists():
        return {
            "scraped_at": None,
            "game_count": 0,
            "games": [],
            "message": "No scrape yet — wait for the crawler or click Rescrape.",
        }
    try:
        return json.loads(JSON_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        return {"scraped_at": None, "game_count": 0, "games": [], "message": str(e)}


def team_abbr(name: str) -> str | None:
    if not name:
        return None
    key = re.sub(r"\s+", " ", name.strip().lower())
    if key in TEAM_ABBR:
        return TEAM_ABBR[key]
    # partial match longest key
    best = None
    for k, v in TEAM_ABBR.items():
        if k in key or key in k:
            if best is None or len(k) > len(best[0]):
                best = (k, v)
    return best[1] if best else None


def logo_url(abbr: str | None) -> str | None:
    if not abbr:
        return None
    return TEAM_LOGO_CDN.format(abbr=abbr.lower())


def parse_matchup(title: str, slug: str = "") -> dict:
    """Split 'Team A vs Team B' into the legacy away/home fields."""
    text = title or slug.replace("-", " ")
    parts = re.split(r"\s+vs\.?\s+", text, flags=re.I)
    away = parts[0].strip() if parts else ""
    home = parts[1].strip() if len(parts) > 1 else ""
    away_abbr = team_abbr(away)
    home_abbr = team_abbr(home)
    return {
        "away_team": away or None,
        "home_team": home or None,
        "away_abbr": away_abbr,
        "home_abbr": home_abbr,
        "away_logo": logo_url(away_abbr),
        "home_logo": logo_url(home_abbr),
    }


def display_matchup(title: str, slug: str = "") -> dict:
    """Return team names and logos in the exact left-to-right title order."""
    parsed = parse_matchup(title, slug)
    return {
        "display_left_team": parsed["away_team"],
        "display_right_team": parsed["home_team"],
        "display_left_abbr": parsed["away_abbr"],
        "display_right_abbr": parsed["home_abbr"],
        "display_left_logo": parsed["away_logo"],
        "display_right_logo": parsed["home_logo"],
    }


def enrich_games(data: dict) -> dict:
    """Add team logos, ESPN schedule/status, relative play_url; sort live first."""
    events = []
    if espn_schedule is not None:
        try:
            events = espn_schedule.fetch_scoreboard()
        except Exception as e:
            print(f"[espn] {e}")
            events = []

    for g in data.get("games") or []:
        matchup = parse_matchup(g.get("title") or "", g.get("slug") or "")
        g.update(display_matchup(g.get("title") or "", g.get("slug") or ""))
        # only fill missing team fields so ESPN can override names later
        for k, v in matchup.items():
            if not g.get(k):
                g[k] = v
        if events and espn_schedule is not None:
            espn_schedule.enrich_game(g, events)
            # refresh logos if ESPN fixed team names
            if g.get("away_team"):
                ab = team_abbr(g["away_team"])
                g["away_abbr"] = ab
                g["away_logo"] = logo_url(ab)
            if g.get("home_team"):
                ab = team_abbr(g["home_team"])
                g["home_abbr"] = ab
                g["home_logo"] = logo_url(ab)
        for s in g.get("streams") or []:
            media = s.get("media_url")
            if media:
                s["play_url"] = f"/proxy?url={quote(media, safe='')}"

    games = data.get("games") or []
    if espn_schedule is not None:
        data["games"] = espn_schedule.sort_games_for_ui(games)
    else:
        data["games"] = games
    return data


def public_base_url() -> str:
    """Absolute base for IPTV clients only. Never hardcode LAN IP by default."""
    env = (os.environ.get("PUBLIC_BASE_URL") or "").rstrip("/")
    if env:
        return env
    host = request.host  # includes port if non-default
    # Prefer scheme client used (http on LAN)
    scheme = request.headers.get("X-Forwarded-Proto") or request.scheme or "http"
    return f"{scheme}://{host}"


def _clean_match_title(g: dict) -> str:
    """Human title for IPTV: 'Away @ Home' when known, else scraped title."""
    away = (g.get("away_team") or "").strip()
    home = (g.get("home_team") or "").strip()
    if away and home:
        return f"{away} @ {home}"
    title = (g.get("title") or g.get("slug") or "NFL Game").strip()
    # Drop "vs" noise already fine; strip long tails if any
    return title.split(" — ")[0].strip()


def _iptv_group(g: dict) -> str:
    state = (g.get("status_state") or "").lower()
    if state == "in" or g.get("live"):
        return "NFL · Live"
    if state == "post" or g.get("ended"):
        return "NFL · Final"
    if state == "pre":
        return "NFL · Upcoming"
    return "NFL"


def _iptv_logo(g: dict) -> str:
    """Prefer home logo (TV guide style); fall back to away."""
    for key in ("home_logo", "away_logo"):
        u = (g.get(key) or "").strip()
        if u.startswith("http://") or u.startswith("https://"):
            return u
    return ""


def iter_playable_streams(data: dict):
    """One IPTV row per game (first playable HLS), clean title, no provider noise."""
    for g in data.get("games") or []:
        media = None
        for s in g.get("streams") or []:
            if s.get("media_url"):
                media = s["media_url"]
                break
        if not media:
            continue
        title = _clean_match_title(g)
        kick = (g.get("kickoff_local") or "").strip()
        # Display name: match only; optional short time for upcoming
        label = title
        if kick and (g.get("status_state") or "") == "pre":
            label = f"{title} ({kick})"
        yield {
            "game_title": title,
            "label": label,
            "media_url": media,
            "tvg_id": str(g.get("id") or g.get("espn_id") or title),
            "group": _iptv_group(g),
            "logo": _iptv_logo(g),
            "away_logo": g.get("away_logo"),
            "home_logo": g.get("home_logo"),
        }


def _run_rescrape():
    global _rescrape_state
    with _rescrape_lock:
        if _rescrape_state["running"]:
            return
        _rescrape_state["running"] = True
        _rescrape_state["last_started"] = datetime.now(timezone.utc).isoformat()
        _rescrape_state["last_error"] = None

    try:
        # Import here so web server still starts if scraper deps missing in odd setups
        import sundaysignal_scraper as scraper

        data = scraper.crawl(resolve=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        JSON_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        _rescrape_state["last_game_count"] = data.get("game_count")
        _rescrape_state["last_finished"] = datetime.now(timezone.utc).isoformat()
        print(f"[rescrape] wrote {data.get('game_count')} games → {JSON_PATH}")
    except Exception as e:
        _rescrape_state["last_error"] = str(e)
        _rescrape_state["last_finished"] = datetime.now(timezone.utc).isoformat()
        print(f"[rescrape] error: {e}")
    finally:
        _rescrape_state["running"] = False


@app.get("/api/health")
def health():
    data = load_data()
    playable = sum(1 for _ in iter_playable_streams(data))
    return jsonify(
        {
            "ok": True,
            "service": "SundaySignal",
            "discovery_version": 1,
            "json_exists": JSON_PATH.exists(),
            "scraped_at": data.get("scraped_at"),
            "games": data.get("game_count", 0),
            "playable_streams": playable,
            "playlist": "/playlist.m3u",
            "rescrape": dict(_rescrape_state),
        }
    )


@app.get("/api/streams")
@app.get("/sundaysignal_streams.json")
def api_streams():
    data = enrich_games(load_data())
    body = json.dumps(data, indent=2, ensure_ascii=False)
    return Response(
        body,
        mimetype="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.post("/api/rescrape")
@app.get("/api/rescrape")
def api_rescrape():
    """Trigger a full crawl+resolve in the background."""
    if _rescrape_state["running"]:
        return jsonify({"ok": True, "status": "already_running", **_rescrape_state})
    t = threading.Thread(target=_run_rescrape, name="rescrape", daemon=True)
    t.start()
    return jsonify({"ok": True, "status": "started", **_rescrape_state})


@app.get("/playlist.m3u")
@app.get("/playlist.m3u8")
@app.get("/api/playlist.m3u")
def playlist_m3u():
    """
    Clean IPTV playlist for TiviMate / VLC / etc.

    Example:
      #EXTINF:-1 tvg-id="68490" tvg-name="Denver Broncos @ Atlanta Falcons"
        tvg-logo="https://a.espncdn.com/i/teamlogos/nfl/500/atl.png"
        group-title="NFL · Upcoming",Denver Broncos @ Atlanta Falcons
      http://host:8765/proxy?url=...
    """
    data = enrich_games(load_data())
    base = public_base_url()
    lines = [
        "#EXTM3U",
        "#EXTINF:-1,SundaySignal",
    ]
    count = 0
    for item in iter_playable_streams(data):
        proxy = f"{base}/proxy?url={quote(item['media_url'], safe='')}"
        name = item["label"].replace('"', "'")
        logo = (item.get("logo") or "").replace('"', "")
        attrs = [
            f'tvg-id="{item["tvg_id"]}"',
            f'tvg-name="{name}"',
            f'group-title="{item["group"]}"',
        ]
        if logo:
            attrs.append(f'tvg-logo="{logo}"')
        # Standard: attributes then comma + display name (match only)
        lines.append("#EXTINF:-1 " + " ".join(attrs) + f",{name}")
        lines.append(proxy)
        count += 1

    if count == 0:
        lines.append("#EXTINF:-1 group-title=\"NFL\",No games — open web UI and Rescrape")
        lines.append(f"{base}/api/health")

    body = "\n".join(lines) + "\n"
    return Response(
        body,
        mimetype="audio/x-mpegurl",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Content-Disposition": 'inline; filename="sundaysignal.m3u"',
        },
    )


def _is_playlist(content_type: str | None, body: bytes, url: str) -> bool:
    ct = (content_type or "").lower()
    if "mpegurl" in ct or "m3u" in ct:
        return True
    if url.rstrip("/").endswith(".m3u8"):
        return True
    head = body[:32].lstrip()
    return head.startswith(b"#EXTM3U")


def _rewrite_playlist(body: bytes, base_url: str) -> bytes:
    text = body.decode("utf-8", errors="replace")
    out_lines = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            def repl(m):
                u = m.group(1)
                abs_u = urljoin(base_url, u)
                return f'URI="/proxy?url={quote(abs_u, safe="")}"'

            line = re.sub(r'URI="([^"]+)"', repl, line)
            out_lines.append(line)
            continue
        abs_u = urljoin(base_url, raw)
        out_lines.append(f"/proxy?url={quote(abs_u, safe='')}")
    return ("\n".join(out_lines) + "\n").encode("utf-8")


@app.get("/proxy")
def proxy():
    target = request.args.get("url") or ""
    target = unquote(target).strip()
    if not target.startswith(("http://", "https://")):
        return Response("invalid url", status=400)

    host = (urlparse(target).hostname or "").lower()
    allowed_suffixes = (
        "fingersoon.st",
        "iframe.st",
        "cloudflarestorage.com",
        "r2.dev",
        "totalsporteks.st",
        "workers.dev",
    )
    if not any(host == s or host.endswith("." + s) for s in allowed_suffixes):
        data = load_data()
        known = set()
        for g in data.get("games") or []:
            for s in g.get("streams") or []:
                if s.get("media_url"):
                    known.add(urlparse(s["media_url"]).hostname or "")
        if host not in known and not any(host.endswith("." + k) for k in known if k):
            return Response(f"host not allowed: {host}", status=403)

    headers = {
        "User-Agent": PROXY_UA,
        "Referer": PROXY_REFERER,
        "Origin": "https://iframe.st",
        "Accept": "*/*",
    }
    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]
    try:
        r = SESSION.get(target, headers=headers, timeout=25, allow_redirects=True)
    except requests.RequestException as e:
        return Response(f"upstream error: {e}", status=502)

    final_host = (urlparse(r.url).hostname or "").lower()
    if "yahoo." in final_host or "google." in final_host:
        return Response(
            "upstream redirected away from media (dead stream or bad referer) — try Rescrape",
            status=502,
        )

    content = r.content
    ct = r.headers.get("Content-Type", "application/octet-stream")

    if _is_playlist(ct, content, target):
        content = _rewrite_playlist(content, r.url)
        ct = "application/vnd.apple.mpegurl"
    elif (
        len(content) > 376
        and content[0] == 0x47
        and content[188] == 0x47
        and content[376] == 0x47
    ):
        # Some providers disguise MPEG-TS segments as .txt/text/plain. Native
        # HLS players rely on the media Content-Type, so identify the transport
        # stream packet signature here.
        ct = "video/mp2t"
    elif len(content) > 8 and content[4:8] == b"ftyp":
        ct = "video/mp4"

    response_headers = {
        "Access-Control-Allow-Origin": "*",
        "Cache-Control": "no-cache",
    }
    if not _is_playlist(ct, content, target):
        for header_name in ("Content-Length", "Content-Range", "Accept-Ranges"):
            if r.headers.get(header_name):
                response_headers[header_name] = r.headers[header_name]

    return Response(
        content,
        status=r.status_code,
        mimetype=ct,
        headers=response_headers,
    )


UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SundaySignal</title>
  <meta name="theme-color" content="#112852" />
  <link rel="icon" href="/static/SundaySignalIcon.jpg" type="image/jpeg" />
  <script src="https://cdn.jsdelivr.net/npm/hls.js@1.5.15/dist/hls.min.js"></script>
  <style>
    :root {
      --bg: #071226;
      --panel: #0c1d3c;
      --header: #091831;
      --accent: #6ea8ff;
      --accent-hot: #ff6470;
      --text: #f7f9ff;
      --muted: #afc2e6;
      --border: rgba(255,255,255,0.10);
      --ok: #78d7b0;
      --card: #112852;
      --card-hover: #183a77;
      --card-active: #214a91;
      --sidebar-w: min(420px, 36vw);
    }
    * { box-sizing: border-box; }
    html { font-size: 16px; }
    body {
      margin: 0;
      font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background:
        radial-gradient(1200px 600px at 80% -10%, rgba(110,168,255,0.15), transparent 55%),
        var(--bg);
      color: var(--text);
      min-height: 100vh;
      line-height: 1.4;
      -webkit-font-smoothing: antialiased;
    }
    header {
      background: rgba(9,24,49,0.94);
      backdrop-filter: blur(10px);
      border-bottom: 1px solid var(--border);
      padding: 16px 22px;
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      align-items: center;
      justify-content: space-between;
      position: sticky;
      top: 0;
      z-index: 20;
    }
    .brand-lockup { display: flex; align-items: center; gap: 12px; color: var(--text); text-decoration: none; }
    .brand-logo {
      display: block;
      width: 46px;
      height: 46px;
      border-radius: 11px;
      object-fit: cover;
      box-shadow: 0 5px 18px rgba(0,0,0,0.28);
    }
    .brand-name { font-size: clamp(1.08rem, 1.7vw, 1.38rem); font-weight: 760; letter-spacing: -0.035em; }
    .meta {
      color: var(--muted);
      font-size: clamp(0.75rem, 1.1vw, 0.85rem);
      margin: 2px 0 0 58px;
      font-variant-numeric: tabular-nums;
    }
    .btn {
      background: var(--accent);
      color: #15180f;
      border: none;
      border-radius: 10px;
      padding: 10px 14px;
      cursor: pointer;
      font-weight: 700;
      font-size: 0.875rem;
      transition: transform 0.12s ease, filter 0.12s ease;
    }
    .btn:hover { filter: brightness(1.06); transform: translateY(-1px); }
    .btn:disabled { opacity: 0.55; cursor: wait; transform: none; }
    .btn.secondary {
      background: #112852;
      border: 1px solid var(--border);
      color: #e6edff;
    }
    .layout {
      display: grid;
      grid-template-columns: var(--sidebar-w) 1fr;
      min-height: calc(100vh - 88px);
    }
    .sidebar {
      background: rgba(12,29,60,0.96);
      border-right: 1px solid var(--border);
      overflow-y: auto;
      max-height: calc(100vh - 88px);
      padding: 18px 16px 28px;
    }
    .sidebar::before {
      content: "CHANNEL LIBRARY";
      display: block;
      margin: 2px 6px 14px;
      color: var(--muted);
      font-size: 0.7rem;
      font-weight: 700;
      letter-spacing: 0.12em;
    }
    .game {
      position: relative;
      display: flex;
      flex-direction: column;
      gap: 10px;
      border: 1px solid transparent;
      border-radius: 14px;
      padding: 14px 14px 12px;
      margin-bottom: 10px;
      background: var(--card);
      cursor: pointer;
      user-select: none;
      transition: background 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
    }
    .game:hover {
      background: var(--card-hover);
      border-color: rgba(255,255,255,0.08);
    }
    .game.active {
      background: var(--card-active);
      border-color: rgba(241,255,115,0.35);
      box-shadow: inset 3px 0 0 var(--accent);
    }
    .game.ended { opacity: 0.78; }
    .logos {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 16px;
      min-height: 64px;
      padding: 8px 10px;
      border-radius: 12px;
      background: linear-gradient(145deg, rgba(42,82,145,0.48), rgba(9,24,49,0.42));
    }
    .logos img {
      width: clamp(44px, 5.5vw, 56px);
      height: clamp(44px, 5.5vw, 56px);
      object-fit: contain;
      background: transparent;
      filter: drop-shadow(0 2px 4px rgba(0,0,0,0.4));
    }
    .logos .vs {
      color: var(--muted);
      font-size: 0.7rem;
      font-weight: 800;
      letter-spacing: 0.08em;
    }
    .game h3 {
      margin: 0;
      text-align: center;
      font-size: clamp(0.92rem, 1.35vw, 1.05rem);
      font-weight: 650;
      line-height: 1.3;
      color: #f7f9ff;
      padding: 0 4px;
    }
    .game-meta {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: center;
      gap: 6px;
      min-height: 24px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 9px;
      border-radius: 999px;
      background: #17366d;
      color: #d9e5ff;
      font-size: clamp(0.68rem, 1vw, 0.75rem);
      font-weight: 700;
      letter-spacing: 0.02em;
      white-space: nowrap;
      border: 1px solid transparent;
    }
    .pill svg { width: 12px; height: 12px; flex-shrink: 0; }
    .pill.live {
      background: rgba(241,255,115,0.12);
      color: #e8ff6a;
      border-color: rgba(241,255,115,0.22);
    }
    .pill.upcoming {
      background: #12345d;
      color: #cce0ff;
      border-color: rgba(110,168,255,0.22);
    }
    .pill.final {
      background: #14284c;
      color: #afc2e6;
    }
    .game .hint {
      margin: 0;
      text-align: center;
      font-size: clamp(0.7rem, 1vw, 0.78rem);
      color: var(--muted);
    }
    .main {
      padding: clamp(18px, 3vw, 34px);
      display: flex;
      flex-direction: column;
      gap: 14px;
    }
    .main::before {
      content: "WATCHING  /  SUNDAY SIGNAL";
      color: var(--muted);
      font-size: 0.7rem;
      font-weight: 700;
      letter-spacing: 0.1em;
    }
    .player-wrap {
      position: relative;
      width: 100%;
      aspect-ratio: 16 / 9;
      max-height: min(70vh, 720px);
      border: 1px solid rgba(255,255,255,0.12);
      border-radius: 16px;
      overflow: hidden;
      background: linear-gradient(135deg, #17386f, #112852 62%, #071226);
      box-shadow: 0 16px 55px rgba(0,0,0,0.28);
    }
    .player-wrap::before {
      content: "";
      position: absolute;
      inset: 0;
      pointer-events: none;
      background:
        radial-gradient(ellipse at 65% 37%, rgba(215,255,188,0.12), transparent 28%),
        linear-gradient(0deg, rgba(0,0,0,0.45), transparent 48%);
      z-index: 1;
    }
    video {
      position: relative;
      z-index: 2;
      width: 100%;
      height: 100%;
      background: #000;
      display: block;
    }
    .placeholder {
      position: absolute;
      inset: 0;
      z-index: 2;
      display: flex;
      justify-content: flex-start;
      align-items: flex-end;
      padding: 28px;
      color: #e4e8e2;
      font-size: clamp(1rem, 2vw, 1.25rem);
      font-weight: 650;
      pointer-events: none;
      background: linear-gradient(0deg, rgba(0,0,0,0.5), transparent 45%);
    }
    .placeholder.hidden { display: none; }
    .placeholder::before {
      content: "READY TO WATCH";
      position: absolute;
      left: 28px;
      bottom: 58px;
      color: var(--accent);
      font-size: 0.7rem;
      font-weight: 700;
      letter-spacing: 0.12em;
    }
    .player-toolbar {
      position: absolute;
      right: 16px;
      bottom: 58px;
      z-index: 6;
      display: none;
      gap: 8px;
    }
    .player-toolbar.visible { display: flex; }
    .live-btn {
      border: 1px solid rgba(241,255,115,0.4);
      background: rgba(25,31,22,0.93);
      color: var(--accent);
      font-weight: 800;
      font-size: 0.78rem;
      letter-spacing: 0.06em;
      border-radius: 999px;
      padding: 9px 14px;
      cursor: pointer;
    }
    .live-btn:hover { filter: brightness(1.12); }
    .info {
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 14px 16px;
      background: #0c1d3c;
      color: #afc2e6;
      font-size: clamp(0.82rem, 1.2vw, 0.92rem);
      line-height: 1.5;
    }
    .info strong { color: #f7f9ff; }
    .info code {
      background: #183a77;
      color: #edf3ff;
      padding: 2px 6px;
      border-radius: 4px;
      font-size: 0.85em;
      word-break: break-all;
    }
    .chain { margin-top: 8px; color: var(--muted); font-size: 0.9em; }
    .empty {
      color: var(--muted);
      padding: 28px 12px;
      text-align: center;
      border: 1px dashed rgba(255,255,255,0.12);
      border-radius: 12px;
      font-size: 0.9rem;
      line-height: 1.55;
    }
    @media (max-width: 960px) {
      .layout { grid-template-columns: 1fr; }
      .sidebar {
        max-height: 42vh;
        border-right: 0;
        border-bottom: 1px solid var(--border);
      }
      .meta { margin-left: 0; }
      .main { padding: 18px 16px 24px; }
      .player-wrap { max-height: 50vh; }
    }
    @media (max-width: 520px) {
      header { padding: 12px 14px; }
      .btn { padding: 9px 11px; font-size: 0.8rem; }
      .logos img { width: 48px; height: 48px; }
    }
</style>
</head>
<body>
  <header>
    <div>
      <a class="brand-lockup" href="/" aria-label="SundaySignal home">
        <img class="brand-logo" src="/static/SundaySignalIcon.jpg" alt="" />
        <span class="brand-name">SundaySignal</span>
      </a>
      <div class="meta" id="statusMeta">Loading…</div>
    </div>
    <div style="display:flex; gap:8px; flex-wrap:wrap;">
      <button class="btn" id="btnRescrape" type="button">Rescrape now</button>
      <button class="btn secondary" id="btnRefresh" type="button">Reload list</button>
      <button class="btn secondary" id="btnM3u" type="button">IPTV M3U</button>
      <button class="btn secondary" id="btnApi" type="button">JSON</button>
    </div>
  </header>

  <div class="layout">
    <aside class="sidebar" id="sidebar">
      <div class="empty">Loading games…</div>
    </aside>
    <section class="main">
      <div class="player-wrap">
        <video id="video" controls playsinline></video>
        <div class="placeholder" id="placeholder">Select a playable stream from the list</div>
        <div class="player-toolbar" id="playerToolbar">
          <button type="button" class="live-btn" id="btnLiveEdge" title="Jump to live edge">● LIVE</button>
        </div>
      </div>
      <div class="info" id="info">
        <strong>Tips</strong><br/>
        Streams expire — use <span class="badge">Rescrape now</span> to refresh HLS links from the configured source.
        The catalog reloads every 5 minutes while this tab is visible. This does not trigger a scrape.
        Playback uses relative <code>/proxy</code> (no hardcoded IP).
        IPTV: open <code>/playlist.m3u</code> from this same host in VLC / TiviMate.
      </div>
    </section>
  </div>

  <script>
    const sidebar = document.getElementById('sidebar');
    const statusMeta = document.getElementById('statusMeta');
    const video = document.getElementById('video');
    const placeholder = document.getElementById('placeholder');
    const info = document.getElementById('info');
    const btnRescrape = document.getElementById('btnRescrape');
    let hls = null;
    let data = null;
    let pollTimer = null;
    let rescrapePoll = null;

    function stopPlayer() {
      if (hls) { hls.destroy(); hls = null; }
      video.removeAttribute('src');
      video.load();
      if (typeof showLiveToolbar === 'function') showLiveToolbar(false);
    }

    const playerToolbar = document.getElementById('playerToolbar');
    const btnLiveEdge = document.getElementById('btnLiveEdge');

    function showLiveToolbar(show) {
      if (!playerToolbar) return;
      if (show) playerToolbar.classList.add('visible');
      else playerToolbar.classList.remove('visible');
    }

    function jumpToLiveEdge() {
      try {
        if (hls && hls.liveSyncPosition != null) {
          video.currentTime = hls.liveSyncPosition;
          video.play().catch(() => {});
          return;
        }
        // Native HLS (Safari) or VOD-style duration
        if (video.seekable && video.seekable.length > 0) {
          const end = video.seekable.end(video.seekable.length - 1);
          video.currentTime = Math.max(0, end - 0.5);
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

    if (btnLiveEdge) btnLiveEdge.addEventListener('click', (ev) => {
      ev.preventDefault();
      jumpToLiveEdge();
    });

    function playMedia(url, label, gameTitle) {
      stopPlayer();
      placeholder.classList.add('hidden');
      showLiveToolbar(true);
      info.innerHTML = `<strong>Now playing:</strong> ${escapeHtml(gameTitle)} — ${escapeHtml(label)}<br/>
        <div class="chain">Proxied HLS: <code>${escapeHtml(url)}</code></div>
        <div class="chain">Behind live? Use the <strong>● LIVE</strong> button on the player to jump to the edge.</div>
        <div class="chain">If this fails, click <strong>Rescrape now</strong> then try again.</div>`;

      if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = url;
        video.play().catch(() => {});
        // Auto-nudge toward live after metadata
        video.addEventListener('loadedmetadata', function onMeta() {
          video.removeEventListener('loadedmetadata', onMeta);
          setTimeout(jumpToLiveEdge, 400);
        });
        return;
      }
      if (window.Hls && Hls.isSupported()) {
        hls = new Hls({
          enableWorker: true,
          lowLatencyMode: true,
          liveSyncDurationCount: 3,
          liveMaxLatencyDurationCount: 6,
        });
        hls.loadSource(url);
        hls.attachMedia(video);
        hls.on(Hls.Events.MANIFEST_PARSED, () => {
          video.play().catch(() => {});
          setTimeout(jumpToLiveEdge, 500);
        });
        hls.on(Hls.Events.ERROR, (_, d) => {
          if (d.fatal) {
            info.innerHTML += `<div class="chain" style="color:#ec4750">HLS error: ${escapeHtml(String(d.type))} / ${escapeHtml(String(d.details))} — try Rescrape</div>`;
          }
        });
      } else {
        info.innerHTML += `<div class="chain" style="color:#ec4750">This browser cannot play HLS.</div>`;
      }
    }

    function escapeHtml(s) {
      return String(s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function formatClientDate(value) {
      if (!value) return 'Not yet';
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return String(value);
      return new Intl.DateTimeFormat(undefined, {
        weekday: 'short',
        month: 'short',
        day: 'numeric',
        year: 'numeric',
        hour: 'numeric',
        minute: '2-digit',
        timeZoneName: 'short'
      }).format(date);
    }

    function logoImg(url, alt) {
      if (!url) return '';
      return `<img src="${escapeHtml(url)}" alt="${escapeHtml(alt || '')}" loading="lazy" onerror="this.style.visibility='hidden'" />`;
    }

    const HD_ICON = `<svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <rect x="2" y="5" width="20" height="14" rx="2.5" stroke="currentColor" stroke-width="2"/>
      <path d="M7 9.5h2.2c1.1 0 1.9.7 1.9 1.75S10.3 13 9.2 13H7V9.5zm0 4.9h2.35M13.2 9.5H16c1.15 0 2 .75 2 1.9v1.2c0 1.15-.85 1.9-2 1.9h-2.8V9.5z" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;

    function firstPlayUrl(g) {
      for (const s of (g.streams || [])) {
        const media = s.play_url || (s.media_url ? ('/proxy?url=' + encodeURIComponent(s.media_url)) : null);
        if (media) return { media, name: s.name || 'Live' };
      }
      return null;
    }

    function render(payload) {
      data = payload;
      const games = (payload.games || []).filter(g => (g.streams || []).length > 0);
      const scraped = formatClientDate(payload.scraped_at);
      statusMeta.textContent = `Updated ${scraped}  ·  ${games.length} games  ·  catalog refresh 5m`;

      if (!games.length) {
        sidebar.innerHTML = `<div class="empty">No playable streams in the current file.<br/>Click <strong>Rescrape now</strong>. The crawler keeps the last good list if a scrape finds nothing.</div>`;
        return;
      }

      sidebar.innerHTML = '';
      games.forEach((g) => {
        const el = document.createElement('div');
        el.className = 'game';
        el.setAttribute('role', 'button');
        el.tabIndex = 0;
        const title = g.title || g.slug || 'Game';
        const leftTeam = g.display_left_team || g.away_team || '';
        const rightTeam = g.display_right_team || g.home_team || '';
        const play = firstPlayUrl(g);
        const when = g.kickoff_local || '';
        const state = g.status_state || (g.live ? 'in' : (g.ended ? 'post' : ''));
        let statusPill = `<span class="pill">${HD_ICON} HD</span>`;
        if (state === 'in' || g.live) {
          statusPill += `<span class="pill live">● LIVE</span>`;
          el.classList.add('is-live');
        } else if (state === 'post' || g.ended) {
          statusPill += `<span class="pill final">FINAL</span>`;
          el.classList.add('ended');
        } else if (state === 'pre') {
          statusPill += `<span class="pill upcoming">UPCOMING</span>`;
          el.classList.add('upcoming');
        }
        if (when) statusPill += `<span class="pill">${escapeHtml(when)}</span>`;
        const detail = g.status_detail && state === 'in' ? escapeHtml(g.status_detail) : '';

        el.innerHTML = `
          <div class="logos">
            ${logoImg(g.display_left_logo || g.away_logo, leftTeam)}
            <span class="vs">VS</span>
            ${logoImg(g.display_right_logo || g.home_logo, rightTeam)}
          </div>
          <h3>${escapeHtml(title)}</h3>
          <div class="game-meta">${statusPill}</div>
          ${detail ? `<div class="hint">${detail}</div>` : `<div class="hint">Click to watch</div>`}
`;

        const activate = () => {
          document.querySelectorAll('.game').forEach(x => x.classList.remove('active'));
          el.classList.add('active');
          if (!play) return;
          playMedia(play.media, 'HD Live', title);
        };
        el.addEventListener('click', activate);
        el.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); activate(); }
        });
        sidebar.appendChild(el);
      });
    }

    async function load() {
      try {
        const res = await fetch('/api/streams?_=' + Date.now(), { cache: 'no-store' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        const payload = await res.json();
        render(payload);
      } catch (e) {
        statusMeta.textContent = 'Failed to load API: ' + e;
        sidebar.innerHTML = `<div class="empty">Could not load /api/streams</div>`;
      }
    }

    async function rescrape() {
      btnRescrape.disabled = true;
      btnRescrape.textContent = 'Scraping…';
      statusMeta.textContent = 'Rescrape started — resolving fresh HLS links…';
      try {
        await fetch('/api/rescrape', { method: 'POST' });
      } catch (e) {
        statusMeta.textContent = 'Rescrape request failed: ' + e;
        btnRescrape.disabled = false;
        btnRescrape.textContent = 'Rescrape now';
        return;
      }
      if (rescrapePoll) clearInterval(rescrapePoll);
      let tries = 0;
      rescrapePoll = setInterval(async () => {
        tries += 1;
        try {
          const h = await fetch('/api/health?_=' + Date.now(), { cache: 'no-store' });
          const j = await h.json();
          if (!j.rescrape || !j.rescrape.running) {
            clearInterval(rescrapePoll);
            rescrapePoll = null;
            btnRescrape.disabled = false;
            btnRescrape.textContent = 'Rescrape now';
            await load();
            if (j.rescrape && j.rescrape.last_error) {
              statusMeta.textContent = 'Rescrape error: ' + j.rescrape.last_error;
            }
          } else {
            statusMeta.textContent = 'Rescrape still running… (' + tries + 's)';
          }
        } catch (_) {}
        if (tries > 180) {
          clearInterval(rescrapePoll);
          rescrapePoll = null;
          btnRescrape.disabled = false;
          btnRescrape.textContent = 'Rescrape now';
          statusMeta.textContent = 'Rescrape timed out — check crawler logs';
        }
      }, 2000);
    }

    document.getElementById('btnRefresh').addEventListener('click', load);
    btnRescrape.addEventListener('click', rescrape);
    document.getElementById('btnApi').addEventListener('click', () => window.open('/api/streams', '_blank'));
    document.getElementById('btnM3u').addEventListener('click', () => window.open('/playlist.m3u', '_blank'));

    load();
    pollTimer = setInterval(() => {
      if (document.visibilityState === 'visible') load();
    }, 300000);
  </script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(UI_HTML)


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if not JSON_PATH.exists():
        JSON_PATH.write_text(
            json.dumps(
                {
                    "scraped_at": None,
                    "game_count": 0,
                    "games": [],
                    "message": "No scrape yet — run the crawler or POST /api/rescrape",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    print(f"Web GUI + API on http://0.0.0.0:{PORT}/")
    print(f"JSON: /api/streams  M3U: /playlist.m3u  Rescrape: POST /api/rescrape")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
