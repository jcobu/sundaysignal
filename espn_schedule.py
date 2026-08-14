"""Fetch NFL schedule/status from ESPN's public JSON (unofficial, no key)."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

import requests

ESPN_SCOREBOARD = (
    "https://site.web.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
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
        print(f"[espn] scoreboard fetch failed: {e}")
        return []

    events_out = []
    for e in payload.get("events") or []:
        comps = e.get("competitions") or [{}]
        c = comps[0]
        home = away = home_abbr = away_abbr = None
        for t in c.get("competitors") or []:
            team = t.get("team") or {}
            name = team.get("displayName") or team.get("name") or ""
            abbr = team.get("abbreviation") or ""
            if t.get("homeAway") == "home":
                home, home_abbr = name, abbr
            elif t.get("homeAway") == "away":
                away, away_abbr = name, abbr
        st = (e.get("status") or {}).get("type") or {}
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
            }
        )
    return events_out


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


def sort_games_for_ui(games: list[dict]) -> list[dict]:
    """Live first, then upcoming by kickoff, then ended."""

    def key(g: dict):
        state = g.get("status_state") or ""
        if state == "in" or g.get("live"):
            rank = 0
        elif state == "post" or g.get("ended"):
            rank = 2
        else:
            rank = 1
        start = g.get("start_time") or ""
        return (rank, start)

    return sorted(games, key=key)


if __name__ == "__main__":
    evs = fetch_scoreboard()
    print(len(evs), "events")
    for e in evs[:3]:
        print(e["name"], e["date"], e["status_state"], format_kickoff(e["date"]))
