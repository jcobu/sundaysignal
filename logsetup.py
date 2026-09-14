"""Shared logging configuration.

SUNDAYSIGNAL_LOG_LEVEL sets the global level (default INFO). Dead mirrors
and per-hop resolve detail log at DEBUG so they stay out of the way until
something needs diagnosing; SUNDAYSIGNAL_DEBUG_RESOLVE=1 turns just the
resolver chatter on without making everything else verbose.
"""

from __future__ import annotations

import logging
import os

_CONFIGURED = False


def configure() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    level = os.environ.get("SUNDAYSIGNAL_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    if os.environ.get("SUNDAYSIGNAL_DEBUG_RESOLVE"):
        logging.getLogger("sundaysignal_scraper").setLevel(logging.DEBUG)
        logging.getLogger("netfetch").setLevel(logging.DEBUG)
    _CONFIGURED = True
