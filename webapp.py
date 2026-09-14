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

import hmac
import ipaddress
import json
import logging
import os
import re
import socket
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse
from xml.sax.saxutils import escape as xml_escape

import requests

import logsetup

try:
    import espn_schedule
except ImportError:
    espn_schedule = None  # type: ignore

try:
    from version import VERSION, BUILD_TIME
except ImportError:
    VERSION, BUILD_TIME = "0.0.0-dev", "unknown"

from flask import Flask, Response, jsonify, render_template_string, request

OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/output"))
JSON_PATH = OUTPUT_DIR / "sundaysignal_streams.json"
#: Written on a crawl that resolved nothing, when the previous catalog was
#: kept. Without it, a crawler that runs but finds nothing is indistinguishable
#: from a stopped one, since the catalog's own timestamp stays frozen.
STATUS_PATH = OUTPUT_DIR / "last_scrape_status.json"
PORT = int(os.environ.get("WEB_PORT", os.environ.get("SERVE_PORT", "8765")))

#: When set, /api/rescrape requires this token (X-SundaySignal-Token header
#: or ?token=). Unset keeps the open LAN-trusted behavior.
ADMIN_TOKEN = os.environ.get("SUNDAYSIGNAL_ADMIN_TOKEN", "").strip()

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

logsetup.configure()
log = logging.getLogger(__name__)

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
    "last_playable": None,
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
    is_matchup = bool(away_abbr and home_abbr)
    if is_matchup or not home:
        display_title = text
    elif away.strip().lower() == home.strip().lower():
        display_title = away
    else:
        display_title = f"{away} / {home}" if away and home else (away or home or text)
    return {
        "away_team": away or None,
        "home_team": home or None,
        "away_abbr": away_abbr,
        "home_abbr": home_abbr,
        "away_logo": logo_url(away_abbr),
        "home_logo": logo_url(home_abbr),
        "is_matchup": is_matchup,
        "display_title": display_title,
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
    }


def enrich_games(data: dict) -> dict:
    """Add team logos, ESPN schedule/status, relative play_url; sort live first."""
    events = []
    if espn_schedule is not None:
        try:
            events = espn_schedule.fetch_scoreboard()
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
    """Prefer home logo (TV guide style); fall back to away."""
    for key in ("home_logo", "away_logo"):
        u = (g.get(key) or "").strip()
        if u.startswith("http://") or u.startswith("https://"):
            return u
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


def _playable_game_count(data: dict) -> int:
    """Distinct games with at least one playable stream — not the same as
    the number of playlist rows now that each game can contribute multiple
    alternate-source rows."""
    return sum(
        1 for g in (data.get("games") or [])
        if any(s.get("media_url") for s in g.get("streams") or [])
    )


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
        result = scraper.run_cycle(str(OUTPUT_DIR))
        _rescrape_state["last_game_count"] = result.get("game_count")
        _rescrape_state["last_kept_previous"] = result.get("kept_previous", False)
        _rescrape_state["last_playable"] = result.get("playable", 0)
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
    playable = _playable_game_count(data)
    return jsonify(
        {
            "ok": True,
            "service": "SundaySignal",
            "version": VERSION,
            "build_time": BUILD_TIME,
            "discovery_version": 1,
            "json_exists": JSON_PATH.exists(),
            "scraped_at": data.get("scraped_at"),
            "games": data.get("game_count", 0),
            "playable_streams": playable,
            "playlist": "/playlist.m3u",
            "epg": "/epg.xml",
            "rescrape_requires_token": bool(ADMIN_TOKEN),
            # Distinguishes "crawler is running but finding nothing" from
            # "crawler is stopped" — the catalog's own timestamp only moves
            # on a successful write.
            "last_attempt": load_scrape_status(),
            "rescrape": dict(_rescrape_state),
        }
    )


@app.get("/api/streams")
@app.get("/sundaysignal_streams.json")
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


def _rescrape_authorized() -> bool:
    if not ADMIN_TOKEN:
        return True
    supplied = request.headers.get("X-SundaySignal-Token") or request.args.get("token") or ""
    return hmac.compare_digest(supplied, ADMIN_TOKEN)


@app.post("/api/rescrape")
@app.get("/api/rescrape")
def api_rescrape():
    """Trigger a full crawl+resolve in the background."""
    if not _rescrape_authorized():
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
    # url-tvg / x-tvg-url let players auto-discover the guide; different
    # clients look for different one of the two.
    lines = [
        f'#EXTM3U url-tvg="{base}/epg.xml" x-tvg-url="{base}/epg.xml"',
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


#: Typical NFL broadcast window; ESPN gives a kickoff but no end time.
EPG_BLOCK_HOURS = float(os.environ.get("SUNDAYSIGNAL_EPG_BLOCK_HOURS", "3.5"))


def _xmltv_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S %z")


def _epg_window(start_iso: str | None) -> tuple[datetime, datetime]:
    """Programme start/stop for a game. Without a kickoff from ESPN, show a
    block around now so the channel isn't blank in the guide."""
    now = datetime.now(timezone.utc)
    start = None
    if start_iso:
        try:
            start = datetime.fromisoformat(str(start_iso).replace("Z", "+00:00"))
        except ValueError:
            start = None
    if start is None:
        start = now - timedelta(hours=1)
    return start, start + timedelta(hours=EPG_BLOCK_HOURS)


@app.get("/epg.xml")
@app.get("/api/epg.xml")
def epg_xml():
    """XMLTV guide matching the playlist's channel ids, so TiviMate/VLC can
    show a real programme grid instead of a bare channel list."""
    data = enrich_games(load_data())
    channels = []
    programmes = []

    for item in iter_playable_streams(data):
        chan_id = xml_escape(item["tvg_id"])
        name = xml_escape(item["label"])
        logo = (item.get("logo") or "").strip()

        chan = [f'  <channel id="{chan_id}">', f"    <display-name>{name}</display-name>"]
        if logo.startswith(("http://", "https://")):
            chan.append(f'    <icon src="{xml_escape(logo)}" />')
        chan.append("  </channel>")
        channels.append("\n".join(chan))

        start, stop = _epg_window(item.get("start_time"))
        desc_bits = [item["group"]]
        if item.get("status_detail"):
            desc_bits.append(str(item["status_detail"]))
        if item.get("venue"):
            desc_bits.append(str(item["venue"]))
        if item.get("source_index"):
            desc_bits.append(f"Alternate source {item['source_index'] + 1}")
        desc = xml_escape(" · ".join(b for b in desc_bits if b))

        programmes.append(
            "\n".join(
                [
                    f'  <programme start="{_xmltv_time(start)}" stop="{_xmltv_time(stop)}" channel="{chan_id}">',
                    f'    <title lang="en">{xml_escape(item["game_title"])}</title>',
                    f'    <desc lang="en">{desc}</desc>',
                    '    <category lang="en">Sports</category>',
                    "  </programme>",
                ]
            )
        )

    body = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<tv generator-info-name="SundaySignal {VERSION}">',
            *channels,
            *programmes,
            "</tv>",
        ]
    ) + "\n"

    return Response(
        body,
        mimetype="application/xml",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Content-Disposition": 'inline; filename="sundaysignal-epg.xml"',
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


UI_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>SundaySignal</title>
  <meta name="theme-color" content="#112852" />
  <link rel="icon" href="/static/sundaysignal_icon.jpg" type="image/jpeg" />
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
    /* An explicit display on a class beats the UA stylesheet's [hidden]
       rule, so .feed-row/.sources-row would stay visible when hidden. */
    [hidden] { display: none !important; }
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
    .version-badge {
      color: var(--muted);
      font-size: 0.68rem;
      font-weight: 700;
      letter-spacing: 0.03em;
      border: 1px solid var(--border);
      border-radius: 999px;
      padding: 2px 8px;
      cursor: default;
    }
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
    .pill.none {
      background: #1a2742;
      color: #8fa3c8;
      border-color: rgba(255,255,255,0.08);
    }
    .game.no-streams { opacity: 0.72; }
    .game.no-streams .logos { filter: grayscale(0.5); }
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
    .watching-label {
      color: var(--muted);
      font-size: 0.7rem;
      font-weight: 700;
      letter-spacing: 0.1em;
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 8px;
      min-height: 1em;
    }
    .watching-game {
      color: var(--text);
      font-size: 0.92rem;
      font-weight: 700;
      letter-spacing: -0.01em;
    }
    .watching-source { color: var(--accent); }
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
    .sources-row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .sources-row .sources-label {
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0.08em;
      margin-right: 2px;
    }
    .source-pill {
      border: 1px solid var(--border);
      background: var(--card);
      color: #d9e5ff;
      font-size: 0.78rem;
      font-weight: 700;
      border-radius: 999px;
      padding: 6px 12px;
      cursor: pointer;
    }
    .source-pill:hover { background: var(--card-hover); }
    .source-pill.active {
      background: var(--accent);
      color: #15180f;
      border-color: transparent;
    }
    .source-pill.failed {
      opacity: 0.5;
      text-decoration: line-through;
    }
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
    .header-actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .settings-panel {
      /* Anchored to <header> (position: sticky, so it's a containing
         block), not .header-actions — that box only wraps its buttons, so
         on a narrow screen where the header wraps to two rows it sits near
         the left edge, not the true right edge of the screen. Anchoring
         to right:0 on that box overflowed the panel off the left side of
         the viewport instead of hanging it under the Settings button. */
      position: absolute;
      top: calc(100% + 10px);
      right: 0;
      z-index: 40;
      width: min(420px, calc(100vw - 32px));
      max-height: calc(100vh - 90px);
      overflow-y: auto;
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 14px;
      box-shadow: 0 18px 50px rgba(0,0,0,0.45);
      padding: 14px;
      text-align: left;
    }
    .settings-title {
      color: var(--muted);
      font-size: 0.68rem;
      font-weight: 800;
      letter-spacing: 0.12em;
      margin: 2px 2px 10px;
    }
    .settings-title + .settings-title { margin-top: 16px; }
    .feed-row {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 8px;
      border-radius: 10px;
      background: var(--card);
      margin-bottom: 8px;
    }
    .feed-row:hover { background: var(--card-hover); }
    .feed-info { flex: 1; min-width: 0; }
    .feed-name { font-size: 0.88rem; font-weight: 700; color: var(--text); }
    .feed-path {
      font-size: 0.72rem;
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .feed-actions { display: flex; gap: 6px; flex-shrink: 0; }
    .mini-btn {
      background: #17366d;
      color: #e6edff;
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 6px 10px;
      font-size: 0.75rem;
      font-weight: 700;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
    }
    .mini-btn:hover { background: var(--card-active); }
    .settings-about {
      color: var(--muted);
      font-size: 0.75rem;
      line-height: 1.6;
      padding: 2px 2px 0;
    }
    .token-input {
      width: 100%;
      margin-top: 4px;
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--text);
      padding: 6px 8px;
      font-size: 0.78rem;
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
        <img class="brand-logo" src="/static/sundaysignal_icon.jpg" alt="" />
        <span class="brand-name">SundaySignal</span>
        <span class="version-badge" title="{% if app_build_time and app_build_time != 'unknown' %}Built {{ app_build_time }}{% else %}Build time unavailable (not a Docker build){% endif %}">v{{ app_version }}</span>
      </a>
      <div class="meta" id="statusMeta">Loading…</div>
    </div>
    <div class="header-actions">
      <button class="btn" id="btnRefresh" type="button">Reload list</button>
      <button class="btn secondary" id="btnSettings" type="button" aria-haspopup="true" aria-expanded="false">⚙ Settings</button>

      <div class="settings-panel" id="settingsPanel" hidden>
        <div class="settings-title">FEEDS &amp; INTEGRATIONS</div>

        <div class="feed-row">
          <div class="feed-info">
            <div class="feed-name">IPTV playlist</div>
            <div class="feed-path" data-path="/playlist.m3u">/playlist.m3u</div>
          </div>
          <div class="feed-actions">
            <button class="mini-btn" type="button" data-copy="/playlist.m3u">Copy</button>
            <a class="mini-btn" href="/playlist.m3u" target="_blank" rel="noopener">Open</a>
          </div>
        </div>

        <div class="feed-row">
          <div class="feed-info">
            <div class="feed-name">TV guide (XMLTV)</div>
            <div class="feed-path" data-path="/epg.xml">/epg.xml</div>
          </div>
          <div class="feed-actions">
            <button class="mini-btn" type="button" data-copy="/epg.xml">Copy</button>
            <a class="mini-btn" href="/epg.xml" target="_blank" rel="noopener">Open</a>
          </div>
        </div>

        <div class="feed-row">
          <div class="feed-info">
            <div class="feed-name">Stream catalog (JSON)</div>
            <div class="feed-path" data-path="/api/streams">/api/streams</div>
          </div>
          <div class="feed-actions">
            <button class="mini-btn" type="button" data-copy="/api/streams">Copy</button>
            <a class="mini-btn" href="/api/streams" target="_blank" rel="noopener">Open</a>
          </div>
        </div>

        <div class="feed-row">
          <div class="feed-info">
            <div class="feed-name">Health check</div>
            <div class="feed-path" data-path="/api/health">/api/health</div>
          </div>
          <div class="feed-actions">
            <button class="mini-btn" type="button" data-copy="/api/health">Copy</button>
            <a class="mini-btn" href="/api/health" target="_blank" rel="noopener">Open</a>
          </div>
        </div>

        <div class="settings-title">ADMIN</div>

        <div class="feed-row">
          <div class="feed-info">
            <div class="feed-name">Rescrape now</div>
            <div class="feed-path">Re-resolve stream links from the source</div>
          </div>
          <div class="feed-actions">
            <button class="mini-btn" type="button" id="btnRescrape">Run</button>
          </div>
        </div>

        <div class="feed-row" id="adminTokenRow" hidden>
          <div class="feed-info">
            <div class="feed-name">Admin token</div>
            <input class="token-input" type="password" id="adminToken" placeholder="Required to rescrape" autocomplete="off" />
          </div>
          <div class="feed-actions">
            <button class="mini-btn" type="button" id="btnSaveToken">Save</button>
          </div>
        </div>

        <div class="settings-title">ABOUT</div>
        <div class="settings-about">
          SundaySignal <strong>v{{ app_version }}</strong><br/>
          {% if app_build_time and app_build_time != "unknown" %}Built {{ app_build_time }}<br/>{% endif %}
          Paste the playlist URL into VLC or TiviMate; add the XMLTV URL as the guide source.
        </div>
      </div>
    </div>
  </header>

  <div class="layout">
    <aside class="sidebar" id="sidebar">
      <div class="empty">Loading games…</div>
    </aside>
    <section class="main">
      <div class="watching-label" id="watchingLabel" hidden></div>
      <div class="player-wrap">
        <video id="video" controls playsinline></video>
        <div class="placeholder" id="placeholder">Select a playable stream from the list</div>
        <div class="player-toolbar" id="playerToolbar">
          <button type="button" class="live-btn" id="btnLiveEdge" title="Jump to live edge">● LIVE</button>
        </div>
      </div>
      <div class="sources-row" id="sourcesRow" hidden></div>
      <div class="info" id="info">
        <strong>Tips</strong><br/>
        Games with more than one working stream show a <strong>Sources</strong> row above — switch if one starts lagging,
        and playback falls back to the next source automatically if one dies.
        The catalog reloads every 5 minutes while this tab is visible; this does not trigger a scrape.
        Playback uses relative <code>/proxy</code> (no hardcoded IP).
        IPTV playlist, TV guide URLs and <strong>Rescrape</strong> are under <strong>⚙ Settings</strong>.
        A rescrape only ever adds streams — it can't remove ones that still work.
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

    if (btnLiveEdge) btnLiveEdge.addEventListener('click', (ev) => {
      ev.preventDefault();
      jumpToLiveEdge();
    });

    let playGeneration = 0;

    // How far behind the true live edge playback deliberately sits. These
    // are scraped third-party mirrors, not a broadcast-grade low-latency
    // origin, so a small cushion isn't enough to absorb a normal blip —
    // riding 20s back gives hls.js a real buffer to draw from instead of
    // stalling the moment a segment fetch is slow.
    const LIVE_EDGE_CUSHION_SECONDS = 20;

    function playMedia(url, label, gameTitle) {
      stopPlayer();
      const myGeneration = ++playGeneration;
      placeholder.classList.add('hidden');
      showLiveToolbar(true);
      // Providers often label a stream "unknown"; don't print that at people.
      const named = label && !/^(unknown|live)$/i.test(String(label).trim());
      info.innerHTML = `<strong>Now playing:</strong> ${escapeHtml(gameTitle)}${named ? ' — ' + escapeHtml(label) : ''}<br/>
        <div class="chain">Proxied HLS: <code>${escapeHtml(url)}</code></div>
        <div class="chain">Playback deliberately sits ~${LIVE_EDGE_CUSHION_SECONDS}s behind live for a stutter-resistant buffer. Fallen further behind? Use the <strong>● LIVE</strong> button to catch back up.</div>
        <div class="chain">Lagging or broken? Pick another source above, or run <strong>Rescrape</strong> from <strong>⚙ Settings</strong>.</div>`;

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
          // These are scraped third-party mirrors, not real low-latency
          // HLS — chasing the live edge just means playing right up
          // against whatever's already downloaded, so a normal network
          // hiccup empties the buffer and stalls playback. A fixed
          // seconds-based target (rather than a segment-count one) holds
          // steady regardless of how long this mirror's segments are.
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

    function sourcesFor(g) {
      return (g.streams || [])
        .map(s => ({
          media: s.play_url || (s.media_url ? ('/proxy?url=' + encodeURIComponent(s.media_url)) : null),
          name: s.name || 'Live',
        }))
        .filter(s => s.media);
    }

    const sourcesRow = document.getElementById('sourcesRow');
    let currentSources = [];
    let currentSourceIndex = -1;
    let currentGameTitle = '';
    let failedSourceIndexes = new Set();

    function renderSourcesRow() {
      if (!sourcesRow) return;
      if (currentSources.length <= 1) {
        sourcesRow.hidden = true;
        sourcesRow.innerHTML = '';
        return;
      }
      sourcesRow.hidden = false;
      sourcesRow.innerHTML = '<span class="sources-label">SOURCES</span>' + currentSources.map((s, i) => {
        const cls = ['source-pill'];
        if (i === currentSourceIndex) cls.push('active');
        if (failedSourceIndexes.has(i)) cls.push('failed');
        return `<button type="button" class="${cls.join(' ')}" data-idx="${i}">Source ${i + 1}</button>`;
      }).join('');
    }

    if (sourcesRow) sourcesRow.addEventListener('click', (ev) => {
      const btn = ev.target.closest('.source-pill');
      if (!btn) return;
      playSourceAtIndex(Number(btn.dataset.idx));
    });

    const watchingLabel = document.getElementById('watchingLabel');

    function setWatchingLabel(gameTitle, idx) {
      if (!watchingLabel) return;
      const source = currentSources[idx] || {};
      // The stream's own name is often junk ("unknown"), so lead with the
      // source number the pills use and only add a name when it says something.
      const name = (source.name || '').trim();
      const useful = name && !/^(unknown|live)$/i.test(name);
      watchingLabel.innerHTML =
        `WATCHING /<span class="watching-game">${escapeHtml(gameTitle)}</span>` +
        `<span class="watching-source">Source ${idx + 1}${useful ? ' · ' + escapeHtml(name) : ''}</span>`;
      watchingLabel.hidden = false;
    }

    function clearWatchingLabel() {
      if (!watchingLabel) return;
      watchingLabel.innerHTML = '';
      watchingLabel.hidden = true;
    }

    function playSourceAtIndex(idx) {
      if (idx < 0 || idx >= currentSources.length) return;
      currentSourceIndex = idx;
      renderSourcesRow();
      setWatchingLabel(currentGameTitle, idx);
      playMedia(currentSources[idx].media, currentSources[idx].name, currentGameTitle);
    }

    function tryNextSource(reason) {
      failedSourceIndexes.add(currentSourceIndex);
      const nextIdx = currentSources.findIndex((_, i) => i > currentSourceIndex && !failedSourceIndexes.has(i));
      if (nextIdx === -1) {
        renderSourcesRow();
        info.innerHTML += `<div class="chain" style="color:#ec4750">${escapeHtml(reason)} — no more alternate sources for this game. Try Rescrape.</div>`;
        return;
      }
      info.innerHTML += `<div class="chain" style="color:#ec4750">${escapeHtml(reason)} — switching to Source ${nextIdx + 1}…</div>`;
      playSourceAtIndex(nextIdx);
    }

    function playGame(g, title) {
      currentSources = sourcesFor(g);
      currentGameTitle = title;
      failedSourceIndexes = new Set();
      currentSourceIndex = -1;
      if (!currentSources.length) {
        renderSourcesRow();
        clearWatchingLabel();
        return;
      }
      playSourceAtIndex(0);
    }

    function render(payload) {
      data = payload;
      // Every scheduled game is listed, whether or not a stream resolved for
      // it — the schedule decides the lineup, scraping only fills in streams.
      const games = payload.games || [];
      const withStreams = games.filter(g => (g.streams || []).length > 0).length;
      const scraped = formatClientDate(payload.scraped_at);
      let status = `Updated ${scraped}  ·  ${games.length} games, ${withStreams} with streams  ·  catalog refresh 5m`;
      // The catalog timestamp only moves on a successful write, so without
      // this a crawler that runs but finds nothing looks like a dead one.
      const attempt = payload.last_attempt || {};
      if (attempt.kept_previous && attempt.scraped_at && attempt.scraped_at !== payload.scraped_at) {
        status += `  ·  last attempt ${formatClientDate(attempt.scraped_at)} found no streams (showing previous list)`;
      }
      statusMeta.textContent = status;

      if (!games.length) {
        sidebar.innerHTML = `<div class="empty">No games listed yet.<br/>The schedule may be unreachable — check the crawler logs, or run <strong>Rescrape</strong> from <strong>⚙ Settings</strong>.</div>`;
        return;
      }

      sidebar.innerHTML = '';
      games.forEach((g) => {
        const el = document.createElement('div');
        el.className = 'game';
        el.setAttribute('role', 'button');
        el.tabIndex = 0;
        // display_title drops the "vs" wording for a listing that isn't
        // really two teams playing (RedZone, NFL Network, etc. still arrive
        // through the same "<a>-vs-<b>" URL shape the source sites use for
        // real games).
        const title = g.display_title || g.title || g.slug || 'Game';
        const isMatchup = g.is_matchup !== false;
        const leftTeam = g.display_left_team || g.away_team || '';
        const rightTeam = g.display_right_team || g.home_team || '';
        const when = g.kickoff_local || '';
        const state = g.status_state || (g.live ? 'in' : (g.ended ? 'post' : ''));
        const isFinal = state === 'post' || g.ended;
        const streamCount = (g.streams || []).length;
        if (!streamCount) el.classList.add('no-streams');
        // Only claim a stream exists when one actually does; a finished
        // game missing a stream isn't "not yet" anymore, so say nothing.
        let statusPill = streamCount
          ? `<span class="pill">${HD_ICON} HD</span>`
          : (isFinal ? '' : `<span class="pill none">NO STREAM YET</span>`);
        if (state === 'in' || g.live) {
          statusPill += `<span class="pill live">● LIVE</span>`;
          el.classList.add('is-live');
        } else if (isFinal) {
          statusPill += `<span class="pill final">FINAL</span>`;
          el.classList.add('ended');
        } else if (state === 'pre') {
          statusPill += `<span class="pill upcoming">UPCOMING</span>`;
          el.classList.add('upcoming');
        }
        if (when) statusPill += `<span class="pill">${escapeHtml(when)}</span>`;
        const detail = g.status_detail && state === 'in' ? escapeHtml(g.status_detail) : '';
        let hint;
        if (detail) hint = detail;
        else if (streamCount) hint = 'Click to watch';
        else if (isFinal) hint = 'No stream was found for this game';
        else hint = 'Waiting for a stream';

        el.innerHTML = `
          <div class="logos">
            ${logoImg(g.display_left_logo || g.away_logo, leftTeam)}
            ${isMatchup ? '<span class="vs">VS</span>' : ''}
            ${isMatchup ? logoImg(g.display_right_logo || g.home_logo, rightTeam) : ''}
          </div>
          <h3>${escapeHtml(title)}</h3>
          <div class="game-meta">${statusPill}</div>
          <div class="hint">${hint}</div>
`;

        const activate = () => {
          document.querySelectorAll('.game').forEach(x => x.classList.remove('active'));
          el.classList.add('active');
          if (!streamCount) {
            stopPlayer();
            placeholder.classList.remove('hidden');
            currentSources = [];
            currentSourceIndex = -1;
            renderSourcesRow();
            clearWatchingLabel();
            const noStreamMsg = isFinal
              ? 'No stream was found for this game before it ended.'
              : 'No stream has resolved for this game yet. It stays listed either way — the crawler will pick one up when a source publishes it.';
            info.innerHTML = `<strong>${escapeHtml(title)}</strong><br/>
              <div class="chain">${noStreamMsg}</div>`;
            return;
          }
          playGame(g, title);
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

    const TOKEN_KEY = 'sundaysignal.adminToken';

    function storedToken() {
      try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (_) { return ''; }
    }

    async function rescrape() {
      btnRescrape.disabled = true;
      btnRescrape.textContent = 'Running…';
      statusMeta.textContent = 'Rescrape started — resolving fresh HLS links…';
      try {
        const headers = {};
        const token = storedToken();
        if (token) headers['X-SundaySignal-Token'] = token;
        const res = await fetch('/api/rescrape', { method: 'POST', headers });
        if (res.status === 403) {
          statusMeta.textContent = 'Rescrape refused — enter a valid admin token under ⚙ Settings.';
          btnRescrape.disabled = false;
          btnRescrape.textContent = 'Run';
          setSettingsOpen(true);
          return;
        }
      } catch (e) {
        statusMeta.textContent = 'Rescrape request failed: ' + e;
        btnRescrape.disabled = false;
        btnRescrape.textContent = 'Run';
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
            btnRescrape.textContent = 'Run';
            await load();
            if (j.rescrape && j.rescrape.last_error) {
              statusMeta.textContent = 'Rescrape error: ' + j.rescrape.last_error;
            } else if (j.rescrape && j.rescrape.last_kept_previous) {
              // Say so explicitly — otherwise a scrape that resolved nothing
              // looks like the button simply did nothing.
              statusMeta.textContent =
                'Rescrape found no playable streams — kept the previous list. The source site may be down or changed.';
            }
          } else {
            statusMeta.textContent = 'Rescrape still running… (' + tries + 's)';
          }
        } catch (_) {}
        if (tries > 180) {
          clearInterval(rescrapePoll);
          rescrapePoll = null;
          btnRescrape.disabled = false;
          btnRescrape.textContent = 'Run';
          statusMeta.textContent = 'Rescrape timed out — check crawler logs';
        }
      }, 2000);
    }

    document.getElementById('btnRefresh').addEventListener('click', load);
    btnRescrape.addEventListener('click', rescrape);

    const btnSettings = document.getElementById('btnSettings');
    const settingsPanel = document.getElementById('settingsPanel');

    function setSettingsOpen(open) {
      settingsPanel.hidden = !open;
      btnSettings.setAttribute('aria-expanded', open ? 'true' : 'false');
    }

    btnSettings.addEventListener('click', (ev) => {
      ev.stopPropagation();
      setSettingsOpen(settingsPanel.hidden);
    });

    document.addEventListener('click', (ev) => {
      if (settingsPanel.hidden) return;
      if (!settingsPanel.contains(ev.target) && ev.target !== btnSettings) setSettingsOpen(false);
    });

    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') setSettingsOpen(false);
    });

    // Show absolute URLs so they can be pasted straight into VLC / TiviMate.
    settingsPanel.querySelectorAll('.feed-path[data-path]').forEach((el) => {
      el.textContent = window.location.origin + el.dataset.path;
    });

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

    settingsPanel.addEventListener('click', async (ev) => {
      const btn = ev.target.closest('.mini-btn[data-copy]');
      if (!btn) return;
      ev.stopPropagation();
      const url = window.location.origin + btn.dataset.copy;
      const ok = await copyText(url);
      const original = btn.textContent;
      btn.textContent = ok ? 'Copied' : 'Copy failed';
      setTimeout(() => { btn.textContent = original; }, 1500);
    });

    const adminTokenRow = document.getElementById('adminTokenRow');
    const adminToken = document.getElementById('adminToken');
    const btnSaveToken = document.getElementById('btnSaveToken');

    btnSaveToken.addEventListener('click', () => {
      try { localStorage.setItem(TOKEN_KEY, adminToken.value.trim()); } catch (_) {}
      btnSaveToken.textContent = 'Saved';
      setTimeout(() => { btnSaveToken.textContent = 'Save'; }, 1500);
    });

    // Only surface the token field when the server actually requires one.
    async function initAdminSection() {
      try {
        const j = await (await fetch('/api/health?_=' + Date.now(), { cache: 'no-store' })).json();
        if (j.rescrape_requires_token) {
          adminTokenRow.hidden = false;
          adminToken.value = storedToken();
        }
      } catch (_) {}
    }

    load();
    initAdminSection();
    pollTimer = setInterval(() => {
      if (document.visibilityState === 'visible') load();
    }, 300000);
  </script>
</body>
</html>
"""


@app.get("/")
def index():
    return render_template_string(UI_HTML, app_version=VERSION, app_build_time=BUILD_TIME)


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
    log.info("SundaySignal server v%s (built %s) on http://0.0.0.0:%d/", VERSION, BUILD_TIME, PORT)
    log.info("JSON: /api/streams  M3U: /playlist.m3u  EPG: /epg.xml  Rescrape: POST /api/rescrape")
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
