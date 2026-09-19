"""Tests for the optional Plex login gate (plex_auth.py + its wiring into
webapp.py). Disabled by default — every existing route must behave exactly
as before unless SUNDAYSIGNAL_PLEX_OWNER_TOKEN (plex_auth.ENABLED) is set.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import plex_auth
import webapp


def test_persisted_value_creates_then_reuses_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(plex_auth, "_STATE_DIR", tmp_path)
    calls = []

    def generate():
        calls.append(1)
        return "generated-value"

    first = plex_auth.persisted_value("thing.txt", generate)
    second = plex_auth.persisted_value("thing.txt", generate)

    assert first == second == "generated-value"
    assert len(calls) == 1, "the generator must only run once; the second call reads the file"
    assert (tmp_path / "thing.txt").read_text(encoding="utf-8") == "generated-value"


def test_is_authorized_false_when_plex_login_disabled(monkeypatch):
    monkeypatch.setattr(plex_auth, "ENABLED", False)
    assert plex_auth.is_authorized({"id": 1, "email": "anyone@example.com"}) is False


def test_is_authorized_matches_the_server_owner(monkeypatch):
    monkeypatch.setattr(plex_auth, "ENABLED", True)
    monkeypatch.setattr(plex_auth, "ALLOWED_USERS", set())
    monkeypatch.setattr(plex_auth, "_owner_account_cached", lambda: {"id": 111})
    monkeypatch.setattr(plex_auth, "_friends_cached", lambda: [])

    assert plex_auth.is_authorized({"id": 111, "email": "owner@example.com"}) is True
    assert plex_auth.is_authorized({"id": 222, "email": "stranger@example.com"}) is False


def test_is_authorized_matches_a_shared_friend_by_id_or_email(monkeypatch):
    monkeypatch.setattr(plex_auth, "ENABLED", True)
    monkeypatch.setattr(plex_auth, "ALLOWED_USERS", set())
    monkeypatch.setattr(plex_auth, "_owner_account_cached", lambda: {"id": 111})
    monkeypatch.setattr(
        plex_auth,
        "_friends_cached",
        lambda: [{"id": "222", "username": "friend", "email": "friend@example.com"}],
    )

    assert plex_auth.is_authorized({"id": 222, "email": "friend@example.com"}) is True
    # Matching by email alone covers a friend whose numeric id we didn't get.
    assert plex_auth.is_authorized({"id": 999, "email": "FRIEND@example.com"}) is True
    assert plex_auth.is_authorized({"id": 333, "email": "nobody@example.com"}) is False


def test_is_authorized_matches_the_explicit_allowlist(monkeypatch):
    monkeypatch.setattr(plex_auth, "ENABLED", True)
    monkeypatch.setattr(plex_auth, "ALLOWED_USERS", {"vip@example.com"})
    monkeypatch.setattr(plex_auth, "_owner_account_cached", lambda: {"id": 111})
    monkeypatch.setattr(plex_auth, "_friends_cached", lambda: [])

    assert plex_auth.is_authorized({"id": 555, "email": "vip@example.com"}) is True
    assert plex_auth.is_authorized({"id": 556, "email": "other@example.com"}) is False


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "JSON_PATH", tmp_path / "sundaysignal_streams.json")
    monkeypatch.setattr(webapp, "STATUS_PATH", tmp_path / "last_scrape_status.json")
    monkeypatch.setattr(webapp, "espn_schedule", None)
    return webapp.app.test_client()


def test_plex_disabled_leaves_every_route_open(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp.plex_auth, "ENABLED", False)
    client = _client(monkeypatch, tmp_path)

    assert client.get("/").status_code == 200
    assert client.get("/api/streams").status_code == 200
    assert client.get("/login").status_code == 404


def test_plex_enabled_blocks_unauthenticated_requests(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp.plex_auth, "ENABLED", True)
    client = _client(monkeypatch, tmp_path)

    page = client.get("/", follow_redirects=False)
    assert page.status_code == 302
    assert page.headers["Location"] == "/login"

    assert client.get("/api/streams").status_code == 401
    assert client.get("/proxy?url=https://example.com/a.m3u8").status_code == 401
    assert client.get("/login").status_code == 200


def test_plex_enabled_never_gates_iptv_or_monitoring_endpoints(monkeypatch, tmp_path):
    """M3U/EPG/health must keep working without a Plex login — VLC/TiviMate
    and monitoring tools can't do a browser-based OAuth flow."""
    monkeypatch.setattr(webapp.plex_auth, "ENABLED", True)
    client = _client(monkeypatch, tmp_path)

    assert client.get("/playlist.m3u").status_code == 200
    assert client.get("/epg.xml").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_plex_login_flow_denies_an_unrelated_account(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp.plex_auth, "ENABLED", True)
    client = _client(monkeypatch, tmp_path)

    monkeypatch.setattr(webapp.plex_auth, "create_pin", lambda: {"id": 1, "code": "ABCD"})
    monkeypatch.setattr(webapp.plex_auth, "check_pin", lambda pin_id: {"id": 1, "authToken": "tok"})
    monkeypatch.setattr(
        webapp.plex_auth, "fetch_account", lambda token: {"id": 999, "username": "stranger", "email": "s@x.com"}
    )
    monkeypatch.setattr(webapp.plex_auth, "is_authorized", lambda account: False)

    start = client.post("/auth/plex/pin").get_json()
    assert start["ok"] is True and "app.plex.tv/auth" in start["authUrl"]
    # The raw code (not just the popup URL) is what a client with no browser
    # — the Fire TV app — shows so someone can redeem it at plex.tv/link
    # from another device.
    assert start["code"] == "ABCD"

    poll = client.get(f"/auth/plex/poll/{start['id']}").get_json()
    assert poll == {
        "ok": True,
        "authenticated": False,
        "denied": True,
        "error": "This Plex account doesn't have access to this server.",
    }
    assert client.get("/", follow_redirects=False).status_code == 302


def test_plex_login_flow_grants_and_logout_revokes_a_session(monkeypatch, tmp_path):
    monkeypatch.setattr(webapp.plex_auth, "ENABLED", True)
    client = _client(monkeypatch, tmp_path)

    monkeypatch.setattr(webapp.plex_auth, "create_pin", lambda: {"id": 2, "code": "EFGH"})
    monkeypatch.setattr(webapp.plex_auth, "check_pin", lambda pin_id: {"id": 2, "authToken": "tok"})
    monkeypatch.setattr(
        webapp.plex_auth, "fetch_account", lambda token: {"id": 222, "username": "friend1", "email": "f@x.com"}
    )
    monkeypatch.setattr(webapp.plex_auth, "is_authorized", lambda account: True)

    poll = client.get("/auth/plex/poll/2").get_json()
    assert poll == {"ok": True, "authenticated": True}

    page = client.get("/")
    assert page.status_code == 200
    body = page.get_data(as_text=True)
    assert "friend1" in body and "Log out" in body
    assert client.get("/api/streams").status_code == 200

    logout = client.post("/logout", follow_redirects=False)
    assert logout.status_code == 302 and logout.headers["Location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 302
