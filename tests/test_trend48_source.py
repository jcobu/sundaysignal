import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import sources as source_registry
import sundaysignal_scraper as scraper
import webapp
from sources.base import Source
from sources.trend48 import Trend48Source

SAMPLE_MATCHES = [
    {
        "id": "soccer-1",
        "title": "Arsenal vs Chelsea",
        "category": "football",
        "date": 1789826400000,
        "popular": True,
        "manual": False,
        "sources": [
            {"source": "alpha", "id": "a", "streamNo": 1, "hd": True},
            {"source": "beta", "id": "b"},
        ],
        "watch_url": "https://trend48.st/event/soccer-1",
    },
    {
        "id": "hockey-1",
        "title": "Kings vs Canucks",
        "category": "hockey",
        "date": 1789827300000,
        "popular": False,
        "manual": True,
        "sources": [{"source": "manual", "id": "hockey-1"}],
        "watch_url": "https://trend48.st/event/hockey-1",
    },
    {
        "id": "basketball-1",
        "title": "Lakers vs Warriors",
        "category": "basketball",
        "date": 1789828200000,
        "sources": [{"source": "gamma", "id": "c"}],
        "watch_url": "https://trend48.st/event/basketball-1",
    },
]


def test_trend48_maps_categories_and_keeps_provider_branding_private():
    source = Trend48Source(categories="football,hockey")
    games = source.parse_matches(SAMPLE_MATCHES, live_ids={"hockey-1"})

    assert [game["sport"] for game in games] == ["soccer", "hockey"]
    assert games[0]["league"] == "Soccer"
    assert games[1]["live"] is True
    assert [stream["name"] for stream in games[0]["streams"]] == ["Source 1", "Source 2"]
    assert "source=alpha" in games[0]["streams"][0]["embed_url"]
    assert "stream=1" in games[0]["streams"][0]["embed_url"]
    assert all(stream["source_type"] == "embed" for stream in games[0]["streams"])
    assert all("provider" not in stream for stream in games[0]["streams"])


class InlineEmbedSource(Source):
    name = "inline"
    base_url = "https://example.test"

    def discover_games(self):
        return [
            {
                "id": "one",
                "slug": "alpha-vs-beta",
                "title": "Alpha vs Beta",
                "url": "https://example.test/event/one",
                "sport": "hockey",
                "league": "Hockey",
                "streams": [
                    {
                        "name": "Source 1",
                        "url": "https://example.test/event/one?source=a",
                        "embed_url": "https://example.test/event/one?source=a",
                        "media_url": None,
                        "source_type": "embed",
                    }
                ],
            }
        ]

    def extract_streams(self, html, game_url):
        raise AssertionError("inline API streams must not fetch a game page")


def test_crawl_accepts_inline_embed_streams_without_hls_resolution(monkeypatch):
    monkeypatch.setattr(source_registry, "get_sources", lambda: [InlineEmbedSource()])
    monkeypatch.setattr(scraper, "SCHEDULE_SOURCE", "none")
    monkeypatch.setattr(
        scraper,
        "fetch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected fetch")),
    )
    result = scraper.crawl(resolve=True)

    assert result["game_count"] == 1
    game = result["games"][0]
    assert game["sport"] == "hockey"
    assert game["stream_count"] == 1
    assert game["streams"][0]["source_type"] == "embed"


def test_web_catalog_marks_embed_players_and_builds_sport_categories(monkeypatch):
    monkeypatch.setattr(webapp, "espn_schedule", None)
    payload = {
        "games": [
            {
                "id": "one",
                "title": "Alpha vs Beta",
                "slug": "alpha-vs-beta",
                "sport": "hockey",
                "streams": [
                    {
                        "name": "Source 1",
                        "embed_url": "https://example.test/event/one",
                        "source_type": "embed",
                    }
                ],
            }
        ]
    }

    enriched = webapp.enrich_games(payload)

    stream = enriched["games"][0]["streams"][0]
    assert stream["play_url"] == "https://example.test/event/one"
    assert stream["player_type"] == "embed"
    assert enriched["sports"] == [
        {"id": "football", "label": "NFL", "count": 0},
        {"id": "hockey", "label": "Hockey", "count": 1},
        {"id": "soccer", "label": "Soccer", "count": 0},
        {"id": "basketball", "label": "Basketball", "count": 0},
    ]


def test_stream_api_can_filter_one_sport_without_losing_category_metadata(monkeypatch):
    monkeypatch.setattr(webapp, "espn_schedule", None)
    monkeypatch.setattr(
        webapp,
        "load_data",
        lambda: {
            "games": [
                {"id": "h", "title": "A vs B", "sport": "hockey", "streams": []},
                {"id": "s", "title": "C vs D", "sport": "soccer", "streams": []},
            ]
        },
    )
    monkeypatch.setattr(webapp, "load_scrape_status", lambda: {})

    response = webapp.app.test_client().get("/api/streams?sport=hockey")
    payload = response.get_json()

    assert response.status_code == 200
    assert [game["id"] for game in payload["games"]] == ["h"]
    assert payload["game_count"] == 1
    assert {entry["id"] for entry in payload["sports"]} == {
        "football",
        "hockey",
        "soccer",
        "basketball",
    }


def test_web_ui_has_permanent_icon_tabs_and_defaults_to_nfl():
    response = webapp.app.test_client().get("/")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "const CORE_SPORTS" in html
    assert "{id: 'football', label: 'NFL'}" in html
    assert "{id: 'hockey', label: 'Hockey'}" in html
    assert "{id: 'soccer', label: 'Soccer'}" in html
    assert "const SPORT_ICONS" in html
    assert "@fortawesome/fontawesome-free@6.7.2/css/all.min.css" in html
    assert "football: 'fa-solid fa-football'" in html
    assert "hockey: 'fa-solid fa-hockey-puck'" in html
    assert "soccer: 'fa-solid fa-futbol'" in html
    assert "basketball: 'fa-solid fa-basketball'" in html
    assert "let activeSport = 'football'" in html
    assert 'id="btnRescrapeTop"' in html
    assert '<div class="topbar">' in html
    assert '<nav class="sport-tabs" id="sportTabs"' in html
    assert '<section class="events-section"' in html
    assert '<div class="event-grid" id="sidebar">' in html
