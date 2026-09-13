"""Optional failure notifications.

Set SUNDAYSIGNAL_NOTIFY_URL to an ntfy topic (https://ntfy.sh/your-topic),
a Discord webhook, or any endpoint that accepts a JSON post. Disabled when
unset, which is the default.
"""

from __future__ import annotations

import json
import logging
import os

import requests

log = logging.getLogger(__name__)

NOTIFY_URL = os.environ.get("SUNDAYSIGNAL_NOTIFY_URL", "").strip()
#: Consecutive failed/empty crawls before the first alert fires.
NOTIFY_AFTER = int(os.environ.get("SUNDAYSIGNAL_NOTIFY_AFTER", "3"))
TIMEOUT = 10


def enabled() -> bool:
    return bool(NOTIFY_URL)


def send(title: str, message: str) -> None:
    """Best-effort notification — never raises, never blocks a crawl."""
    if not NOTIFY_URL:
        return
    try:
        if "discord.com/api/webhooks" in NOTIFY_URL or "discordapp.com/api/webhooks" in NOTIFY_URL:
            payload = {"content": f"**{title}**\n{message}"}
            requests.post(NOTIFY_URL, json=payload, timeout=TIMEOUT).raise_for_status()
        elif "ntfy" in NOTIFY_URL:
            requests.post(
                NOTIFY_URL,
                data=message.encode("utf-8"),
                headers={"Title": title},
                timeout=TIMEOUT,
            ).raise_for_status()
        else:
            requests.post(
                NOTIFY_URL,
                data=json.dumps({"title": title, "message": message}),
                headers={"Content-Type": "application/json"},
                timeout=TIMEOUT,
            ).raise_for_status()
        log.info("notification sent: %s", title)
    except Exception as e:
        log.warning("notification failed (%s): %s", NOTIFY_URL.split("/")[2] if "/" in NOTIFY_URL else "?", e)
