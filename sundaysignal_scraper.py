#!/usr/bin/env python3
"""
SundaySignal crawler: asks each configured source adapter what games it has,
then resolves their wrapper links to real HLS (.m3u8) media URLs.

Site-specific knowledge lives in sources/ — this module owns the generic
parts: the nested-iframe resolve chain, concurrency, merging, and output.
"""

import base64
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

try:
    import espn_schedule
except ImportError:
    espn_schedule = None  # type: ignore

try:
    from version import VERSION, BUILD_TIME
except ImportError:
    VERSION, BUILD_TIME = "0.0.0-dev", "unknown"

import logsetup
import netfetch
import notify
import sources as source_registry
from netfetch import fetch

log = logging.getLogger(__name__)

#: Pause between game-page fetches — the one part that repeatedly hits a
#: single source site, so it stays deliberately polite.
REQUEST_DELAY = 1.0

DEFAULT_MAX_RESOLVE_PER_GAME = int(os.environ.get("SUNDAYSIGNAL_MAX_RESOLVE_PER_GAME", "6"))
RESOLVE_WORKERS = int(os.environ.get("SUNDAYSIGNAL_RESOLVE_WORKERS", "6"))
MAX_RESOLVE_HOPS = int(os.environ.get("SUNDAYSIGNAL_MAX_RESOLVE_HOPS", "4"))

# Third-party mirror hosts are numerous and one-off — fail fast on a
# hung/slow one rather than waiting the full default fetch() timeout on
# every hop, which is tuned for the (trusted, single) main source site.
RESOLVE_FETCH_TIMEOUT = float(os.environ.get("SUNDAYSIGNAL_RESOLVE_TIMEOUT", "6"))

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
        log.info("saved %s dump (%s) → %s", kind, url[:70], path)
    except OSError as e:
        log.warning("failed to save %s dump: %s", kind, e)


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


def _first_player_iframe(html: str, base_url: str) -> str | None:
    """First real <iframe src> on the page, resolved against base_url so
    protocol-relative ("//host/path") and relative srcs work too."""
    for raw in re.findall(r'''<iframe\b[^>]*?\bsrc=["']([^"']+)["']''', html, re.I):
        candidate = urljoin(base_url, raw)
        if any(s in candidate.lower() for s in _NON_PLAYER_IFRAME_SUBSTR):
            continue
        return candidate
    return None


def resolve_media_url(wrapper_url: str, referer: str | None = None) -> dict[str, str] | None:
    """
    Follow nested player <iframe>s to a playable HLS playlist.

    Wrapper pages now commonly nest their real player behind 1-3 layers of
    <iframe src=...> (sometimes protocol-relative). Some layers still use the
    old iframe.st _dd/_dk/_dri obfuscation; most just embed a raw .m3u8.

    Returns dict with media_url (HLS playlist), embed_url, and chain — or None.
    """
    current_url = wrapper_url
    hops = ["wrapper"]
    try:
        for hop in range(MAX_RESOLVE_HOPS):
            html = fetch(current_url, referer=referer, timeout=RESOLVE_FETCH_TIMEOUT)
            if not html:
                log.debug("%s: hop %d (%s) fetch failed (%s)", wrapper_url[:70], hop, "→".join(hops), current_url[:70])
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
                    log.debug("%s: hop %d decrypt did not yield an http(s) URL (%r)", wrapper_url[:70], hop, media[:70])
                else:
                    log.debug("%s: hop %d _dd marker present but _dd/_dk/_dri regex did not all match", wrapper_url[:70], hop)

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
                log.debug("%s: hop %d (%s) dead end, no player iframe (%s)", wrapper_url[:70], hop, "→".join(hops), current_url[:70])
                _dump(f"dead_end_hop{hop}", current_url, html)
                return None

            referer = current_url
            current_url = next_url
            hops.append("iframe")

        log.debug("%s: exceeded %d hops without resolving", wrapper_url[:70], MAX_RESOLVE_HOPS)
        return None
    except Exception as e:
        log.warning("resolve error for %s: %s", wrapper_url[:60], e)
        return None


def parse_teams(title: str) -> dict:
    """Best-effort away/home split for UI logos."""
    parts = re.split(r"\s+vs\.?\s+", title or "", flags=re.I)
    away = parts[0].strip() if parts else ""
    home = parts[1].strip() if len(parts) > 1 else ""
    return {"away_team": away or None, "home_team": home or None}


def _round_robin_merge(lists: list[list]) -> list:
    """Interleave several lists round-robin: [a0,b0,c0,a1,b1,c1,...] so a
    shared worker pool makes progress on every list from the start instead
    of draining the first one before touching the rest."""
    merged = []
    max_len = max((len(lst) for lst in lists), default=0)
    for i in range(max_len):
        for lst in lists:
            if i < len(lst):
                merged.append(lst[i])
    return merged


def _collect_records(srcs: list) -> list[dict[str, Any]]:
    """Phase 1: ask every source for its games, fetch each game page, and
    build that game's ordered candidate wrapper list."""
    records: list[dict[str, Any]] = []
    for src in srcs:
        games = src.discover_games()
        log.info("[%s] found %d game pages", src.name, len(games))
        for i, g in enumerate(games, 1):
            log.info("[%s] (%d/%d) %s → %s", src.name, i, len(games), g["title"], g["url"])
            page = fetch(g["url"], referer=src.base_url + "/")
            streams = src.extract_streams(page, g["url"]) if page else []
            # Try known-working mirrors first, but only as a starting order —
            # any provider that resolves counts toward max_resolve_per_game.
            ordered = sorted(streams, key=src.rank_stream)
            candidates = [
                s for s in ordered
                if not any(x in (s.get("url") or "") for x in netfetch.SKIP_HOST_SUBSTR)
                and not netfetch.is_dead(netfetch.host_of(s.get("url") or ""))
            ]
            records.append(
                {
                    "game": g,
                    "source": src.name,
                    "referer": src.base_url + "/",
                    "streams": streams,
                    "candidates": candidates,
                    "resolved": 0,
                    "in_flight": 0,
                }
            )
            if i < len(games):
                time.sleep(REQUEST_DELAY)
    return records


def _resolve_records(records: list[dict[str, Any]], max_resolve_per_game: int) -> None:
    """Phase 2: resolve every game's candidates in one shared thread pool
    instead of one game's pool fully draining before the next game starts
    (each game's wrapper streams live on independent third-party hosts, so
    there's no reason to serialize them). Round-robin interleaving means the
    first wave already spans every game; waves are submitted a few at a time
    so a game stops drawing more candidates the moment it hits its cap,
    instead of every one of its wrapper URLs getting dispatched up front
    regardless of how many already resolved."""
    merged = _round_robin_merge([[(r, s) for s in r["candidates"]] for r in records])
    if not merged:
        return
    with ThreadPoolExecutor(max_workers=RESOLVE_WORKERS) as pool:
        idx = 0
        while idx < len(merged):
            batch = []
            while idx < len(merged) and len(batch) < RESOLVE_WORKERS:
                rec, s = merged[idx]
                idx += 1
                # Reserve quota at submission time, not just checking the
                # settled "resolved" count — otherwise a single batch can
                # queue several of the same game's candidates before any of
                # them finish, all succeed, and blow past its cap.
                if rec["resolved"] + rec["in_flight"] >= max_resolve_per_game:
                    continue
                rec["in_flight"] += 1
                batch.append((rec, s))
            if not batch:
                continue
            futures = {
                pool.submit(resolve_media_url, s["url"], referer=rec["referer"]): (rec, s)
                for rec, s in batch
            }
            for future in as_completed(futures):
                rec, s = futures[future]
                rec["in_flight"] -= 1
                try:
                    resolved_info = future.result()
                except Exception as e:
                    log.warning("resolve error for %s: %s", (s.get("url") or "")[:60], e)
                    continue
                if resolved_info and resolved_info.get("media_url"):
                    s["media_url"] = resolved_info["media_url"]
                    s["embed_url"] = resolved_info.get("embed_url")
                    s["source_type"] = resolved_info.get("source_type", "hls_playlist")
                    s["chain"] = resolved_info.get("chain")
                    rec["resolved"] += 1
                    log.info("  ✓ [%s] %s: %s", rec["game"]["title"], s["name"], s["media_url"][:70])


def crawl(resolve: bool = True, max_resolve_per_game: int = DEFAULT_MAX_RESOLVE_PER_GAME) -> dict[str, Any]:
    srcs = source_registry.get_sources()
    log.info("crawling %d source(s): %s", len(srcs), ", ".join(s.name for s in srcs))

    records = _collect_records(srcs)
    if resolve:
        _resolve_records(records, max_resolve_per_game)

    results = []
    for rec in records:
        g = rec["game"]
        playable = [s for s in rec["streams"] if s.get("media_url")]
        teams = parse_teams(g["title"])
        results.append(
            {
                "id": g["id"],
                # Namespaced key so two sources listing the same game id
                # can't collide when merging or de-duplicating.
                "uid": f"{rec['source']}:{g['id']}",
                "source": rec["source"],
                "slug": g["slug"],
                "title": g["title"],
                "url": g["url"],
                "away_team": teams.get("away_team"),
                "home_team": teams.get("home_team"),
                "stream_count": len(playable),
                "resolved_count": len(playable),
                "streams": playable,  # only playable HLS media_url entries
                "all_wrapper_count": len(rec["streams"]),
            }
        )

    return {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "source": srcs[0].base_url if srcs else "",
        "sources": [{"name": s.name, "base_url": s.base_url} for s in srcs],
        "game_count": len(results),
        "games": results,
        "version": VERSION,
        "build_time": BUILD_TIME,
    }


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


def _game_key(g: dict) -> str:
    return str(g.get("uid") or g.get("id"))


#: How long a previously-working stream is carried forward once newer
#: scrapes stop finding it. Past this it's assumed the link has expired.
KEEP_STALE_HOURS = float(os.environ.get("SUNDAYSIGNAL_KEEP_STALE_HOURS", "6"))
#: Ceiling on a game's stream list after merging, so carried-over links
#: can't accumulate indefinitely.
MAX_STREAMS_PER_GAME = int(os.environ.get("SUNDAYSIGNAL_MAX_STREAMS_PER_GAME", "12"))


def _is_live(game: dict) -> bool:
    return (game.get("status_state") or "").lower() == "in" or bool(game.get("live"))


def _age_hours(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        then = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600


def _merge_game_streams(new_game: dict, prev_game: dict | None, old_scraped_at: str | None) -> list:
    """Union a game's fresh streams with the ones it had before.

    A scrape must never be able to shrink a working game: flaky mirrors mean
    a run that resolves 1 of 12 is normal, and replacing the list outright
    would throw away 11 working links mid-game. Fresh streams come first;
    previously-working ones follow, marked stale.
    """
    fresh = list(new_game.get("streams") or [])
    prev = list((prev_game or {}).get("streams") or [])
    if not prev:
        return fresh

    from_ts = (prev_game or {}).get("stale_from") or (prev_game or {}).get("scraped_at") or old_scraped_at
    age = _age_hours(from_ts)
    expired = age is not None and age > KEEP_STALE_HOURS
    # A live game with nothing fresh keeps its old links no matter how old:
    # a link that might still work beats an empty list while it's on.
    if expired and not (_is_live(new_game) and not fresh):
        return fresh

    seen = {s.get("media_url") for s in fresh if s.get("media_url")}
    carried = []
    for s in prev:
        url = s.get("media_url")
        if not url or url in seen:
            continue
        seen.add(url)
        entry = dict(s)
        entry["stale"] = True
        entry["stale_from"] = from_ts
        carried.append(entry)
    return (fresh + carried)[:MAX_STREAMS_PER_GAME]


def _merge_keep_previous(new: dict, old: dict | None) -> dict:
    """Merge a fresh scrape over the previous catalog without ever losing
    streams that were working, so a partial scrape can only ever add."""
    if not old:
        return new
    old_by_id = {
        _game_key(g): g
        for g in (old.get("games") or [])
        if g.get("id") is not None or g.get("uid") is not None
    }
    old_scraped_at = old.get("scraped_at")
    merged_games = []
    for g in new.get("games") or []:
        prev = old_by_id.get(_game_key(g))
        streams = _merge_game_streams(g, prev, old_scraped_at)
        merged = dict(g)
        merged["streams"] = streams
        merged["stream_count"] = len(streams)
        merged["resolved_count"] = sum(1 for s in streams if not s.get("stale"))
        carried = len(streams) - merged["resolved_count"]
        if carried:
            merged["stale"] = merged["resolved_count"] == 0
            merged["stale_from"] = (prev or {}).get("stale_from") or (prev or {}).get("scraped_at") or old_scraped_at
            log.info(
                "carried %d previous stream(s) for %s (%d resolved this run)",
                carried, g.get("title"), merged["resolved_count"],
            )
        merged_games.append(merged)

    # Also keep old games that disappeared from the listing but still had streams
    new_ids = {_game_key(g) for g in merged_games}
    for gid, prev in old_by_id.items():
        if gid not in new_ids and (prev.get("streams") or []):
            p = dict(prev)
            p["stale"] = True
            merged_games.append(p)
            log.info("retained previous game %s", prev.get("title"))
    out = dict(new)
    out["games"] = merged_games
    out["game_count"] = len(merged_games)
    out["playable_total"] = _count_playable(out)
    return out


def _output_dir_candidates() -> tuple[str, ...]:
    # OUTPUT_DIR first so the crawler and the web app never disagree about
    # where the catalog lives (docker-compose sets it for both).
    env_dir = os.environ.get("OUTPUT_DIR", "").strip()
    return tuple(
        d for d in (
            env_dir,
            "/output",
            os.path.join(os.path.dirname(__file__) or ".", "output"),
            "/home/workdir/artifacts/sundaysignal/output",
            ".",
        ) if d
    )


def _resolve_output_dir() -> str:
    for d in _output_dir_candidates():
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except OSError:
            continue
    return "."


def _load_failure_streak(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            return int(json.load(f).get("consecutive_failures", 0))
    except Exception:
        return 0


def _save_failure_streak(path: str, streak: int) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"consecutive_failures": streak, "updated_at": datetime.now(timezone.utc).isoformat()}, f, indent=2)
    except OSError as e:
        log.warning("failed to save failure streak: %s", e)


def _handle_crawl_outcome(state_path: str, playable: int, games: int) -> None:
    """Track consecutive empty crawls and alert once the streak crosses the
    configured threshold, then again when it recovers."""
    streak = _load_failure_streak(state_path)
    if playable > 0:
        if streak >= notify.NOTIFY_AFTER:
            notify.send(
                "SundaySignal recovered",
                f"Streams are resolving again: {playable} across {games} games "
                f"(after {streak} empty crawls).",
            )
        _save_failure_streak(state_path, 0)
        return

    streak += 1
    _save_failure_streak(state_path, streak)
    log.warning("crawl produced no playable streams (%d in a row)", streak)
    if streak == notify.NOTIFY_AFTER:
        notify.send(
            "SundaySignal is not finding streams",
            f"{streak} crawls in a row resolved 0 playable streams across {games} games. "
            f"The source site may have changed its markup or rotated domains.",
        )


def run_cycle(output_dir: str | None = None) -> dict[str, Any]:
    """One complete crawl cycle: crawl, enrich, guard, merge, write.

    Both the interval crawler and the web UI's "Rescrape now" button go
    through here. They must: writing crawl() output straight to disk skips
    the guard below, so a scrape that resolves nothing would replace a
    perfectly good catalog with an empty one.

    Returns a summary of what happened.
    """
    output_dir = output_dir or _resolve_output_dir()
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "sundaysignal_streams.json")
    dead_hosts_path = os.path.join(output_dir, "dead_hosts.json")
    status_path = os.path.join(output_dir, "last_scrape_status.json")
    streak_path = os.path.join(output_dir, "crawl_state.json")

    # Every crawl runs as a fresh process (see entrypoint-crawler.sh), so
    # without this, every cycle re-eats the DNS/timeout cost of every mirror
    # that was already known dead from the last run.
    netfetch.load_dead_hosts(dead_hosts_path)

    data = crawl(resolve=True)

    netfetch.save_dead_hosts(dead_hosts_path)

    # Attach ESPN kickoff / live status when possible
    if espn_schedule is not None:
        try:
            events = espn_schedule.fetch_scoreboard()
            for g in data.get("games") or []:
                espn_schedule.enrich_game(g, events)
            data["games"] = espn_schedule.sort_games_for_ui(data.get("games") or [])
            data["schedule_enriched"] = True
            log.info(
                "matched ESPN schedule for %d/%d games",
                sum(1 for g in data["games"] if g.get("schedule_source")),
                len(data["games"]),
            )
        except Exception as e:
            log.warning("ESPN enrich failed: %s", e)
            data["schedule_enriched"] = False

    previous = _load_previous(out_path)
    new_playable = _count_playable(data)
    old_playable = _count_playable(previous) if previous else 0

    _handle_crawl_outcome(streak_path, new_playable, data.get("game_count", 0))

    def _write_status(kept_previous: bool, playable: int) -> None:
        # Written on every cycle, not just failures: the catalog's own
        # timestamp only moves on a successful write, so this sidecar is what
        # tells the UI the crawler is alive even when it finds nothing.
        try:
            with open(status_path, "w", encoding="utf-8") as sf:
                json.dump({
                    "scraped_at": data.get("scraped_at"),
                    "playable": playable,
                    "game_count": data.get("game_count", 0),
                    "kept_previous": kept_previous,
                    "previous_playable": old_playable,
                    "dead_hosts": sorted(netfetch.dead_hosts().keys()),
                }, sf, indent=2)
        except OSError as e:
            log.warning("failed to write scrape status: %s", e)

    if new_playable == 0 and old_playable > 0:
        log.warning("new scrape has 0 playable streams; keeping previous file (%d streams)", old_playable)
        _write_status(kept_previous=True, playable=0)
        return {
            "kept_previous": True,
            "wrote": False,
            "game_count": data.get("game_count", 0),
            "playable": 0,
            "previous_playable": old_playable,
            "path": out_path,
        }

    data = _merge_keep_previous(data, previous)
    data["playable_total"] = _count_playable(data)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    _write_status(kept_previous=False, playable=data["playable_total"])

    total_resolved = sum(g.get("resolved_count") or 0 for g in data["games"])
    log.info("wrote %d games → %s", data["game_count"], out_path)
    log.info("resolved media URLs: %d (playable_total=%d)", total_resolved, data["playable_total"])
    for g in data["games"]:
        flag = " [stale]" if g.get("stale") else ""
        log.info("  • %s: %d/%d streams resolved%s", g["title"], g.get("stream_count", 0), g.get("all_wrapper_count", 0), flag)

    return {
        "kept_previous": False,
        "wrote": True,
        "game_count": data.get("game_count", 0),
        "playable": data.get("playable_total", 0),
        "previous_playable": old_playable,
        "path": out_path,
    }


def main() -> None:
    logsetup.configure()
    log.info("SundaySignal crawler v%s (built %s)", VERSION, BUILD_TIME)
    run_cycle()


if __name__ == "__main__":
    main()
