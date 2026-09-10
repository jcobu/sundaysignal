#!/usr/bin/env python3
"""
SundaySignal source adapter: discovers game pages, extracts stream wrapper URLs,
and resolves them to real HLS (.m3u8) media URLs where possible.
"""

import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

try:
    import espn_schedule
except ImportError:
    espn_schedule = None  # type: ignore

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.nflbite.is"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL + "/",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
REQUEST_DELAY = 1.0
RESOLVE_DELAY = 0.4


# Hosts that repeatedly fail DNS/timeout — skip for the rest of the process
_DEAD_HOSTS: set[str] = set()

# Opt-in raw HTML capture for debugging embed-chain changes. Capped per
# kind so a run with many failures does not dump hundreds of files.
_DUMP_DIR = os.environ.get("SUNDAYSIGNAL_DEBUG_DUMP_DIR")
_DUMP_LIMIT = int(os.environ.get("SUNDAYSIGNAL_DEBUG_DUMP_LIMIT", "3"))
_DUMP_COUNTS: dict[str, int] = {}


def _dump(kind: str, url: str, content: str) -> None:
    if not _DUMP_DIR:
        return
    if _DUMP_COUNTS.get(kind, 0) >= _DUMP_LIMIT:
        return
    _DUMP_COUNTS[kind] = _DUMP_COUNTS.get(kind, 0) + 1
    try:
        os.makedirs(_DUMP_DIR, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", url)[:80]
        path = os.path.join(_DUMP_DIR, f"{kind}_{_DUMP_COUNTS[kind]}_{safe}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"    [dump] saved {kind} ({url[:70]}) → {path}")
    except OSError as e:
        print(f"    [dump] failed to save {kind}: {e}")
# Prefer these wrappers; skip noisy/dead embed farms
_SKIP_HOST_SUBSTR = (
    "selltvonline.shop",
    "sportsz.one",
    "sportsworlds.shop",
    "mjumbo.icu",
    "youtube.com",
    "live_chat",
)


def _host(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def fetch(url: str, referer: str | None = None, timeout: float = 12) -> str | None:
    host = _host(url)
    if host and host in _DEAD_HOSTS:
        return None
    if any(s in url for s in _SKIP_HOST_SUBSTR):
        return None
    try:
        headers = dict(HEADERS)
        if referer:
            headers["Referer"] = referer
        resp = SESSION.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.ConnectionError as e:
        if host:
            _DEAD_HOSTS.add(host)
        print(f"[ERROR] Failed to fetch {url}: {e}")
        return None
    except requests.RequestException as e:
        # DNS / timeout — mark host dead so we do not hammer it
        err = str(e).lower()
        if host and ("nameresolution" in err or "failed to resolve" in err or "timed out" in err):
            _DEAD_HOSTS.add(host)
        print(f"[ERROR] Failed to fetch {url}: {e}")
        return None


def slug_to_title(slug: str) -> str:
    return slug.replace("-", " ")


def extract_game_links(html: str) -> list[dict[str, str]]:
    soup = BeautifulSoup(html, "lxml")
    games = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.match(r"^/([A-Za-z0-9\-]+-vs-[A-Za-z0-9\-]+)/(\d+)/?$", href)
        if not m:
            continue
        slug, game_id = m.group(1), m.group(2)
        full = urljoin(BASE_URL, href)
        if game_id not in games:
            games[game_id] = {
                "id": game_id,
                "slug": slug,
                "title": slug_to_title(slug),
                "url": full,
            }
    return list(games.values())


def extract_streams(html: str, game_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    streams = []
    seen = set()

    for row in soup.find_all("tr"):
        hid = row.find("input", attrs={"type": "hidden", "id": re.compile(r"^linkk\d+$")})
        if not hid or not hid.get("value"):
            continue
        stream_url = hid["value"].strip()
        if not stream_url or stream_url in seen:
            continue
        seen.add(stream_url)

        name = None
        for td in row.find_all("td"):
            text = td.get_text(" ", strip=True)
            if text and len(text) > 2 and not text.isdigit() and "THANK YOU" not in text.upper():
                if any(c.isalpha() for c in text):
                    name = text
                    break
        if not name:
            name = "unknown"

        badges = []
        for a in row.find_all("a", class_=re.compile(r"btn")):
            t = a.get_text(strip=True)
            if t and t not in ("THANK YOU",) and len(t) < 30:
                badges.append(t)

        streams.append({"name": name, "url": stream_url, "badges": badges, "media_url": None})

    for hid in soup.find_all("input", attrs={"type": "hidden", "id": re.compile(r"^linkk\d+$")}):
        stream_url = (hid.get("value") or "").strip()
        if stream_url and stream_url not in seen:
            seen.add(stream_url)
            streams.append({"name": "unknown", "url": stream_url, "badges": [], "media_url": None})

    return streams


def _iframe_st_decrypt(encoded: str, xor_key: int, rev_indices: list[int]) -> str:
    chars = list(encoded)
    unshuffled = [""] * len(chars)
    for i, ch in enumerate(chars):
        unshuffled[rev_indices[i]] = ch
    xor_encoded = "".join(unshuffled)

    hex_encoded = ""
    for i in range(0, len(xor_encoded), 2):
        byte = int(xor_encoded[i : i + 2], 16)
        hex_encoded += chr(byte ^ xor_key)

    rot13_str = ""
    for i in range(0, len(hex_encoded), 2):
        rot13_str += chr(int(hex_encoded[i : i + 2], 16))

    def rot13_js(c: str) -> str:
        code = ord(c)
        if "A" <= c <= "Z":
            return chr(code + 13 if 90 >= code + 13 else code - 13)
        if "a" <= c <= "z":
            return chr(code + 13 if 122 >= code + 13 else code - 13)
        return c

    reversed_s = "".join(rot13_js(c) for c in rot13_str)
    b64 = reversed_s[::-1]
    return base64.b64decode(b64).decode("utf-8", errors="replace")


# Real players show up as an actual <iframe src="...">; these substrings mark
# frames that are never the player (ads/analytics/chat) even when present.
_NON_PLAYER_IFRAME_SUBSTR = (
    "youtube.com",
    "youtu.be",
    "google.com",
    "googletagmanager",
    "doubleclick",
    "histats.com",
    "discordapp.com",
    "disqus.com",
    "facebook.com/plugins",
)

MAX_RESOLVE_HOPS = 4


def _first_player_iframe(html: str, base_url: str) -> str | None:
    """First real <iframe src> on the page, resolved against base_url so
    protocol-relative ("//host/path") and relative srcs work too."""
    for raw in re.findall(r'''<iframe\b[^>]*?\bsrc=["']([^"']+)["']''', html, re.I):
        candidate = urljoin(base_url, raw)
        if any(s in candidate.lower() for s in _NON_PLAYER_IFRAME_SUBSTR):
            continue
        return candidate
    return None


def resolve_media_url(wrapper_url: str) -> dict[str, str] | None:
    """
    Follow nested player <iframe>s to a playable HLS playlist.

    Wrapper pages now commonly nest their real player behind 1-3 layers of
    <iframe src=...> (sometimes protocol-relative). Some layers still use the
    old iframe.st _dd/_dk/_dri obfuscation; most just embed a raw .m3u8.

    Returns dict with media_url (HLS playlist), embed_url, and chain — or None.
    """
    debug = os.environ.get("SUNDAYSIGNAL_DEBUG_RESOLVE")
    current_url = wrapper_url
    referer = BASE_URL + "/"
    hops = ["wrapper"]
    try:
        for hop in range(MAX_RESOLVE_HOPS):
            html = fetch(current_url, referer=referer)
            if not html:
                if debug:
                    print(f"    [resolve] {wrapper_url[:70]}: hop {hop} ({'→'.join(hops)}) fetch failed ({current_url[:70]})")
                return None

            # iframe.st style — decrypt runtime stream URL
            if "const _dd" in html or "_dd =" in html:
                dd_m = re.search(r'const _dd\s*=\s*"([^"]+)"', html)
                dk_m = re.search(r'const _dk\s*=\s*(\d+)', html)
                dri_m = re.search(r'const _dri\s*=\s*\[([^\]]+)\]', html)
                if dd_m and dk_m and dri_m:
                    media = _iframe_st_decrypt(
                        dd_m.group(1),
                        int(dk_m.group(1)),
                        [int(x.strip()) for x in dri_m.group(1).split(",") if x.strip()],
                    )
                    if media.startswith("http"):
                        return {
                            "media_url": media,
                            "embed_url": current_url,
                            "source_type": "hls_playlist",
                            "chain": "→".join(hops) + "→decrypt→hls",
                        }
                    if debug:
                        print(f"    [resolve] {wrapper_url[:70]}: hop {hop} decrypt did not yield an http(s) URL ({media[:70]!r})")
                elif debug:
                    print(f"    [resolve] {wrapper_url[:70]}: hop {hop} _dd marker present but _dd/_dk/_dri regex did not all match")

            # direct m3u8 on page
            m3u8s = re.findall(r'https?://[^\s"\']+\.m3u8[^\s"\']*', html)
            if m3u8s:
                return {
                    "media_url": m3u8s[0],
                    "embed_url": current_url,
                    "source_type": "hls_playlist",
                    "chain": "→".join(hops) + "→m3u8",
                }

            next_url = _first_player_iframe(html, current_url)
            if not next_url:
                if debug:
                    print(f"    [resolve] {wrapper_url[:70]}: hop {hop} ({'→'.join(hops)}) dead end, no player iframe found ({current_url[:70]})")
                _dump(f"dead_end_hop{hop}", current_url, html)
                return None

            referer = current_url
            current_url = next_url
            hops.append("iframe")

        if debug:
            print(f"    [resolve] {wrapper_url[:70]}: exceeded {MAX_RESOLVE_HOPS} hops without resolving")
        return None
    except Exception as e:
        print(f"  [resolve error] {wrapper_url[:60]}: {e}")
        return None



def parse_teams(title: str) -> dict:
    """Best-effort away/home split for UI logos."""
    parts = re.split(r"\s+vs\.?\s+", title or "", flags=re.I)
    away = parts[0].strip() if parts else ""
    home = parts[1].strip() if len(parts) > 1 else ""
    return {"away_team": away or None, "home_team": home or None}

def crawl(resolve: bool = True, max_resolve_per_game: int = 6) -> dict[str, Any]:
    print(f"[{datetime.now(timezone.utc).isoformat()}] Fetching homepage …")
    home_html = fetch(BASE_URL + "/")
    if not home_html:
        home_html = fetch(BASE_URL + "/date/today")
    if not home_html:
        raise RuntimeError("Could not fetch any listing page")

    games = extract_game_links(home_html)
    print(f"Found {len(games)} unique game pages")

    results = []
    for i, g in enumerate(games, 1):
        print(f"  [{i}/{len(games)}] {g['title']} → {g['url']}")
        page = fetch(g["url"])
        streams = extract_streams(page, g["url"]) if page else []

        if resolve and streams:
            # Prefer known-working mirrors first
            ordered = sorted(
                streams,
                key=lambda s: (
                    0 if "live2.totalsporteks" in s.get("url", "") else
                    1 if "totalsporteks" in s.get("url", "") else 2
                ),
            )
            resolved = 0
            for s in ordered:
                if resolved >= max_resolve_per_game:
                    break
                u = s.get("url") or ""
                # Skip known-bad / low-value hosts quickly
                if any(x in u for x in _SKIP_HOST_SUBSTR):
                    continue
                if _host(u) in _DEAD_HOSTS:
                    continue
                # Prefer live2.totalsporteks (iframe.st decrypt path)
                if "live2.totalsporteks" not in u and resolved >= 1:
                    continue
                if "totalsporteks" not in u and "iframe.st" not in u and resolved >= 1:
                    continue
                resolved_info = resolve_media_url(s["url"])
                if resolved_info and resolved_info.get("media_url"):
                    s["media_url"] = resolved_info["media_url"]
                    s["embed_url"] = resolved_info.get("embed_url")
                    s["source_type"] = resolved_info.get("source_type", "hls_playlist")
                    s["chain"] = resolved_info.get("chain")
                    resolved += 1
                    print(f"      ✓ {s['name']}: {s['media_url'][:70]}")
                time.sleep(RESOLVE_DELAY)

        playable = [s for s in streams if s.get("media_url")]
        teams = parse_teams(g["title"])
        results.append(
            {
                "id": g["id"],
                "slug": g["slug"],
                "title": g["title"],
                "url": g["url"],
                "away_team": teams.get("away_team"),
                "home_team": teams.get("home_team"),
                "stream_count": len(playable),
                "resolved_count": len(playable),
                "streams": playable,  # only playable HLS media_url entries
                "all_wrapper_count": len(streams),
            }
        )
        if i < len(games):
            time.sleep(REQUEST_DELAY)

    payload = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source": BASE_URL,
        "game_count": len(results),
        "games": results,
    }
    return payload


def _count_playable(data: dict) -> int:
    return sum(len(g.get("streams") or []) for g in (data.get("games") or []))


def _load_previous(path: str) -> dict | None:
    try:
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _merge_keep_previous(new: dict, old: dict | None) -> dict:
    """
    If the new scrape resolved fewer (or zero) playable streams, keep prior
    playable entries per game so the UI does not go blank on flaky embeds.
    """
    if not old:
        return new
    old_by_id = {str(g.get("id")): g for g in (old.get("games") or []) if g.get("id") is not None}
    merged_games = []
    for g in new.get("games") or []:
        gid = str(g.get("id"))
        new_streams = g.get("streams") or []
        if new_streams:
            merged_games.append(g)
            continue
        prev = old_by_id.get(gid)
        if prev and (prev.get("streams") or []):
            kept = dict(g)
            kept["streams"] = prev["streams"]
            kept["stream_count"] = len(prev["streams"])
            kept["resolved_count"] = len(prev["streams"])
            kept["stale"] = True
            kept["stale_from"] = prev.get("scraped_at") or old.get("scraped_at")
            merged_games.append(kept)
            print(f"  · kept previous streams for {g.get('title')} (new resolve empty)")
        else:
            merged_games.append(g)
    # Also keep old games that disappeared from the listing but still had streams
    new_ids = {str(g.get("id")) for g in merged_games}
    for gid, prev in old_by_id.items():
        if gid not in new_ids and (prev.get("streams") or []):
            p = dict(prev)
            p["stale"] = True
            merged_games.append(p)
            print(f"  · retained previous game {prev.get('title')}")
    out = dict(new)
    out["games"] = merged_games
    out["game_count"] = len(merged_games)
    out["playable_total"] = _count_playable(out)
    return out


def main() -> None:
    data = crawl(resolve=True)

    # Attach ESPN kickoff / live status when possible
    if espn_schedule is not None:
        try:
            events = espn_schedule.fetch_scoreboard()
            for g in data.get("games") or []:
                espn_schedule.enrich_game(g, events)
            data["games"] = espn_schedule.sort_games_for_ui(data.get("games") or [])
            data["schedule_enriched"] = True
            print(f"[espn] matched schedule for {sum(1 for g in data['games'] if g.get('schedule_source'))}/{len(data['games'])} games")
        except Exception as e:
            print(f"[espn] enrich failed: {e}")
            data["schedule_enriched"] = False
    candidates = [
        "/output/sundaysignal_streams.json",
        os.path.join(os.path.dirname(__file__) or ".", "output", "sundaysignal_streams.json"),
        "/home/workdir/artifacts/sundaysignal/output/sundaysignal_streams.json",
        "sundaysignal_streams.json",
    ]
    out_path = None
    for p in candidates:
        try:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            out_path = p
            break
        except OSError:
            continue
    if out_path is None:
        out_path = "sundaysignal_streams.json"

    previous = _load_previous(out_path)
    new_playable = _count_playable(data)
    old_playable = _count_playable(previous) if previous else 0

    if new_playable == 0 and old_playable > 0:
        print(f"\n[guard] New scrape has 0 playable streams; keeping previous file ({old_playable} streams)")
        # Still update a sidecar status so UI can show attempt time
        try:
            status_path = os.path.join(os.path.dirname(out_path) or ".", "last_scrape_status.json")
            with open(status_path, "w", encoding="utf-8") as sf:
                json.dump({
                    "scraped_at": data.get("scraped_at"),
                    "playable": 0,
                    "kept_previous": True,
                    "previous_playable": old_playable,
                    "dead_hosts": sorted(_DEAD_HOSTS),
                }, sf, indent=2)
        except OSError:
            pass
        print(f"Resolved media URLs: 0 (previous kept at {out_path})")
        return

    data = _merge_keep_previous(data, previous)
    data["playable_total"] = _count_playable(data)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    total_resolved = sum(g.get("resolved_count") or 0 for g in data["games"])
    print(f"\nWrote {data['game_count']} games → {out_path}")
    print(f"Resolved media URLs: {total_resolved} (playable_total={data['playable_total']})")
    for g in data["games"]:
        flag = " [stale]" if g.get("stale") else ""
        print(f"  • {g['title']}: {g.get('stream_count', 0)}/{g.get('all_wrapper_count', 0)} streams resolved{flag}")


if __name__ == "__main__":
    main()
