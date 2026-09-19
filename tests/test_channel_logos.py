"""RedZone/NFL Network are whole-slate channels, not teams — webapp.py gives
them a fixed logo instead of the blank space team_abbr() would leave. These
logos are self-hosted under static/ (not hotlinked to a third-party CDN) so
a dead upstream link can't silently blank the icon again.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import webapp


def test_redzone_and_nfl_network_logos_are_self_hosted():
    assert webapp.REDZONE_LOGO_URL.startswith("/static/")
    assert webapp.NFL_NETWORK_LOGO_URL.startswith("/static/")


def test_parse_matchup_assigns_the_nfl_network_logo():
    parsed = webapp.parse_matchup("NFL Network vs Live", "")
    assert parsed["away_logo"] == webapp.NFL_NETWORK_LOGO_URL
    assert parsed["is_matchup"] is False
    assert parsed["display_title"] == "NFL Network"


def test_parse_matchup_assigns_the_redzone_logo():
    parsed = webapp.parse_matchup("NFL RedZone vs Live", "")
    assert parsed["away_logo"] == webapp.REDZONE_LOGO_URL


def test_static_logo_files_are_served():
    client = webapp.app.test_client()
    for path in (webapp.REDZONE_LOGO_URL, webapp.NFL_NETWORK_LOGO_URL):
        resp = client.get(path)
        assert resp.status_code == 200
        assert "svg" in resp.content_type


def test_iptv_logo_makes_a_self_hosted_path_absolute():
    """TiviMate/VLC fetch tvg-logo outside the browser, so a relative
    "/static/..." path is useless to them without a request context to
    make it absolute against — same treatment /proxy URLs already get."""
    with webapp.app.test_request_context("/playlist.m3u", base_url="http://192.168.1.50:8765"):
        logo = webapp._iptv_logo({"home_logo": None, "away_logo": webapp.NFL_NETWORK_LOGO_URL})
    assert logo == "http://192.168.1.50:8765/static/nfl_network.svg"


def test_iptv_logo_leaves_an_already_absolute_url_untouched():
    with webapp.app.test_request_context("/playlist.m3u"):
        logo = webapp._iptv_logo({"home_logo": "https://a.espncdn.com/i/teamlogos/nfl/500/atl.png", "away_logo": None})
    assert logo == "https://a.espncdn.com/i/teamlogos/nfl/500/atl.png"
