"""nflbite source adapter."""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import netfetch
from sources.base import Source

log = logging.getLogger(__name__)


class NflbiteSource(Source):
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

        soup = BeautifulSoup(html, "lxml")
        games: dict[str, dict[str, str]] = {}
        for a in soup.find_all("a", href=True):
            m = re.match(r"^/([A-Za-z0-9\-]+-vs-[A-Za-z0-9\-]+)/(\d+)/?$", a["href"])
            if not m:
                continue
            slug, game_id = m.group(1), m.group(2)
            if game_id not in games:
                games[game_id] = {
                    "id": game_id,
                    "slug": slug,
                    "title": slug.replace("-", " "),
                    "url": urljoin(self.base_url, a["href"]),
                }
        return list(games.values())

    def extract_streams(self, html: str, game_url: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        streams: list[dict[str, Any]] = []
        seen: set[str] = set()

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

    def rank_stream(self, stream: dict[str, Any]) -> int:
        url = stream.get("url", "")
        if "live2.totalsporteks" in url:
            return 0
        if "totalsporteks" in url:
            return 1
        return 2
