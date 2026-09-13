"""Source adapter registry.

`SUNDAYSIGNAL_SOURCES` selects which adapters run, comma-separated, in the
order listed. Unknown names are skipped with a warning rather than killing
the crawl, so a typo or a retired adapter can't take the whole app down.
"""

from __future__ import annotations

import logging
import os

import netfetch
from sources.base import Source
from sources.nflbite import NflbiteSource

log = logging.getLogger(__name__)

REGISTRY: dict[str, type[Source]] = {
    NflbiteSource.name: NflbiteSource,
}

DEFAULT_SOURCES = (NflbiteSource.name,)


def get_sources() -> list[Source]:
    names = [
        n.strip()
        for n in os.environ.get("SUNDAYSIGNAL_SOURCES", ",".join(DEFAULT_SOURCES)).split(",")
        if n.strip()
    ]
    sources: list[Source] = []
    for name in names:
        cls = REGISTRY.get(name)
        if cls is None:
            log.warning("unknown source %r (known: %s)", name, ", ".join(sorted(REGISTRY)))
            continue
        sources.append(cls())
    if not sources:
        log.warning("no valid sources configured; falling back to %s", DEFAULT_SOURCES[0])
        sources.append(REGISTRY[DEFAULT_SOURCES[0]]())
    # A source site must always be retried, never cached as dead. This also
    # clears a stale entry left by an earlier run, so one bad night can't
    # keep the crawler blind afterwards.
    for src in sources:
        netfetch.protect_host(netfetch.host_of(src.base_url))
    return sources
