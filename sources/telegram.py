"""Telegram channel source adapter.

Reads a channel's public web preview (t.me/s/<channel>), which serves
static HTML with no auth and no API key. The channel announces each game
with a link to its page, so it doubles as a way to survive the main site
rotating domains: whatever host the channel is currently posting is the
host we follow, rather than one baked into config.

Linked pages may be stream tables or direct players. Alternate hosts are
retained and the core recursively resolves candidates with HLS health checks.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any
from urllib.parse import urlparse, urljoin, parse_qs
from datetime import datetime, timedelta, timezone
import hashlib

from bs4 import BeautifulSoup

import netfetch
from sources.base import Source
from sources.linkk_table import extract_linkk_streams, rank_by_known_mirrors

log = logging.getLogger(__name__)



class TelegramSource(Source):
    name = "telegram"
    coverage_group = "telegram"
    coverage_by_url = True

    def __init__(self, channel: str | None = None):
        self.channel = (
            channel or os.environ.get("SUNDAYSIGNAL_TELEGRAM_CHANNEL", "nflbite_official")
        ).strip().lstrip("@")
        self.base_url = f"https://t.me/s/{self.channel}"

    def discover_games(self) -> list[dict[str, str]]:
        try:
            max_pages = max(1, min(10, int(os.environ.get("SUNDAYSIGNAL_TELEGRAM_MAX_PAGES", "3"))))
            age_hours = max(1, min(336, float(os.environ.get("SUNDAYSIGNAL_TELEGRAM_LOOKBACK_HOURS", "168"))))
        except ValueError:
            max_pages, age_hours = 3, 168
        cutoff = datetime.now(timezone.utc) - timedelta(hours=age_hours)
        url = self.base_url
        seen_pages = set()
        games = {}
        for _ in range(max_pages):
            if url in seen_pages:
                break
            seen_pages.add(url)
            html = netfetch.fetch(url, referer="https://t.me/")
            if not html:
                log.warning("[%s] could not fetch channel page", self.name)
                break
            for game in self.parse_channel(html, cutoff=cutoff):
                games.setdefault(game["url"], game)
            soup = BeautifulSoup(html, "lxml")
            older = soup.select_one("a.tme_messages_more[href]")
            if not older:
                break
            next_url = urljoin(url, older["href"])
            parsed = urlparse(next_url)
            if (parsed.scheme != "https" or parsed.netloc != "t.me"
                    or parsed.path.rstrip("/") != urlparse(self.base_url).path
                    or not parse_qs(parsed.query).get("before", [""])[0].isdigit()):
                break
            times = []
            for node in soup.select(".tgme_widget_message time[datetime]"):
                try:
                    value = datetime.fromisoformat(node["datetime"].replace("Z", "+00:00"))
                    if value.tzinfo:
                        times.append(value)
                except ValueError:
                    pass
            if times and max(times) < cutoff:
                break
            url = next_url
        log.info("[%s] discovered %d distinct game links across %d page(s)", self.name, len(games), len(seen_pages))
        for game in games.values():
            netfetch.protect_host(netfetch.host_of(game["url"]))
        return list(games.values())

    def parse_channel(self, html: str, *, cutoff: datetime | None = None) -> list[dict[str, str]]:
        soup = BeautifulSoup(html, "lxml")
        games: dict[str, dict[str, str]] = {}
        # Newest announcements win for an identical URL; retain alternate
        # hosts for the same fixture so one dead domain cannot suppress them.
        for body in reversed(soup.select(".tgme_widget_message_text")):
            post = body.find_parent(class_="tgme_widget_message")
            posted = post.select_one("time[datetime]") if post else None
            if cutoff and posted:
                try:
                    value = datetime.fromisoformat(posted["datetime"].replace("Z", "+00:00"))
                    if value.tzinfo and value < cutoff:
                        continue
                except ValueError:
                    pass
            for a in body.find_all("a", href=True):
                url = a["href"].strip()
                parsed = urlparse(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname:
                    continue
                # Match only the path so query strings and rotated /match/
                # URLs do not make a real game disappear.
                match = re.fullmatch(r"/(?:match/)?([A-Za-z0-9-]+-vs-[A-Za-z0-9-]+)/(\d+)/?", parsed.path, re.I)
                if match:
                    slug, game_id = match.groups()
                else:
                    # Some announcements link directly to an opaque player;
                    # accept only anchors that explicitly name a matchup.
                    label = re.sub(r"\s*[-–|]\s*Live Stream.*$", "", a.get_text(" ", strip=True), flags=re.I)
                    if not re.fullmatch(r"[A-Za-z0-9 .'-]+\s+vs\.?\s+[A-Za-z0-9 .'-]+", label, re.I):
                        continue
                    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
                    game_id = hashlib.sha256(url.encode()).hexdigest()[:16]
                if url in games:
                    continue
                games[url] = {
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
        streams = extract_linkk_streams(html, base_url=game_url)
        if streams:
            return streams
        soup = BeautifulSoup(html, "lxml")
        # Direct player pages can be announced instead of a stream table.
        if (soup.select_one("iframe[src], iframe[data-src], [data-embed-url]")
                or html.lstrip().startswith("#EXTM3U") or ".m3u8" in html or "window._econfig" in html
                or re.search(r"fetch\(\s*[\"']api/player\.php\?id=", html)):
            return [{"name": "Telegram player", "url": game_url, "media_url": None,
                     "badges": [], "referer": game_url}]
        return []

    def rank_stream(self, stream: dict[str, Any]) -> int:
        return rank_by_known_mirrors(stream)
