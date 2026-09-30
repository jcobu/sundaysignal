"""Primary aggregator-site source adapter."""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

import netfetch
from sources.base import Source
from sources.linkk_table import extract_linkk_streams, rank_by_known_mirrors

log = logging.getLogger(__name__)


class SiteSource(Source):
    name = "nflbite"

    def __init__(self, base_url: str | None = None):
        self.base_url = (
            base_url or os.environ.get("SUNDAYSIGNAL_BASE_URL", "https://www.nflbite.is")
        ).rstrip("/")

    def discover_games(self) -> list[dict[str, str]]:
        html = netfetch.fetch(self.base_url + "/", referer=self.base_url + "/")
        if not html:
            html = netfetch.fetch(self.base_url + "/date/today", referer=self.base_url + "/")
        if not html:
            log.error("[%s] could not fetch any listing page", self.name)
            return []

        return self.parse_listing(html)

    def parse_listing(self, html: str) -> list[dict[str, str]]:
        """Extract game pages from an NFLbite listing.

        NFLbite historically used relative links on its own host.  It now
        serves absolute links from a rotating ``links.nflbite.*`` host, so
        match the stable path shape after resolving either form instead of
        requiring the raw href to begin with ``/``.
        """
        soup = BeautifulSoup(html, "lxml")
        games: dict[str, dict[str, str]] = {}
        for a in soup.find_all("a", href=True):
            game_url = urljoin(self.base_url + "/", a["href"].strip())
            parsed = urlparse(game_url)
            m = re.match(r"^/([A-Za-z0-9\-]+-vs-[A-Za-z0-9\-]+)/(\d+)/?$", parsed.path)
            if not m:
                continue
            slug, game_id = m.group(1), m.group(2)
            if game_id not in games:
                origin = f"{parsed.scheme}://{parsed.netloc}/"
                games[game_id] = {
                    "id": game_id,
                    "slug": slug,
                    "title": slug.replace("-", " "),
                    "url": game_url,
                    "referer": origin,
                }
        return list(games.values())

    def extract_streams(self, html: str, game_url: str) -> list[dict[str, Any]]:
        return extract_linkk_streams(html)

    def rank_stream(self, stream: dict[str, Any]) -> int:
        return rank_by_known_mirrors(stream)
