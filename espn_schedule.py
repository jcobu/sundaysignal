"""Fetch NFL schedule/status from ESPN's public JSON (unofficial, no key)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)

ESPN_SCOREBOARD = (
    "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
)
ESPN_SUMMARY = (
    "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/summary"
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# normalize nicknames / city variants for matching
ALIASES = {
    "oakland raiders": "las vegas raiders",
    "washington football team": "washington commanders",
    "washington redskins": "washington commanders",
    "st louis rams": "los angeles rams",
    "san diego chargers": "los angeles chargers",
}


def _norm(name: str) -> str:
    s = re.sub(r"[^a-z0-9\s]", "", (name or "").lower())
    s = re.sub(r"\s+", " ", s).strip()
    return ALIASES.get(s, s)


def _team_tokens(name: str) -> set[str]:
    n = _norm(name)
    parts = n.split()
    # full name + last token (nickname) for fuzzy match
    out = {n}
    if parts:
        out.add(parts[-1])
    if len(parts) >= 2:
        out.add(" ".join(parts[-2:]))
    return out


def fetch_scoreboard(dates: str | None = None, timeout: float = 15) -> list[dict[str, Any]]:
    """
    Return normalized events from ESPN scoreboard.
    dates: optional YYYYMMDD or YYYYMMDD-YYYYMMDD
    """
    params = {}
    if dates:
        params["dates"] = dates
    try:
        r = requests.get(
            ESPN_SCOREBOARD,
            params=params or None,
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=timeout,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        log.warning("scoreboard fetch failed: %s", e)
        return []

    events_out = []
    for e in payload.get("events") or []:
        comps = e.get("competitions") or [{}]
        c = comps[0]
        home = away = home_abbr = away_abbr = None
        sides: dict[str, dict] = {}
        for t in c.get("competitors") or []:
            team = t.get("team") or {}
            name = team.get("displayName") or team.get("name") or ""
            abbr = team.get("abbreviation") or ""
            side = t.get("homeAway")
            if side == "home":
                home, home_abbr = name, abbr
            elif side == "away":
                away, away_abbr = name, abbr
            if side in ("home", "away"):
                sides[side] = {
                    "id": str(team.get("id") or t.get("id") or ""),
                    "score": t.get("score"),
                    "color": _hex_color(team.get("color")),
                }
        status = e.get("status") or {}
        st = status.get("type") or {}
        state = (st.get("state") or "").lower()  # pre | in | post
        status_name = st.get("name") or ""
        desc = st.get("description") or st.get("detail") or ""
        iso = e.get("date") or ""
        events_out.append(
            {
                "espn_id": e.get("id"),
                "name": e.get("name"),
                "date": iso,
                "home_team": home,
                "away_team": away,
                "home_abbr": home_abbr,
                "away_abbr": away_abbr,
                "status_state": state,  # pre, in, post
                "status_name": status_name,
                "status_detail": desc,
                "venue": ((c.get("venue") or {}).get("fullName")),
                "tokens": _team_tokens(home or "") | _team_tokens(away or ""),
                **_live_fields(sides, status, st, c),
            }
        )
    return events_out


def _hex_color(value: Any) -> str | None:
    value = str(value or "").strip().lstrip("#")
    return "#" + value.lower() if re.fullmatch(r"[0-9a-fA-F]{6}", value) else None


def _score(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _live_fields(sides: dict, status: dict, st: dict, comp: dict) -> dict[str, Any]:
    """Scores, team colors, clock and down-and-distance for one event."""
    away, home = sides.get("away") or {}, sides.get("home") or {}
    situation = None
    sit = comp.get("situation") or {}
    if sit.get("downDistanceText") or sit.get("possessionText"):
        possession = str(sit.get("possession") or "")
        side = "away" if possession and possession == away.get("id") else "home" if possession and possession == home.get("id") else None
        situation = {
            "down_distance": sit.get("shortDownDistanceText") or sit.get("downDistanceText"),
            "spot": sit.get("possessionText"),
            "possession": side,
            "red_zone": bool(sit.get("isRedZone")),
            "last_play": ((sit.get("lastPlay") or {}).get("text")),
        }
    broadcasts = [n for b in comp.get("broadcasts") or [] for n in (b.get("names") or [])]
    return {
        "away_score": _score(away.get("score")),
        "home_score": _score(home.get("score")),
        "away_color": away.get("color"),
        "home_color": home.get("color"),
        "period": status.get("period") or None,
        "display_clock": status.get("displayClock") or None,
        "short_detail": st.get("shortDetail") or None,
        "broadcast": broadcasts[0] if broadcasts else None,
        "situation": situation,
    }


LIVE_KEYS = ("away_score", "home_score", "away_color", "home_color", "period",
             "display_clock", "short_detail", "broadcast", "situation")


def format_kickoff(iso: str, tz_name: str = "America/Los_Angeles") -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except Exception:
        return iso
    try:
        local = dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        local = dt.astimezone(timezone.utc)
    try:
        s = local.strftime("%a %b %d · %I:%M %p %Z")
        s = s.replace(" 0", " ")
        return s.replace(" · 0", " · ")
    except Exception:
        return iso



def match_event(game_title: str, events: list[dict], away: str | None = None, home: str | None = None) -> dict | None:
    """Match a scraped game title to an ESPN event by team name overlap."""
    tokens = set()
    if away:
        tokens |= _team_tokens(away)
    if home:
        tokens |= _team_tokens(home)
    if game_title:
        # split vs
        parts = re.split(r"\s+vs\.?\s+", game_title, flags=re.I)
        for p in parts:
            tokens |= _team_tokens(p)
        tokens |= _team_tokens(game_title)

    tokens = {t for t in tokens if len(t) > 2}
    if not tokens:
        return None

    best = None
    best_score = 0
    for ev in events:
        et = ev.get("tokens") or set()
        # score: overlap of significant tokens
        score = len(tokens & et)
        # bonus if both nicknames match
        if score > best_score:
            best_score = score
            best = ev
    if best_score >= 2:
        return best
    return None


def enrich_game(game: dict, events: list[dict], tz_name: str = "America/Los_Angeles") -> dict:
    ev = match_event(
        game.get("title") or "",
        events,
        away=game.get("away_team"),
        home=game.get("home_team"),
    )
    if not ev:
        game.setdefault("schedule_source", None)
        return game

    state = ev.get("status_state") or "pre"
    game["espn_id"] = ev.get("espn_id")
    game["start_time"] = ev.get("date")
    game["kickoff_local"] = format_kickoff(ev.get("date") or "", tz_name)
    game["status_state"] = state  # pre | in | post
    game["status_detail"] = ev.get("status_detail") or ev.get("status_name")
    game["venue"] = ev.get("venue")
    for key in LIVE_KEYS:
        game[key] = ev.get(key)
    game["schedule_source"] = "espn"
    # Prefer ESPN team order when available
    if ev.get("away_team"):
        game["away_team"] = ev["away_team"]
    if ev.get("home_team"):
        game["home_team"] = ev["home_team"]
    if state == "in":
        game["live"] = True
    elif state == "post":
        game["live"] = False
        game["ended"] = True
    else:
        game["live"] = False
        game["ended"] = False
    return game


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def games_from_events(events: list[dict], tz_name: str = "America/Los_Angeles") -> list[dict]:
    """Turn scoreboard events into game records.

    The schedule — not a scrape — is what decides which games exist. A
    mirror site failing to list a game (or failing to parse) then costs
    its streams, not its place in the lineup.
    """
    games = []
    for ev in events:
        away = ev.get("away_team") or ""
        home = ev.get("home_team") or ""
        title = f"{away} vs {home}".strip() if (away or home) else (ev.get("name") or "NFL game")
        state = ev.get("status_state") or "pre"
        games.append(
            {
                "id": str(ev.get("espn_id") or slugify(title)),
                "uid": f"espn:{ev.get('espn_id') or slugify(title)}",
                "source": "schedule",
                "espn_id": ev.get("espn_id"),
                "slug": slugify(title),
                "title": title,
                "url": None,
                "away_team": away or None,
                "home_team": home or None,
                "start_time": ev.get("date"),
                "kickoff_local": format_kickoff(ev.get("date") or "", tz_name),
                "status_state": state,
                "status_detail": ev.get("status_detail") or ev.get("status_name"),
                "venue": ev.get("venue"),
                **{key: ev.get(key) for key in LIVE_KEYS},
                "schedule_source": "espn",
                "live": state == "in",
                "ended": state == "post",
                "streams": [],
                "stream_count": 0,
                "resolved_count": 0,
                "all_wrapper_count": 0,
            }
        )
    return games


def fetch_summary(event_id: str, timeout: float = 10) -> dict[str, Any] | None:
    """Venue, weather, recent plays and team stats for one event."""
    try:
        r = requests.get(
            ESPN_SUMMARY,
            params={"event": event_id},
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=timeout,
        )
        r.raise_for_status()
        return normalize_summary(r.json())
    except Exception as e:
        log.warning("summary fetch failed for %s: %s", event_id, e)
        return None


#: Boxscore stats shown on the Stats tab, in display order.
SUMMARY_STATS = (
    ("totalYards", "Total yards"),
    ("netPassingYards", "Passing"),
    ("rushingYards", "Rushing"),
    ("turnovers", "Turnovers"),
    ("thirdDownEff", "3rd down"),
    ("possessionTime", "Possession"),
)


def normalize_summary(payload: dict) -> dict[str, Any]:
    """Reduce ESPN's game summary to what the Watch screen shows.

    Every section is optional: a game that hasn't kicked off has no plays
    or stats, and ESPN leaves out weather for indoor stadiums.
    """
    comp = ((payload.get("header") or {}).get("competitions") or [{}])[0]
    side_by_id = {
        str((t.get("team") or {}).get("id") or t.get("id") or ""): t.get("homeAway")
        for t in comp.get("competitors") or []
    }

    info = payload.get("gameInfo") or {}
    venue = info.get("venue") or {}
    address = venue.get("address") or {}
    weather = info.get("weather") or {}
    weather_text = None
    if weather.get("temperature") is not None:
        weather_text = f"{weather['temperature']}°"
        if weather.get("displayValue"):
            weather_text += f", {weather['displayValue'].lower()}"
    elif venue.get("indoor"):
        weather_text = "Indoors"
    broadcasts = [
        (b.get("media") or {}).get("shortName")
        for b in comp.get("broadcasts") or []
        if (b.get("media") or {}).get("shortName")
    ]

    plays = []
    drives = payload.get("drives") or {}
    drive_list = list(drives.get("previous") or [])
    if drives.get("current"):
        drive_list.append(drives["current"])
    for drive in drive_list:
        drive_team = str((drive.get("team") or {}).get("id") or "")
        for play in drive.get("plays") or []:
            team_id = str(((play.get("start") or {}).get("team") or {}).get("id") or drive_team)
            yards = play.get("statYardage")
            tag = None
            if play.get("scoringPlay"):
                tag = (play.get("scoringType") or {}).get("abbreviation") or "SCORE"
            elif isinstance(yards, (int, float)) and yards >= 20:
                tag = "BIG PLAY"
            plays.append({
                "text": play.get("text") or (play.get("type") or {}).get("text") or "",
                "period": (play.get("period") or {}).get("number"),
                "clock": (play.get("clock") or {}).get("displayValue"),
                "side": side_by_id.get(team_id),
                "tag": tag,
                "away_score": play.get("awayScore") if play.get("scoringPlay") else None,
                "home_score": play.get("homeScore") if play.get("scoringPlay") else None,
            })
    plays = [p for p in plays if p["text"]][-15:][::-1]

    stats = []
    teams = (payload.get("boxscore") or {}).get("teams") or []
    by_side = {}
    for t in teams:
        side = side_by_id.get(str((t.get("team") or {}).get("id") or ""))
        if side:
            by_side[side] = {s.get("name"): s.get("displayValue") for s in t.get("statistics") or []}
    if "away" in by_side and "home" in by_side:
        for key, label in SUMMARY_STATS:
            a, h = by_side["away"].get(key), by_side["home"].get(key)
            if a is not None and h is not None:
                stats.append({"key": key, "label": label, "away": a, "home": h})

    return {
        "venue": venue.get("fullName"),
        "city": ", ".join(x for x in (address.get("city"), address.get("state")) if x) or None,
        "weather": weather_text,
        "broadcast": broadcasts[0] if broadcasts else None,
        "attendance": info.get("attendance") or None,
        "plays": plays,
        "stats": stats,
    }


def sort_games_for_ui(games: list[dict]) -> list[dict]:
    """Live first, scheduled upcoming, undated upcoming, then ended."""

    def key(g: dict):
        state = g.get("status_state") or ""
        if state == "in" or g.get("live"):
            rank = 0
        elif state == "post" or g.get("ended"):
            rank = 2
        else:
            rank = 1
        start = g.get("start_time") or ""
        return (rank, not bool(start), start)

    return sorted(games, key=key)


if __name__ == "__main__":
    evs = fetch_scoreboard()
    print(len(evs), "events")
    for e in evs[:3]:
        print(e["name"], e["date"], e["status_state"], format_kickoff(e["date"]))
