"""Telegram channel source adapter.

Reads a channel's public web preview (t.me/s/<channel>), which serves
static HTML with no auth and no API key. The channel announces each game
with a link to its page, so it doubles as a way to survive the main site
rotating domains: whatever host the channel is currently posting is the
host we follow, rather than one baked into config.

The linked pages run the same software as nflbite, so stream extraction
is shared (see linkk_table).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

import netfetch
from sources.base import Source
from sources.linkk_table import extract_linkk_streams, rank_by_known_mirrors

log = logging.getLogger(__name__)

#: A game link on any host: /<team>-vs-<team>/<numeric id>. Anchoring on the
#: link shape rather than the post's wording keeps this working through
#: changes to emoji, captions and kickoff phrasing.
GAME_LINK = re.compile(r"^https?://[^/]+/([A-Za-z0-9\-]+-vs-[A-Za-z0-9\-]+)/(\d+)/?$", re.I)


class TelegramSource(Source):
    name = "telegram"

    def __init__(self, channel: str | None = None):
        self.channel = (
            channel or os.environ.get("SUNDAYSIGNAL_TELEGRAM_CHANNEL", "nflbite_official")
        ).strip().lstrip("@")
        self.base_url = f"https://t.me/s/{self.channel}"

    def discover_games(self) -> list[dict[str, str]]:
        html = netfetch.fetch(self.base_url, referer="https://t.me/")
        if not html:
            log.error("[%s] could not fetch channel %s", self.name, self.channel)
            return []
        return self.parse_channel(html)

    def parse_channel(self, html: str) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        games: dict[str, dict[str, str]] = {}
        # Posts repeat as a game is re-announced, so first link per id wins.
        for body in soup.select(".tgme_widget_message_text"):
            for a in body.find_all("a", href=True):
                url = a["href"].strip()
                m = GAME_LINK.match(url)
                if not m:
                    continue
                slug, game_id = m.group(1), m.group(2)
                if game_id in games:
                    continue
                parsed = urlparse(url)
                games[game_id] = {
                    "id": game_id,
                    "slug": slug,
                    "title": slug.replace("-", " "),
                    "url": url,
                    # The linked host isn't this channel, so send its own
                    # origin as the Referer rather than t.me.
                    "referer": f"{parsed.scheme}://{parsed.netloc}/",
                }
        if games:
            hosts = sorted({urlparse(g["url"]).netloc for g in games.values()})
            log.info("[%s] %d game(s) linked from %s", self.name, len(games), ", ".join(hosts))
        return list(games.values())

    def extract_streams(self, html: str, game_url: str) -> list[dict[str, Any]]:
        return extract_linkk_streams(html)

    def rank_stream(self, stream: dict[str, Any]) -> int:
        return rank_by_known_mirrors(stream)
