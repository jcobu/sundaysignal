"""Shared outbound HTTP layer: session, proxy pool, dead-host cache, fetch.

Lives apart from the scraper core and the source adapters so both can use it
without importing each other.
"""

from __future__ import annotations

import json
import logging
import os
import random
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
SESSION = requests.Session()
SESSION.headers.update(BASE_HEADERS)

DEFAULT_TIMEOUT = 12.0

# Optional pool of outbound proxies ("http://user:pass@host:port,socks5://host:port")
# to spread third-party lookups across egress IPs. One is picked at random per
# fetch() call. Empty (default) = direct connection, unchanged behavior.
_PROXY_POOL = [p.strip() for p in os.environ.get("SUNDAYSIGNAL_PROXIES", "").split(",") if p.strip()]

# Hosts that repeatedly fail DNS/timeout — skipped until the entry expires.
# Maps host -> ISO timestamp of the last observed failure.
_DEAD_HOSTS: dict[str, str] = {}
_DEAD_HOST_TTL_HOURS = float(os.environ.get("SUNDAYSIGNAL_DEAD_HOST_TTL_HOURS", "24"))

# Source sites themselves are never cached as dead. They're the one host we
# must always retry: a single transient DNS blip would otherwise blind the
# crawler for the whole TTL, and every later crawl would find zero games
# without ever attempting a request.
_PROTECTED_HOSTS: set[str] = set()


def protect_host(host: str) -> None:
    if host:
        _PROTECTED_HOSTS.add(host.lower())
        _DEAD_HOSTS.pop(host.lower(), None)

# Substrings marking wrappers/embeds that are never worth fetching.
SKIP_HOST_SUBSTR = (
    "selltvonline.shop",
    "sportsz.one",
    "sportsworlds.shop",
    "mjumbo.icu",
    "youtube.com",
    "live_chat",
)


def pick_proxies() -> dict[str, str] | None:
    if not _PROXY_POOL:
        return None
    proxy = random.choice(_PROXY_POOL)
    return {"http": proxy, "https": proxy}


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def mark_dead(host: str) -> None:
    if host and host.lower() not in _PROTECTED_HOSTS:
        _DEAD_HOSTS[host] = datetime.now(timezone.utc).isoformat()


def is_dead(host: str) -> bool:
    if host and host.lower() in _PROTECTED_HOSTS:
        return False
    ts = _DEAD_HOSTS.get(host)
    if not ts:
        return False
    try:
        age_hours = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds() / 3600
    except ValueError:
        return True
    if age_hours > _DEAD_HOST_TTL_HOURS:
        del _DEAD_HOSTS[host]
        return False
    return True


def dead_hosts() -> dict[str, str]:
    return _DEAD_HOSTS


def clear_dead_hosts() -> None:
    """Give every mirror a fresh shot instead of waiting out the TTL.

    A manual rescrape is a deliberate "try harder" action — a mirror cached
    dead from a blip up to SUNDAYSIGNAL_DEAD_HOST_TTL_HOURS ago (24h by
    default) shouldn't stay silently skipped through it just because an
    earlier cycle happened to catch it down.
    """
    _DEAD_HOSTS.clear()


def load_dead_hosts(path: str) -> None:
    try:
        if not os.path.isfile(path):
            return
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _DEAD_HOSTS.update(data)
            log.info("loaded %d dead-host entries from %s", len(data), path)
    except Exception as e:
        log.warning("failed to load dead hosts from %s: %s", path, e)


def save_dead_hosts(path: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_DEAD_HOSTS, f, indent=2)
    except OSError as e:
        log.warning("failed to save dead hosts to %s: %s", path, e)


def fetch(url: str, referer: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str | None:
    host = host_of(url)
    if host and is_dead(host):
        return None
    if any(s in url for s in SKIP_HOST_SUBSTR):
        return None
    try:
        headers = dict(BASE_HEADERS)
        if referer:
            headers["Referer"] = referer
        resp = SESSION.get(url, headers=headers, timeout=timeout, proxies=pick_proxies())
        resp.raise_for_status()
        return resp.text
    except requests.exceptions.ProxyError as e:
        # The proxy itself misbehaved — not evidence the target host is dead.
        log.error("proxy failure fetching %s: %s", url, e)
        return None
    except requests.exceptions.ConnectionError as e:
        if host:
            mark_dead(host)
        log.debug("failed to fetch %s: %s", url, e)
        return None
    except requests.RequestException as e:
        # DNS / timeout — mark host dead so we do not hammer it
        err = str(e).lower()
        if host and ("nameresolution" in err or "failed to resolve" in err or "timed out" in err):
            mark_dead(host)
        log.debug("failed to fetch %s: %s", url, e)
        return None


def fetch_bytes(
    url: str,
    referer: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    max_bytes: int = 2048,
    range_request: bool = True,
) -> tuple[bytes, str] | None:
    """Fetch only the beginning of a resource and return (body, final URL).

    HLS health checks use this for manifests and media segments.  Streaming
    the response and closing it after ``max_bytes`` keeps a probe from
    downloading an entire multi-megabyte segment when a server ignores the
    Range header.  The final URL matters when a manifest redirects and then
    contains relative variant or segment paths.
    """
    host = host_of(url)
    if host and is_dead(host):
        return None
    if any(s in url for s in SKIP_HOST_SUBSTR):
        return None
    try:
        headers = dict(BASE_HEADERS)
        if range_request:
            headers["Range"] = f"bytes=0-{max(0, max_bytes - 1)}"
        if referer:
            headers["Referer"] = referer
        with SESSION.get(
            url,
            headers=headers,
            timeout=timeout,
            proxies=pick_proxies(),
            stream=True,
        ) as resp:
            resp.raise_for_status()
            body = bytearray()
            for chunk in resp.iter_content(chunk_size=min(8192, max_bytes)):
                if not chunk:
                    continue
                remaining = max_bytes - len(body)
                body.extend(chunk[:remaining])
                if len(body) >= max_bytes:
                    break
            return bytes(body), resp.url
    except requests.exceptions.ProxyError as e:
        log.error("proxy failure fetching %s: %s", url, e)
        return None
    except requests.exceptions.ConnectionError as e:
        if host:
            mark_dead(host)
        log.debug("failed to fetch %s: %s", url, e)
        return None
    except requests.RequestException as e:
        err = str(e).lower()
        if host and ("nameresolution" in err or "failed to resolve" in err or "timed out" in err):
            mark_dead(host)
        log.debug("failed to fetch %s: %s", url, e)
        return None
