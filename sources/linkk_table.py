"""Stream-table parsing shared by sites running the same software.

nflbite and the domains its Telegram channel points at (sportslinks.is and
whatever it rotates to next) serve the same game page: a table whose rows
carry the wrapper URL in a hidden `linkk<n>` input. Keeping the parser in
one place means a markup change is fixed once.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

_LINKK_ID = re.compile(r"^linkk\d+$")


def extract_linkk_streams(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    streams: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in soup.find_all("tr"):
        hid = row.find("input", attrs={"type": "hidden", "id": _LINKK_ID})
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

    # Some pages carry hidden inputs outside any table row.
    for hid in soup.find_all("input", attrs={"type": "hidden", "id": _LINKK_ID}):
        stream_url = (hid.get("value") or "").strip()
        if stream_url and stream_url not in seen:
            seen.add(stream_url)
            streams.append({"name": "unknown", "url": stream_url, "badges": [], "media_url": None})

    return streams


_KNOWN_MIRROR_RE = re.compile(r"^https?://(live\d*)\.totalsporte", re.I)


def rank_by_known_mirrors(stream: dict[str, Any]) -> int:
    """Ordering hint only — every candidate still gets tried.

    This mirror rotates domains/TLDs while keeping the same brand and a
    "live"/"live2" subdomain convention — seen as both
    live2.totalsporteks.* and live.totalsporteke.st. Match the stable
    "live*.totalsporte" prefix instead of one exact spelling, which goes
    stale the next time the site rotates.
    """
    url = stream.get("url", "")
    m = _KNOWN_MIRROR_RE.match(url)
    if m:
        return 0 if m.group(1) == "live2" else 1
    return 2
