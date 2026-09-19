"""Interface every stream source adapter implements.

These aggregator sites change markup and rotate domains constantly, so each
one lives behind this small interface: the core owns fetching, the dead-host
cache, the nested-iframe resolve chain and output, while a source only knows
how to list today's games and pull candidate wrapper URLs off a game page.
Adding a second site means adding one module here, not touching the core.
"""

from __future__ import annotations

from typing import Any


class Source:
    #: Short identifier, used in logs and in the `source` field of each game.
    name: str = "base"

    #: Site root, also used as the Referer when following its wrapper links.
    base_url: str = ""

    def discover_games(self) -> list[dict[str, Any]]:
        """Return the games this source is currently listing.

        Each entry needs at least: id, slug, title, url. An entry may also
        carry `referer` when its page lives on a different host than the
        source itself — as it does for a channel that links out elsewhere.

        API-backed sources may provide a `streams` list directly. Those
        entries bypass the game-page fetch and may contain either a normal
        wrapper `url` for the HLS resolver or an `embed_url` with
        `source_type="embed"` for providers that intentionally expose only
        an iframe player.
        """
        raise NotImplementedError

    def extract_streams(self, html: str, game_url: str) -> list[dict[str, Any]]:
        """Return candidate wrapper streams from a fetched game page.

        Each entry needs at least: name, url, media_url (None initially).
        These are *wrapper* pages, not playable media — the core resolves
        them to HLS by following their nested player iframes.
        """
        raise NotImplementedError

    def rank_stream(self, stream: dict[str, Any]) -> int:
        """Ordering preference for resolve attempts; lower is tried first.

        Only an ordering hint — every candidate still counts toward the
        per-game cap, so a lower-ranked provider is used when it works.
        """
        return 0
