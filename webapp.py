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

import ipaddress
import json
import logging
import os
import re
import socket
import threading
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests

import logsetup
import account_auth
import secrets
import time

try:
    import espn_schedule
except ImportError:
    espn_schedule = None  # type: ignore

try:
    from version import VERSION, BUILD_TIME
except ImportError:
    VERSION, BUILD_TIME = "0.0.0-dev", "unknown"

from flask import Flask, Response, jsonify, redirect, render_template, request, session, has_request_context

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/output"))
JSON_PATH = OUTPUT_DIR / "sundaysignal_streams.json"
#: Written on a crawl that resolved nothing, when the previous catalog was
#: kept. Without it, a crawler that runs but finds nothing is indistinguishable
#: from a stopped one, since the catalog's own timestamp stays frozen.
STATUS_PATH = OUTPUT_DIR / "last_scrape_status.json"
PORT = int(os.environ.get("WEB_PORT", os.environ.get("SERVE_PORT", "8765")))

#: When set, /api/rescrape requires this token (X-SundaySignal-Token header
#: or ?token=). Local administrator sessions can also run rescrapes.
ADMIN_TOKEN = os.environ.get("SUNDAYSIGNAL_ADMIN_TOKEN", "").strip()

PROXY_REFERER = os.environ.get("PROXY_REFERER", "https://iframe.st/")
PROXY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# ESPN team logo CDN (reliable PNGs). Abbreviations align with common NFL codes
# used by packages like react-nfl-logos / ESPN.
TEAM_LOGO_CDN = "https://a.espncdn.com/i/teamlogos/nfl/500/{abbr}.png"

# RedZone and NFL Network aren't teams, so team_abbr() never matches them —
# give each a fixed logo instead of the blank space a non-matchup listing
# otherwise gets. Self-hosted under static/ (rather than hotlinked to a
# third-party CDN) so the icon can't break out from under us again.
REDZONE_LOGO_URL = "/static/nlf_redzone.svg"
_REDZONE_RE = re.compile(r"red\s*zone", re.I)
NFL_NETWORK_LOGO_URL = "/static/nfl_network.svg"
_NFL_NETWORK_RE = re.compile(r"nfl\s*network", re.I)
# These channels' listings pair the channel name with a literal "Live"
# placeholder (e.g. "NFL RedZone vs Live") in the same "<a>-vs-<b>" slug
# shape real games use — collapse that down to just the channel name.
_LIVE_PLACEHOLDER_RE = re.compile(r"^live$", re.I)

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

logsetup.configure()
log = logging.getLogger(__name__)

app = Flask(__name__)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": PROXY_UA})

app.secret_key = os.environ.get("SUNDAYSIGNAL_SECRET_KEY") or account_auth.persisted_value(
    "flask_secret_key.txt", lambda: secrets.token_hex(32)
)
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("SUNDAYSIGNAL_SECURE_COOKIES") == "1",
                  MAX_CONTENT_LENGTH=1024 * 1024)


def current_user():
    account = account_auth.user(session.get("user_id"))
    if account and account_auth.secure_equal(session.get("auth_stamp", ""), account_auth.auth_stamp(account["id"])):
        return account
    return None


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]

app.jinja_env.globals["csrf_token"] = csrf_token


def require_account_page(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect("/login" if account_auth.initialized() else "/setup")
        return view(*args, **kwargs)
    return wrapped


def require_account_api(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user() and not (request.path == "/proxy" and _admin_authorized()):
            return jsonify({"ok": False, "error": "Login required"}), 401
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def protect_account_forms():
    if request.path == "/api/rescrape" and current_user():
        supplied_token = request.headers.get("X-SundaySignal-Token") or request.args.get("token") or ""
        if not (ADMIN_TOKEN and account_auth.secure_equal(supplied_token, ADMIN_TOKEN)):
            supplied_csrf = request.headers.get("X-CSRF-Token", "")
            if request.method != "POST" or not supplied_csrf or not account_auth.secure_equal(supplied_csrf, session.get("csrf", "")):
                return jsonify(ok=False, error="A POST with a valid form token is required"), 403
    protected = {"/setup", "/login", "/invite", "/logout", "/admin/users", "/device", "/account/password"}
    if request.method == "POST" and request.path in protected:
        supplied = request.form.get("csrf", "")
        if not supplied or not account_auth.secure_equal(supplied, session.get("csrf", "")):
            return Response("Invalid form token. Reload the page and try again.", status=400)

_rescrape_lock = threading.Lock()
_rescrape_state = {
    "running": False,
    "last_started": None,
    "last_finished": None,
    "last_error": None,
    # True when a scrape resolved nothing and the previous catalog was kept.
    "last_kept_previous": False,
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


def load_scrape_status() -> dict:
    """Sidecar status from the most recent crawl that resolved nothing."""
    try:
        return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


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
    """Split 'Team A vs Team B' into the legacy away/home fields.

    Not every listing is a real matchup — RedZone, NFL Network and similar
    whole-slate channels still come through the same "<a>-vs-<b>" URL shape
    the source sites use for actual games, so a "vs" survives the split
    even though neither side is a recognized NFL team. `is_matchup` flags
    that case so the UI can drop the "vs" wording and the team-logo divider
    instead of showing them for a channel that isn't two teams playing.
    """
    text = title or slug.replace("-", " ")
    parts = re.split(r"\s+vs\.?\s+", text, flags=re.I)
    away = parts[0].strip() if parts else ""
    home = parts[1].strip() if len(parts) > 1 else ""
    away_abbr = team_abbr(away)
    home_abbr = team_abbr(home)
    # Source titles often come directly from lowercase URL slugs. Normalize
    # recognized team names here so display does not depend on ESPN matching.
    # capitalize() keeps numeric nicknames such as 49ers correctly cased.
    if away_abbr:
        away = " ".join(word.capitalize() for word in away.split())
    if home_abbr:
        home = " ".join(word.capitalize() for word in home.split())
    is_matchup = bool(away_abbr and home_abbr)
    always_live = False
    if is_matchup:
        display_title = f"{away} vs {home}"
    elif not home:
        display_title = away
    elif _LIVE_PLACEHOLDER_RE.match(home):
        display_title = away
        always_live = True
    elif _LIVE_PLACEHOLDER_RE.match(away):
        display_title = home
        always_live = True
    elif away.strip().lower() == home.strip().lower():
        display_title = away
    else:
        display_title = f"{away} / {home}" if away and home else (away or home or text)
    away_logo = logo_url(away_abbr)
    if not is_matchup:
        if _REDZONE_RE.search(text):
            away_logo = REDZONE_LOGO_URL
        elif _NFL_NETWORK_RE.search(text):
            away_logo = NFL_NETWORK_LOGO_URL
    return {
        "away_team": away or None,
        "home_team": home or None,
        "away_abbr": away_abbr,
        "home_abbr": home_abbr,
        "away_logo": away_logo,
        "home_logo": logo_url(home_abbr),
        "is_matchup": is_matchup,
        "display_title": display_title,
        "always_live": always_live,
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
        "is_matchup": parsed["is_matchup"],
        "display_title": parsed["display_title"],
        "always_live": parsed["always_live"],
    }


#: Every open page polls /api/streams; share one ESPN scoreboard fetch per window.
SCOREBOARD_TTL_SECONDS = 15
_scoreboard_cache: dict = {"at": 0.0, "fetch": None, "events": []}
GAME_DETAIL_TTL_SECONDS = 20
_game_detail_cache: dict[str, tuple[float, dict]] = {}


def _scoreboard_events() -> list[dict]:
    fetch = espn_schedule.fetch_scoreboard
    cache = _scoreboard_cache
    now = time.monotonic()
    # Keyed on the function too, so a swapped-in fetcher never sees stale events.
    if cache["fetch"] is fetch and now - cache["at"] < SCOREBOARD_TTL_SECONDS:
        return cache["events"]
    events = fetch()
    cache.update(at=now, fetch=fetch, events=events)
    return events


def enrich_games(data: dict) -> dict:
    """Add team logos, ESPN schedule/status, relative play_url; sort live first."""
    events = []
    if espn_schedule is not None:
        try:
            events = _scoreboard_events()
        except Exception as e:
            log.warning("ESPN scoreboard fetch failed: %s", e)
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
            # re-derive display fields in case ESPN turned an unmatched
            # channel entry into a recognized matchup (or vice versa)
            away_or_home = g.get("away_team") or g.get("home_team")
            source_text = (
                f"{g.get('away_team') or ''} vs {g.get('home_team') or ''}".strip()
                if away_or_home
                else (g.get("title") or "")
            )
            refreshed = display_matchup(source_text, g.get("slug") or "")
            g.update(refreshed)
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
    """Prefer home logo (TV guide style); fall back to away.

    IPTV clients (TiviMate/VLC) fetch this outside the browser, so a
    self-hosted "/static/..." logo (RedZone, NFL Network) needs to be made
    absolute the same way proxy URLs are.
    """
    for key in ("home_logo", "away_logo"):
        u = (g.get(key) or "").strip()
        if u.startswith("http://") or u.startswith("https://"):
            return u
        if u.startswith("/"):
            return f"{public_base_url()}{u}"
    return ""


def iter_playable_streams(data: dict):
    """One IPTV row per resolved stream per game (all alternates) so a
    lagging or broken primary source has fallbacks right in the playlist."""
    for g in data.get("games") or []:
        media_list = [s["media_url"] for s in (g.get("streams") or []) if s.get("media_url")]
        if not media_list:
            continue
        title = _clean_match_title(g)
        kick = (g.get("kickoff_local") or "").strip()
        # Display name: match only; optional short time for upcoming
        base_label = title
        if kick and (g.get("status_state") or "") == "pre":
            base_label = f"{title} ({kick})"
        base_tvg_id = str(g.get("id") or g.get("espn_id") or title)
        multi = len(media_list) > 1
        for i, media in enumerate(media_list):
            yield {
                "game_title": title,
                "label": f"{base_label} (Source {i + 1})" if multi else base_label,
                "media_url": media,
                "tvg_id": f"{base_tvg_id}-alt{i}" if i > 0 else base_tvg_id,
                "group": _iptv_group(g),
                "logo": _iptv_logo(g),
                "away_logo": g.get("away_logo"),
                "home_logo": g.get("home_logo"),
                "source_index": i,
                "start_time": g.get("start_time"),
                "status_detail": g.get("status_detail"),
                "venue": g.get("venue"),
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

        # Must go through run_cycle, not crawl() + write: writing raw crawl
        # output here would skip the guard that keeps the last good catalog
        # when a scrape resolves nothing, wiping a working list of games.
        # force_retry=True: a manual rescrape is a deliberate "try again",
        # so give every mirror a fresh shot instead of honoring dead-host
        # entries that may just be a stale blip from an earlier cycle.
        result = scraper.run_cycle(str(OUTPUT_DIR), force_retry=True)
        _rescrape_state["last_kept_previous"] = result.get("kept_previous", False)
        _rescrape_state["last_finished"] = datetime.now(timezone.utc).isoformat()
        if result.get("kept_previous"):
            log.warning(
                "rescrape resolved no streams; kept previous catalog (%s playable)",
                result.get("previous_playable"),
            )
        else:
            log.info("rescrape wrote %s games → %s", result.get("game_count"), JSON_PATH)
    except Exception as e:
        _rescrape_state["last_error"] = str(e)
        _rescrape_state["last_finished"] = datetime.now(timezone.utc).isoformat()
        log.error("rescrape failed: %s", e)
    finally:
        _rescrape_state["running"] = False


@app.get("/api/health")
def health():
    data = load_data()
    return jsonify(
        {
            "ok": True,
            "service": "SundaySignal",
            "version": VERSION,
            "build_time": BUILD_TIME,
            "discovery_version": 1,
            "json_exists": JSON_PATH.exists(),
            "scraped_at": data.get("scraped_at"),
            "admin_token_configured": bool(ADMIN_TOKEN),
            # Distinguishes "crawler is running but finding nothing" from
            # "crawler is stopped" — the catalog's own timestamp only moves
            # on a successful write.
            "last_attempt": load_scrape_status(),
            "rescrape": dict(_rescrape_state),
        }
    )


@app.get("/api/streams")
@require_account_api
def api_streams():
    data = enrich_games(load_data())
    data["last_attempt"] = load_scrape_status()
    body = json.dumps(data, indent=2, ensure_ascii=False)
    return Response(
        body,
        mimetype="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


@app.get("/api/game/<espn_id>")
@require_account_api
def api_game(espn_id):
    """Venue, weather, recent plays and team stats for the Watch screen."""
    if espn_schedule is None or not re.fullmatch(r"\d{1,12}", espn_id):
        return jsonify(ok=False, error="Unknown game"), 404
    now = time.monotonic()
    cached = _game_detail_cache.get(espn_id)
    if cached and now - cached[0] < GAME_DETAIL_TTL_SECONDS:
        return jsonify(ok=True, game=cached[1])
    game = espn_schedule.fetch_summary(espn_id)
    if game is None:
        if cached:
            return jsonify(ok=True, game=cached[1], stale=True)
        return jsonify(ok=False, error="Game details unavailable"), 502
    if len(_game_detail_cache) > 64:
        _game_detail_cache.clear()
    _game_detail_cache[espn_id] = (now, game)
    return jsonify(ok=True, game=game)


def _admin_authorized() -> bool:
    account = current_user()
    if account and account["admin"]:
        return True
    supplied = request.headers.get("X-SundaySignal-Token") or request.args.get("token") or ""
    return bool(ADMIN_TOKEN and account_auth.secure_equal(supplied, ADMIN_TOKEN))


def require_feed_auth(view):
    """Allow a signed-in viewer or an IPTV token to fetch the feed."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user() and not _admin_authorized():
            return jsonify({"ok": False, "error": "Unauthorized"}), 401
        return view(*args, **kwargs)

    return wrapped


@app.post("/api/rescrape")
@app.get("/api/rescrape")
def api_rescrape():
    """Trigger a full crawl+resolve in the background."""
    if not _admin_authorized():
        log.warning("rejected unauthorized rescrape from %s", request.remote_addr)
        return jsonify({"ok": False, "status": "unauthorized"}), 403
    if _rescrape_state["running"]:
        return jsonify({"ok": True, "status": "already_running", **_rescrape_state})
    t = threading.Thread(target=_run_rescrape, name="rescrape", daemon=True)
    t.start()
    return jsonify({"ok": True, "status": "started", **_rescrape_state})


@app.get("/playlist.m3u")
@app.get("/playlist.m3u8")
@app.get("/api/playlist.m3u")
@require_feed_auth
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
        proxy = f"{base}/proxy?url={quote(item['media_url'], safe='')}" + _proxy_token_suffix()
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


def _proxy_token_suffix():
    if has_request_context() and ADMIN_TOKEN:
        supplied = request.headers.get("X-SundaySignal-Token") or request.args.get("token") or ""
        if account_auth.secure_equal(supplied, ADMIN_TOKEN):
            return "&token=" + quote(ADMIN_TOKEN, safe="")
    return ""


def _rewrite_playlist(body: bytes, base_url: str) -> bytes:
    text = body.decode("utf-8", errors="replace")
    out_lines = []
    for line in text.splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#"):
            def repl(m):
                u = m.group(1)
                abs_u = urljoin(base_url, u)
                return f'URI="/proxy?url={quote(abs_u, safe="")}{_proxy_token_suffix()}"'

            line = re.sub(r'URI="([^"]+)"', repl, line)
            out_lines.append(line)
            continue
        abs_u = urljoin(base_url, raw)
        out_lines.append(f"/proxy?url={quote(abs_u, safe='')}{_proxy_token_suffix()}")
    return ("\n".join(out_lines) + "\n").encode("utf-8")


def _is_safe_public_host(host: str) -> bool:
    """Allow any public host, but block SSRF into LAN/loopback/link-local/
    metadata ranges. Stream mirrors rotate domains constantly, so a static
    domain allowlist just goes stale (and a string match on the hostname was
    never a real security boundary anyway — attacker-controlled DNS can point
    any name at an internal IP). Checking the resolved IP is what actually
    matters here.
    """
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
    return True


@app.get("/proxy")
@require_account_api
def proxy():
    target = request.args.get("url") or ""
    target = unquote(target).strip()
    if not target.startswith(("http://", "https://")):
        return Response("invalid url", status=400)

    host = (urlparse(target).hostname or "").lower()
    if not _is_safe_public_host(host):
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


#: Accent colors offered under Settings → Theme (key, label, swatch).
THEMES = (
    ("signal", "Signal", "#ff6a13"),
    ("turf", "Turf", "#2fbf71"),
    ("ice", "Ice", "#3d9bff"),
    ("crimson", "Crimson", "#e5384a"),
    ("gold", "Gold", "#f2b705"),
    ("violet", "Violet", "#9b6bff"),
)


def account_page(title, mode, submit=None, **kwargs):
    csrf_token()  # every account page can POST, even when it renders no form
    return render_template("account.html", title=title, mode=mode, submit=submit or title,
                           app_version=VERSION, signed_in=current_user() is not None, **kwargs)


def sign_in(user_id):
    session.clear()
    session.permanent = True
    session["user_id"] = user_id
    session["auth_stamp"] = account_auth.auth_stamp(user_id)


@app.get("/")
@require_account_page
def index():
    account = current_user()
    is_admin = bool(account["admin"])
    user_count = pending_count = 0
    if is_admin:
        with account_auth.db() as conn:
            user_count, pending_count = conn.execute(
                "SELECT COUNT(*), SUM(password IS NULL AND active=1) FROM users").fetchone()
    # IPTV apps can't do a browser login, so an admin gets the playlist URL
    # with the automation token already on it.
    playlist = "/playlist.m3u"
    if is_admin and ADMIN_TOKEN:
        playlist += "?token=" + quote(ADMIN_TOKEN, safe="")
    if is_admin:
        playlist_note = None if ADMIN_TOKEN else "Set SUNDAYSIGNAL_ADMIN_TOKEN so IPTV apps that can't sign in can use this."
    else:
        playlist_note = "Works in this browser. IPTV apps need the token link from your admin."
    feeds = (
        ("IPTV playlist", playlist, playlist_note),
        ("JSON catalog", "/api/streams", None),
        ("Health", "/api/health", None),
    )
    return render_template(
        "index.html", app_version=VERSION, app_build_time=BUILD_TIME, account=account, is_admin=is_admin,
        user_count=user_count or 0, pending_count=pending_count or 0, themes=THEMES, feeds=feeds,
        csrf=csrf_token(), client_config={"csrf": csrf_token(), "isAdmin": is_admin, "version": VERSION},
    )


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if account_auth.initialized():
        return redirect("/login")
    # Generate the bootstrap token for operators to read from the persistent volume.
    account_auth.persisted_value("admin_setup_token.txt", lambda: secrets.token_urlsafe(32))
    error = None
    if request.method == "POST":
        if account_auth.throttled("setup:" + (request.remote_addr or "")):
            return Response("Too many attempts. Try again in 15 minutes.", status=429)
        try:
            sign_in(account_auth.setup(request.form.get("email", ""), request.form.get("password", ""), request.form.get("token", "")))
            return redirect("/admin/users")
        except ValueError as exc:
            error = str(exc)
    return account_page("Create administrator", "setup", submit="Create admin account", error=error,
        message="Use SUNDAYSIGNAL_SETUP_TOKEN, or read admin_setup_token.txt from the output volume.")


@app.route("/login", methods=["GET", "POST"])
def login():
    if not account_auth.initialized():
        return redirect("/setup")
    if current_user():
        return redirect("/")
    error = None
    if request.method == "POST":
        if account_auth.throttled("login:" + (request.remote_addr or "")):
            return Response("Too many attempts. Try again in 15 minutes.", status=429)
        user_id = account_auth.authenticate(request.form.get("email", ""), request.form.get("password", ""))
        if user_id:
            sign_in(user_id)
            return redirect("/")
        error = "Invalid email or password."
    return account_page("Sign in", "login", error=error)


@app.route("/invite", methods=["GET", "POST"])
def accept_invite():
    error = None
    if request.method == "POST":
        if account_auth.throttled("invite:" + (request.remote_addr or ""), 20):
            return Response("Too many attempts. Try again later.", status=429)
        try:
            sign_in(account_auth.accept(request.args.get("token", ""), request.form.get("password", "")))
            return redirect("/")
        except ValueError as exc:
            error = str(exc)
    info = account_auth.invite_info(request.args.get("token", ""))
    if not info and not error:
        error = "This invitation is invalid or expired."
    return account_page("Set your password", "invite", submit="Join SundaySignal" if not (info and info["reset"]) else "Save password",
                        error=error, invite_valid=info is not None, invite_email=(info or {}).get("email"),
                        invite_reset=bool(info and info["reset"]))


@app.route("/admin/users", methods=["GET", "POST"])
@require_account_page
def manage_users():
    if not current_user()["admin"]:
        return Response("Administrator access required.", status=403)
    error = invitation = invitation_email = flash = None
    invitation_kind = "Invite link ready"
    if request.method == "POST":
        try:
            action = request.form.get("action")
            if action:
                with account_auth.db() as conn:
                    target = conn.execute("SELECT * FROM users WHERE id=? AND admin=0", (request.form.get("user_id"),)).fetchone()
                    if not target:
                        raise ValueError("User not found.")
                    if action == "toggle":
                        conn.execute("UPDATE users SET active=1-active,session_version=session_version+1 WHERE id=?", (target["id"],))
                        conn.execute("DELETE FROM invites WHERE user_id=?", (target["id"],))
                        conn.execute("DELETE FROM devices WHERE user_id=?", (target["id"],))
                        if target["active"]:
                            flash = {"text": f"Disabled {target['email']}. They're signed out everywhere.", "undo": target["id"]}
                        else:
                            flash = {"text": f"Enabled {target['email']}.", "undo": None}
                if action == "invite":
                    invitation = request.url_root.rstrip("/") + "/invite?token=" + account_auth.invite(target["id"])
                    invitation_email = target["email"]
                    if target["password"]:
                        invitation_kind = "Password reset link"
            else:
                password = request.form.get("password") or None
                email = request.form.get("email", "")
                user_id = account_auth.create_user(email, password)
                if password is None:
                    invitation = request.url_root.rstrip("/") + "/invite?token=" + account_auth.invite(user_id)
                    invitation_email = account_auth.validate(email)
                else:
                    flash = {"text": f"Created {account_auth.validate(email)}. Share the starting password with them privately.", "undo": None}
        except ValueError as exc:
            error = str(exc)
    with account_auth.db() as conn:
        rows = conn.execute(
            "SELECT users.id,email,admin,active,password IS NULL AS pending,MAX(invites.expires) AS expires "
            "FROM users LEFT JOIN invites ON invites.user_id=users.id AND invites.expires>? "
            "GROUP BY users.id ORDER BY admin DESC,email", (time.time(),)).fetchall()
    users = []
    for row in rows:
        user = dict(row)
        expires = datetime.fromtimestamp(row["expires"]) if row["expires"] else None
        user["invite_expires"] = f"{expires:%b} {expires.day}" if expires else None
        users.append(user)
    return account_page("Manage users", "admin", users=users, me=current_user(), error=error, flash=flash,
                        invitation=invitation, invitation_email=invitation_email, invitation_kind=invitation_kind)


@app.post("/auth/device/pin")
def device_start():
    if account_auth.throttled("device-start:" + (request.remote_addr or ""), 30):
        return jsonify(ok=False, error="Too many attempts"), 429
    secret = secrets.token_urlsafe(32)
    code = secrets.token_hex(4).upper()
    with account_auth.db() as conn:
        conn.execute("DELETE FROM devices WHERE expires<?", (time.time(),))
        pin_id = conn.execute("INSERT INTO devices(secret,code,expires) VALUES (?,?,?)", (account_auth.digest(secret), code, time.time()+600)).lastrowid
    session["device_secret"] = secret
    return jsonify(ok=True, id=pin_id, code=code)


@app.get("/auth/device/poll/<int:pin_id>")
def device_poll(pin_id):
    with account_auth.db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        device = conn.execute("SELECT * FROM devices WHERE id=? AND expires>?", (pin_id, time.time())).fetchone()
        if not device or not account_auth.secure_equal(device["secret"], account_auth.digest(session.get("device_secret", ""))):
            return jsonify(ok=False, error="Code expired. Get a new code."), 400
        if not device["user_id"]:
            return jsonify(ok=True, authenticated=False)
        user_id = device["user_id"]
        conn.execute("DELETE FROM devices WHERE id=?", (pin_id,))
    if not account_auth.user(user_id):
        return jsonify(ok=False, error="Account disabled"), 403
    sign_in(user_id)
    return jsonify(ok=True, authenticated=True)


@app.route("/device", methods=["GET", "POST"])
@require_account_page
def device_approve():
    message = error = None
    if request.method == "POST":
        if account_auth.throttled("device-approve:" + str(current_user()["id"])):
            return Response("Too many attempts. Try again later.", status=429)
        with account_auth.db() as conn:
            changed = conn.execute("UPDATE devices SET user_id=? WHERE code=? AND expires>? AND user_id IS NULL", (current_user()["id"], request.form.get("code", "").strip().upper(), time.time())).rowcount
        if changed:
            message = "TV approved. You can return to your TV."
        else:
            error = "Invalid or expired code."
    return account_page("Approve TV sign-in", "device", submit="Approve TV", error=error, message=message)


@app.route("/account/password", methods=["GET", "POST"])
@require_account_page
def change_password():
    message = error = None
    if request.method == "POST":
        user_id = current_user()["id"]
        if account_auth.throttled("password:" + str(user_id)):
            return Response("Too many attempts. Try again later.", status=429)
        try:
            account_auth.change_password(user_id, request.form.get("current", ""), request.form.get("password", ""))
            # The new hash signs out every session, including this one; keep this one.
            sign_in(user_id)
            message = "Password changed. Other browsers and TVs are signed out."
        except ValueError as exc:
            error = str(exc)
    return account_page("Change password", "password", submit="Save new password", error=error, message=message)


@app.post("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.after_request
def cors(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Referrer-Policy"] = "same-origin"
    if request.path in {"/", "/login", "/setup", "/invite", "/admin/users", "/device", "/account/password"}:
        resp.headers["Cache-Control"] = "no-store"
    # Nothing here is meant to be found by search engines — the goal is
    # that a crawler indexing the site learns nothing about what's on it.
    # robots.txt covers well-behaved crawlers; this header is the same
    # instruction enforced server-side, for the ones that don't check it.
    resp.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"
    return resp


@app.get("/robots.txt")
def robots_txt():
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")


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
    log.info("SundaySignal server v%s (built %s) on http://0.0.0.0:%d/", VERSION, BUILD_TIME, PORT)
    log.info("JSON: /api/streams  M3U: /playlist.m3u  Rescrape: POST /api/rescrape")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
