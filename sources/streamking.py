"""StreamKing NFL listings and embedded channel/game players."""
from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import netfetch
from sources.base import Source

log = logging.getLogger(__name__)
_MATCH_PATH = re.compile(r"^/match/([A-Za-z0-9-]+)/(\d+)/?$")


class StreamKingSource(Source):
    name = "streamking"
    coverage_group = "streamking"

    def __init__(self, base_url: str | None = None):
        self.base_url = (
            base_url or os.environ.get("SUNDAYSIGNAL_STREAMKING_BASE_URL", "https://streamking.cx")
        ).rstrip("/")

    def discover_games(self) -> list[dict[str, str]]:
        listing_url = self.base_url + "/categories/nfl/"
        html = netfetch.fetch(listing_url, referer=self.base_url + "/")
        if not html:
            log.warning("[%s] could not fetch NFL listing", self.name)
            return []
        return self.parse_listing(html)

    def parse_listing(self, html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        games = {}
        for link in soup.find_all("a", href=True):
            url = urljoin(self.base_url + "/", link["href"].strip())
            parsed = urlparse(url)
            match = _MATCH_PATH.fullmatch(parsed.path)
            if parsed.scheme not in ("http", "https") or not match:
                continue
            slug, game_id = match.groups()
            card = link.find_parent("article")
            heading = card.select_one(".match-card__title, h3") if card else None
            channel = link.select_one(".channel-card__name")
            title = (heading or channel).get_text(" ", strip=True) if heading or channel else slug.replace("-", " ")
            # Keep the existing channel convention (collapsed to just the name
            # by the UI), rather than treating 24/7 channels as scheduled games.
            if channel:
                title += " vs Live"
            games.setdefault(game_id, {
                "id": game_id, "slug": slug, "title": title, "url": url,
                "referer": self.base_url + "/categories/nfl/",
            })
        return list(games.values())

    def extract_streams(self, html: str, game_url: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        players = soup.select("iframe#stream-player")
        if not players:
            players = soup.select("iframe[allowfullscreen]")
        streams = []
        seen = set()
        for player in players:
            raw = (player.get("src") or "").strip()
            if not raw:
                continue
            url = urljoin(game_url, raw)
            if urlparse(url).scheme not in ("http", "https") or url in seen:
                continue
            seen.add(url)
            streams.append({
                "name": "StreamKing", "url": url, "referer": game_url,
                "badges": [], "media_url": None,
            })
        # Upcoming pages contain only a countdown/placeholder. They must not
        # become playback candidates until the site actually supplies a player.
        return streams
