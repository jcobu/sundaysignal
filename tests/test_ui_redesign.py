"""Backend pieces behind the redesigned web UI: live fields, game details,
the users page, password changes and the main page shell."""
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import account_auth as auth
import espn_schedule
import webapp
from test_account_auth import bootstrap, post


SCOREBOARD = {"events": [{
    "id": "401671234",
    "name": "Kansas City Chiefs at Buffalo Bills",
    "date": "2026-11-01T18:00Z",
    "status": {"period": 3, "displayClock": "4:12",
               "type": {"state": "in", "name": "STATUS_IN_PROGRESS", "description": "In Progress", "shortDetail": "4:12 - 3rd"}},
    "competitions": [{
        "venue": {"fullName": "Highmark Stadium"},
        "broadcasts": [{"names": ["CBS"]}],
        "situation": {"downDistanceText": "3rd & 7 at KC 35", "shortDownDistanceText": "3rd & 7", "possessionText": "KC 35", "possession": "12",
                      "isRedZone": False, "lastPlay": {"text": "P.Mahomes pass short right to T.Kelce for 6 yards"}},
        "competitors": [
            {"homeAway": "home", "score": "17", "team": {"id": "2", "displayName": "Buffalo Bills", "abbreviation": "BUF", "color": "00338D"}},
            {"homeAway": "away", "score": "24", "team": {"id": "12", "displayName": "Kansas City Chiefs", "abbreviation": "KC", "color": "e31837"}},
        ],
    }],
}]}

SUMMARY = {
    "header": {"competitions": [{
        "competitors": [{"homeAway": "home", "team": {"id": "2"}}, {"homeAway": "away", "team": {"id": "12"}}],
        "broadcasts": [{"media": {"shortName": "CBS"}}],
    }]},
    "gameInfo": {"venue": {"fullName": "Highmark Stadium", "address": {"city": "Orchard Park", "state": "NY"}},
                 "weather": {"temperature": 41, "displayValue": "Cloudy"}, "attendance": 70212},
    "drives": {"previous": [{"team": {"id": "12"}, "plays": [
        {"text": "P.Mahomes pass deep left to X.Worthy for 34 yards", "statYardage": 34,
         "period": {"number": 2}, "clock": {"displayValue": "1:10"}},
        {"text": "I.Pacheco 2 yard rush (H.Butker kick)", "scoringPlay": True, "scoringType": {"abbreviation": "TD"},
         "awayScore": 24, "homeScore": 17, "period": {"number": 3}, "clock": {"displayValue": "4:12"}},
    ]}]},
    "boxscore": {"teams": [
        {"team": {"id": "12"}, "statistics": [{"name": "totalYards", "displayValue": "312"}, {"name": "turnovers", "displayValue": "0"}]},
        {"team": {"id": "2"}, "statistics": [{"name": "totalYards", "displayValue": "268"}, {"name": "turnovers", "displayValue": "1"}]},
    ]},
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def test_scoreboard_carries_scores_colors_and_situation(monkeypatch):
    monkeypatch.setattr(espn_schedule.requests, "get", lambda *a, **k: FakeResponse(SCOREBOARD))
    event = espn_schedule.fetch_scoreboard()[0]
    assert (event["away_score"], event["home_score"]) == (24, 17)
    assert (event["away_color"], event["home_color"]) == ("#e31837", "#00338d")
    assert (event["period"], event["display_clock"], event["broadcast"]) == (3, "4:12", "CBS")
    assert event["situation"]["possession"] == "away"
    assert event["situation"]["spot"] == "KC 35"
    assert event["situation"]["down_distance"] == "3rd & 7"
    game = espn_schedule.enrich_game({"title": "Kansas City Chiefs vs Buffalo Bills"}, [event])
    assert game["away_score"] == 24 and game["situation"]["last_play"].startswith("P.Mahomes")


def test_summary_normalizes_plays_stats_and_info():
    summary = espn_schedule.normalize_summary(SUMMARY)
    assert summary["venue"] == "Highmark Stadium"
    assert summary["city"] == "Orchard Park, NY"
    assert summary["weather"] == "41°, cloudy"
    assert summary["broadcast"] == "CBS"
    newest, big = summary["plays"]
    assert newest["tag"] == "TD" and newest["side"] == "away"
    assert (newest["away_score"], newest["home_score"]) == (24, 17)
    assert big["tag"] == "BIG PLAY" and big["away_score"] is None
    assert [s["key"] for s in summary["stats"]] == ["totalYards", "turnovers"]
    assert summary["stats"][0] == {"key": "totalYards", "label": "Total yards", "away": "312", "home": "268"}


def test_summary_tolerates_a_game_that_has_not_started():
    summary = espn_schedule.normalize_summary({"gameInfo": {"venue": {"fullName": "Dome", "indoor": True}}})
    assert summary == {"venue": "Dome", "city": None, "weather": "Indoors", "broadcast": None,
                       "attendance": None, "plays": [], "stats": []}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "STATE_DIR", tmp_path)
    monkeypatch.setenv("SUNDAYSIGNAL_SETUP_TOKEN", "bootstrap-secret")
    monkeypatch.setattr(webapp, "ADMIN_TOKEN", "")
    monkeypatch.setattr(webapp, "JSON_PATH", tmp_path / "catalog.json")
    webapp._game_detail_cache.clear()
    webapp.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    return webapp.app.test_client()


def test_game_detail_endpoint_requires_login_and_caches(client, monkeypatch):
    calls = []
    monkeypatch.setattr(espn_schedule, "fetch_summary", lambda event_id: calls.append(event_id) or {"venue": "Dome", "plays": []})
    assert client.get("/api/game/401671234").status_code == 401
    bootstrap(client)
    assert client.get("/api/game/not-a-number").status_code == 404
    first = client.get("/api/game/401671234").get_json()
    assert first == {"ok": True, "game": {"venue": "Dome", "plays": []}}
    client.get("/api/game/401671234")
    assert calls == ["401671234"]


def test_game_detail_failure_is_a_502(client, monkeypatch):
    monkeypatch.setattr(espn_schedule, "fetch_summary", lambda event_id: None)
    bootstrap(client)
    assert client.get("/api/game/1").status_code == 502


def test_main_page_shell_for_admin_and_member(client, monkeypatch):
    monkeypatch.setattr(webapp, "ADMIN_TOKEN", "iptv-secret")
    bootstrap(client)
    post(client, "/admin/users", email="pending@example.com")
    page = client.get("/")
    assert page.headers["Cache-Control"] == "no-store"
    html = page.text
    assert "/static/app.js" in html and "Users &amp; invites" in html
    assert "2 people, 1 pending" in html
    assert "/playlist.m3u?token=iptv-secret" in html
    assert 'data-theme-key="violet"' in html
    post(client, "/admin/users", email="member@example.com", password="member-password-1")
    member = webapp.app.test_client()
    post(member, "/login", email="member@example.com", password="member-password-1")
    html = member.get("/").text
    assert "Users &amp; invites" not in html and "btnRescrape" not in html
    assert "iptv-secret" not in html


def test_users_page_shows_pending_disabled_and_undo(client):
    bootstrap(client)
    response = post(client, "/admin/users", email="friend@example.com")
    assert "Invite link ready" in response.text and "friend@example.com" in response.text
    assert "PENDING" in response.text and "Invite expires" in response.text
    user_id = auth.create_user("member@example.com", "member-password-1")
    response = post(client, "/admin/users", action="invite", user_id=user_id)
    assert "Password reset link" in response.text
    response = post(client, "/admin/users", action="toggle", user_id=user_id)
    assert "Disabled member@example.com" in response.text and "Undo" in response.text
    assert "DISABLED" in response.text
    response = post(client, "/admin/users", action="toggle", user_id=user_id)
    assert "Enabled member@example.com" in response.text


def test_invite_page_names_the_account(client):
    bootstrap(client)
    response = post(client, "/admin/users", email="friend@example.com")
    token = re.search(r"/invite\?token=([A-Za-z0-9_-]+)", response.text).group(1)
    page = webapp.app.test_client().get("/invite?token=" + token).text
    assert "You&#39;re invited" in page and "friend@example.com" in page
    bad = webapp.app.test_client().get("/invite?token=nope").text
    assert "invalid or expired" in bad and 'name="password"' not in bad


def test_change_password_keeps_this_session_and_signs_out_others(client):
    bootstrap(client)
    other = webapp.app.test_client()
    post(other, "/login", email="admin@example.com", password="long-admin-password")
    assert other.get("/api/streams").status_code == 200
    wrong = post(client, "/account/password", current="nope", password="brand-new-password")
    assert "current password is incorrect" in wrong.text
    short = post(client, "/account/password", current="long-admin-password", password="short")
    assert "between 12 and 256" in short.text
    done = post(client, "/account/password", current="long-admin-password", password="brand-new-password")
    assert "Password changed" in done.text
    assert client.get("/api/streams").status_code == 200
    assert other.get("/api/streams").status_code == 401
    assert auth.authenticate("admin@example.com", "brand-new-password")
    assert client.post("/account/password", data={"current": "x", "password": "y" * 12}).status_code == 400


def test_scoreboard_is_shared_between_requests(monkeypatch):
    calls = []

    def fetch():
        calls.append(time.monotonic())
        return []

    monkeypatch.setattr(espn_schedule, "fetch_scoreboard", fetch)
    webapp.enrich_games({"games": []})
    webapp.enrich_games({"games": []})
    assert len(calls) == 1
