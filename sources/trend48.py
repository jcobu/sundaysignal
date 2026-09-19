"""Trend48 public API source adapter.

Trend48 intentionally keeps provider URLs server-side and exposes a stable
``watch_url`` that is designed to be embedded. SundaySignal therefore keeps
these streams as iframe players instead of pretending they are direct HLS
playlists. Existing sources still use the normal wrapper -> HLS resolver.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import netfetch
from sources.base import Source

log = logging.getLogger(__name__)

SPORT_ALIASES = {
    "football": "soccer",
    "american-football": "football",
    "motor-sports": "motorsports",
    "fight": "combat",
    "boxing": "combat",
}

SPORT_LABELS = {
    "american-football": "Football",
    "football": "Soccer",
    "motor-sports": "Motorsports",
    "fight": "Combat Sports",
    "boxing": "Boxing",
}


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")


def _pinned_watch_url(watch_url: str, source: dict[str, Any]) -> str:
    """Pin Trend48's iframe player to one advertised provider feed."""
    name = str(source.get("source") or "").strip()
    stream = source.get("streamNo")
    if not name and stream is None:
        return watch_url
    parts = urlsplit(watch_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if name:
        query["source"] = name
    if stream is not None:
        query["stream"] = str(stream)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


class Trend48Source(Source):
    name = "trend48"

    def __init__(self, base_url: str | None = None, categories: str | None = None):
        self.base_url = (
            base_url or os.environ.get("SUNDAYSIGNAL_TREND48_URL", "https://trend48.st")
        ).rstrip("/")
        configured = categories
        if configured is None:
            configured = os.environ.get("SUNDAYSIGNAL_TREND48_CATEGORIES", "football,hockey")
        self.categories = {item.strip().lower() for item in configured.split(",") if item.strip()}
        self.endpoint = os.environ.get(
            "SUNDAYSIGNAL_TREND48_ENDPOINT", "/api/matches/all-today"
        ).strip()

    def _fetch_json(self, path: str) -> Any:
        url = path if path.startswith("http") else self.base_url + "/" + path.lstrip("/")
        body = netfetch.fetch(url, referer=self.base_url + "/")
        if not body:
            return None
        try:
            return json.loads(body)
        except (TypeError, json.JSONDecodeError) as exc:
            log.warning("[%s] invalid JSON from %s: %s", self.name, url, exc)
            return None

    def discover_games(self) -> list[dict[str, Any]]:
        matches = self._fetch_json(self.endpoint)
        if not isinstance(matches, list):
            log.error("[%s] match endpoint did not return a list", self.name)
            return []

        live_payload = self._fetch_json("/api/matches/live")
        live_ids = {
            str(item.get("id"))
            for item in (live_payload if isinstance(live_payload, list) else [])
            if item.get("id") is not None
        }
        return self.parse_matches(matches, live_ids=live_ids)

    def parse_matches(
        self, matches: list[dict[str, Any]], live_ids: set[str] | None = None
    ) -> list[dict[str, Any]]:
        live_ids = live_ids or set()
        games: list[dict[str, Any]] = []
        seen: set[str] = set()
        for match in matches:
            if not isinstance(match, dict):
                continue
            category = str(match.get("category") or "other").strip().lower()
            if self.categories and category not in self.categories:
                continue
            game_id = str(match.get("id") or "").strip()
            title = str(match.get("title") or "").strip()
            watch_url = str(match.get("watch_url") or "").strip()
            if not game_id or not title or not watch_url or game_id in seen:
                continue
            if not watch_url.startswith(("http://", "https://")):
                continue
            seen.add(game_id)

            sources = match.get("sources") if isinstance(match.get("sources"), list) else []
            streams = []
            for index, source in enumerate(sources or [{}], 1):
                if not isinstance(source, dict):
                    continue
                streams.append(
                    {
                        # Keep the upstream implementation private in the
                        # user-facing catalog. The UI presents these exactly
                        # like existing alternates: Source 1, Source 2, ...
                        "name": f"Source {index}",
                        "url": _pinned_watch_url(watch_url, source),
                        "embed_url": _pinned_watch_url(watch_url, source),
                        "media_url": None,
                        "source_type": "embed",
                        "hd": bool(source.get("hd")),
                        "language": source.get("language"),
                        "stream_index": index,
                    }
                )
            if not streams:
                streams.append(
                    {
                        "name": "Source 1",
                        "url": watch_url,
                        "embed_url": watch_url,
                        "media_url": None,
                        "source_type": "embed",
                    }
                )

            start_time, kickoff_local = self._format_start(match.get("date"))
            always_live = not match.get("date")
            is_live = game_id in live_ids or always_live
            sport = SPORT_ALIASES.get(category, category)
            games.append(
                {
                    "id": game_id,
                    "slug": _slugify(title) or game_id,
                    "title": title,
                    "url": watch_url,
                    "referer": self.base_url + "/",
                    "sport": sport,
                    "category": category,
                    "league": SPORT_LABELS.get(category, category.replace("-", " ").title()),
                    "start_time": start_time,
                    "kickoff_local": kickoff_local,
                    "status_state": "in" if is_live else "pre",
                    "live": is_live,
                    "ended": False,
                    "always_live": always_live,
                    "popular": bool(match.get("popular")),
                    "manual": bool(match.get("manual")),
                    "streams": streams,
                }
            )
        return games

    @staticmethod
    def _format_start(value: Any) -> tuple[str | None, str]:
        try:
            milliseconds = int(value)
        except (TypeError, ValueError):
            return None, ""
        if milliseconds <= 0:
            return None, ""
        dt = datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc)
        try:
            local = dt.astimezone(ZoneInfo(os.environ.get("TZ", "America/Los_Angeles")))
        except (ValueError, ZoneInfoNotFoundError):
            local = dt
        return dt.isoformat().replace("+00:00", "Z"), local.strftime("%a %b %d · %-I:%M %p %Z")

    def extract_streams(self, html: str, game_url: str) -> list[dict[str, Any]]:
        # API records include their streams directly, so this is only a safe
        # fallback for callers that still invoke the interface method.
        return []
