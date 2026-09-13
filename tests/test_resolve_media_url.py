import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import netfetch
import sources as source_registry
import sundaysignal_scraper as scraper
from sources.base import Source

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeSource(Source):
    """Stand-in adapter so crawl() can be exercised without any network."""

    name = "fake"
    base_url = "https://example.test"

    def __init__(self, games, streams_by_game_url):
        self._games = games
        self._streams = streams_by_game_url

    def discover_games(self):
        return [dict(g) for g in self._games]

    def extract_streams(self, html, game_url):
        return [dict(s) for s in self._streams[game_url]]

    def rank_stream(self, stream):
        return 0


def _use_fake_source(monkeypatch, games, streams_by_game_url):
    source = FakeSource(games, streams_by_game_url)
    monkeypatch.setattr(source_registry, "get_sources", lambda: [source])
    monkeypatch.setattr(scraper, "fetch", lambda url, referer=None, timeout=12: "<html></html>")

    def fake_resolve(url, referer=None):
        return {
            "media_url": f"https://cdn.example/{url.split('//', 1)[1]}.m3u8",
            "embed_url": url,
            "source_type": "hls_playlist",
            "chain": "test",
        }

    monkeypatch.setattr(scraper, "resolve_media_url", fake_resolve)
    return source


def test_first_player_iframe_handles_protocol_relative_nested_src():
    html = _read("streame_center_nested_iframe.html")
    result = scraper._first_player_iframe(html, "https://streame.center/embed/ch30.php")
    assert result == "https://streame.center/embed/hls.php?stream=JHDAJBDnjdna30"


def test_first_player_iframe_finds_absolute_https_src():
    html = _read("everneverbee_player3.html")
    result = scraper._first_player_iframe(html, "https://everneverbee.info.pl/best/player3.php?ch=30")
    assert result == "https://universaltokenforall.st/player3/channel30"


def test_first_player_iframe_skips_ad_noise():
    html = _read("ninesoccer_iframe_amid_ads.html")
    result = scraper._first_player_iframe(html, "https://9soccer.biz/9-T47/Ch1/1.html")
    assert result == "https://ch.nexa.st/ch.php?id=1"


def test_first_player_iframe_finds_main_stream_among_ad_scripts():
    html = _read("ripplestream_main_stream.html")
    result = scraper._first_player_iframe(
        html, "https://ripplestream.cyou/san-francisco-firebells-vs-boston-hunters/"
    )
    assert result == "https://rippleplays.cfd/newhub/stream-55.php"


def test_first_player_iframe_returns_none_for_js_rendered_spa():
    html = _read("opturspapa_js_spa.html")
    result = scraper._first_player_iframe(
        html, "https://opturspapa.xyz/los-angeles-rams-vs-san-francisco-49ers"
    )
    assert result is None


def test_nflbite_source_extracts_wrapper_streams_and_ranks_mirrors():
    from sources.nflbite import NflbiteSource

    html = """
    <table>
      <tr><td>Mirror A</td><input type="hidden" id="linkk1" value="https://ovostream.example/x"></tr>
      <tr><td>Mirror B</td><input type="hidden" id="linkk2" value="https://live2.totalsporteks.example/y"></tr>
    </table>
    """
    src = NflbiteSource()
    streams = src.extract_streams(html, "https://www.nflbite.is/A-vs-B/1")
    urls = {s["url"] for s in streams}
    assert urls == {"https://ovostream.example/x", "https://live2.totalsporteks.example/y"}
    # live2 mirrors are tried first, but both remain candidates.
    assert sorted(streams, key=src.rank_stream)[0]["url"] == "https://live2.totalsporteks.example/y"


def test_get_sources_skips_unknown_names_without_killing_the_crawl(monkeypatch):
    monkeypatch.setenv("SUNDAYSIGNAL_SOURCES", "nosuchsource,nflbite")
    srcs = source_registry.get_sources()
    assert [s.name for s in srcs] == ["nflbite"]


def test_get_sources_falls_back_when_nothing_valid_configured(monkeypatch):
    monkeypatch.setenv("SUNDAYSIGNAL_SOURCES", "nosuchsource")
    srcs = source_registry.get_sources()
    assert [s.name for s in srcs] == [source_registry.DEFAULT_SOURCES[0]]


def test_protected_source_host_is_never_marked_dead():
    netfetch.dead_hosts().clear()
    netfetch._PROTECTED_HOSTS.clear()
    netfetch.protect_host("www.nflbite.is")
    netfetch.mark_dead("www.nflbite.is")
    assert not netfetch.is_dead("www.nflbite.is")
    netfetch._PROTECTED_HOSTS.clear()


def test_protecting_a_host_clears_a_stale_dead_entry():
    """A source site marked dead by an earlier run would otherwise keep the
    crawler blind for the whole TTL — every later crawl finding zero games
    without ever attempting a request."""
    netfetch.dead_hosts().clear()
    netfetch._PROTECTED_HOSTS.clear()
    netfetch.mark_dead("www.nflbite.is")
    assert netfetch.is_dead("www.nflbite.is")

    netfetch.protect_host("www.nflbite.is")
    assert not netfetch.is_dead("www.nflbite.is")
    assert "www.nflbite.is" not in netfetch.dead_hosts()
    netfetch._PROTECTED_HOSTS.clear()


def test_get_sources_protects_each_source_host(monkeypatch):
    monkeypatch.setenv("SUNDAYSIGNAL_SOURCES", "nflbite")
    netfetch._PROTECTED_HOSTS.clear()
    srcs = source_registry.get_sources()
    host = netfetch.host_of(srcs[0].base_url)
    assert host in netfetch._PROTECTED_HOSTS
    netfetch._PROTECTED_HOSTS.clear()


def test_run_cycle_keeps_previous_catalog_when_scrape_resolves_nothing(monkeypatch, tmp_path):
    """Regression test for the web UI's Rescrape button wiping the catalog.

    It used to write crawl() output straight to disk, so a scrape that
    resolved nothing replaced a perfectly good list of games with an empty
    one. Every write path must go through run_cycle's guard.
    """
    catalog = tmp_path / "sundaysignal_streams.json"
    catalog.write_text(
        json.dumps(
            {
                "scraped_at": "2026-09-12T00:00:00Z",
                "game_count": 1,
                "games": [
                    {
                        "uid": "fake:1",
                        "id": "1",
                        "title": "A vs B",
                        "streams": [{"name": "s", "media_url": "https://cdn.example/good.m3u8"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # A crawl that still sees the game but resolves none of its streams.
    monkeypatch.setattr(
        scraper,
        "crawl",
        lambda resolve=True: {
            "scraped_at": "2026-09-13T00:00:00Z",
            "game_count": 1,
            "games": [
                {
                    "uid": "fake:1",
                    "id": "1",
                    "title": "A vs B",
                    "streams": [],
                    "stream_count": 0,
                    "resolved_count": 0,
                    "all_wrapper_count": 5,
                }
            ],
        },
    )
    monkeypatch.setattr(scraper, "espn_schedule", None)

    result = scraper.run_cycle(str(tmp_path))

    assert result["kept_previous"] is True
    assert result["wrote"] is False
    after = json.loads(catalog.read_text(encoding="utf-8"))
    assert after["games"][0]["streams"][0]["media_url"] == "https://cdn.example/good.m3u8"


def test_run_cycle_writes_when_streams_resolve(monkeypatch, tmp_path):
    monkeypatch.setattr(
        scraper,
        "crawl",
        lambda resolve=True: {
            "scraped_at": "2026-09-13T00:00:00Z",
            "game_count": 1,
            "games": [
                {
                    "uid": "fake:1",
                    "id": "1",
                    "title": "A vs B",
                    "streams": [{"name": "s", "media_url": "https://cdn.example/new.m3u8"}],
                    "stream_count": 1,
                    "resolved_count": 1,
                    "all_wrapper_count": 3,
                }
            ],
        },
    )
    monkeypatch.setattr(scraper, "espn_schedule", None)

    result = scraper.run_cycle(str(tmp_path))

    assert result["wrote"] is True
    assert result["kept_previous"] is False
    written = json.loads((tmp_path / "sundaysignal_streams.json").read_text(encoding="utf-8"))
    assert written["games"][0]["streams"][0]["media_url"] == "https://cdn.example/new.m3u8"
    # The freshness marker the crawler healthcheck watches must be updated.
    assert (tmp_path / "crawl_state.json").exists()


def test_dead_host_save_and_load_round_trip(tmp_path):
    netfetch.dead_hosts().clear()
    netfetch.mark_dead("example.invalid")
    path = tmp_path / "dead_hosts.json"
    netfetch.save_dead_hosts(str(path))

    netfetch.dead_hosts().clear()
    netfetch.load_dead_hosts(str(path))
    assert netfetch.is_dead("example.invalid")


def test_round_robin_merge_interleaves_lists_of_unequal_length():
    result = scraper._round_robin_merge([[1, 2, 3], ["a", "b"], []])
    assert result == [1, "a", 2, "b", 3]


def test_crawl_caps_resolved_streams_independently_per_game(monkeypatch):
    """Regression test for the shared cross-game thread pool introduced to
    avoid one game's resolution fully draining before the next game starts:
    each game's max_resolve_per_game cap must stay independent, not a global
    cap shared across every game's candidates in the merged queue.
    """
    def make_streams(prefix: str, count: int):
        return [
            {"name": f"{prefix}{i}", "url": f"https://provider{i}.example/{prefix}", "badges": [], "media_url": None}
            for i in range(count)
        ]

    games = [
        {"id": "1", "slug": "a-vs-b", "title": "Game A", "url": "https://example.test/game/1"},
        {"id": "2", "slug": "c-vs-d", "title": "Game B", "url": "https://example.test/game/2"},
    ]
    _use_fake_source(
        monkeypatch,
        games,
        {
            "https://example.test/game/1": make_streams("a", 5),
            "https://example.test/game/2": make_streams("b", 5),
        },
    )

    data = scraper.crawl(resolve=True, max_resolve_per_game=2)
    assert len(data["games"]) == 2
    for game in data["games"]:
        assert game["resolved_count"] == 2


def test_crawl_resolves_multiple_distinct_providers_per_game(monkeypatch):
    """Regression test: the resolve loop used to stop trying other providers
    once any single stream resolved, unless the URL literally contained
    "live2.totalsporteks" — silently discarding working streams like
    ovostream.net. It should now gather up to max_resolve_per_game from
    whichever providers actually resolve.
    """
    fake_streams = [
        {"name": "A", "url": "https://live2.totalsporteks.example/x", "badges": [], "media_url": None},
        {"name": "B", "url": "https://ovostream.example/y", "badges": [], "media_url": None},
        {"name": "C", "url": "https://another-provider.example/z", "badges": [], "media_url": None},
    ]
    games = [{"id": "1", "slug": "a-vs-b", "title": "A vs B", "url": "https://example.test/game/1"}]
    _use_fake_source(monkeypatch, games, {"https://example.test/game/1": fake_streams})

    data = scraper.crawl(resolve=True, max_resolve_per_game=6)
    game = data["games"][0]
    assert game["resolved_count"] == 3
    assert len({s["media_url"] for s in game["streams"]}) == 3


def test_crawl_tags_games_with_source_and_namespaced_uid(monkeypatch):
    games = [{"id": "42", "slug": "a-vs-b", "title": "A vs B", "url": "https://example.test/game/42"}]
    _use_fake_source(monkeypatch, games, {"https://example.test/game/42": []})

    data = scraper.crawl(resolve=False)
    game = data["games"][0]
    assert game["source"] == "fake"
    assert game["uid"] == "fake:42"
    assert data["sources"] == [{"name": "fake", "base_url": "https://example.test"}]


def _hours_ago(n: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=n)).isoformat()


def _old_catalog(streams, scraped_at=None, **game_fields):
    game = {"uid": "fake:1", "id": "1", "title": "A vs B", "streams": streams}
    game.update(game_fields)
    return {"scraped_at": scraped_at or _hours_ago(1), "games": [game]}


def test_merge_keep_previous_matches_on_uid():
    old = _old_catalog([{"name": "s", "media_url": "https://cdn.example/old.m3u8"}])
    new = {"games": [{"uid": "fake:1", "id": "1", "title": "A vs B", "streams": []}]}

    merged = scraper._merge_keep_previous(new, old)
    game = merged["games"][0]
    assert game["stale"] is True
    assert game["streams"][0]["media_url"] == "https://cdn.example/old.m3u8"


def test_merge_never_shrinks_a_game_that_was_working():
    """Regression test: a scrape that resolves 1 of a game's 3 streams used
    to replace the list outright, throwing away 2 working links mid-game.
    Fresh streams lead; previously-working ones are carried behind them.
    """
    old = _old_catalog(
        [
            {"name": "a", "media_url": "https://cdn.example/1.m3u8"},
            {"name": "b", "media_url": "https://cdn.example/2.m3u8"},
            {"name": "c", "media_url": "https://cdn.example/3.m3u8"},
        ]
    )
    new = {
        "games": [
            {
                "uid": "fake:1",
                "id": "1",
                "title": "A vs B",
                "streams": [{"name": "fresh", "media_url": "https://cdn.example/fresh.m3u8"}],
            }
        ]
    }

    game = scraper._merge_keep_previous(new, old)["games"][0]
    urls = [s["media_url"] for s in game["streams"]]
    assert urls[0] == "https://cdn.example/fresh.m3u8", "fresh stream should lead"
    assert len(urls) == 4, "the three previous streams must survive"
    assert game["resolved_count"] == 1
    assert game["stream_count"] == 4
    # The game itself isn't stale — it has a fresh stream.
    assert game.get("stale") is False


def test_merge_deduplicates_streams_still_present_in_the_new_scrape():
    old = _old_catalog([{"name": "a", "media_url": "https://cdn.example/same.m3u8"}])
    new = {
        "games": [
            {
                "uid": "fake:1",
                "id": "1",
                "title": "A vs B",
                "streams": [{"name": "a", "media_url": "https://cdn.example/same.m3u8"}],
            }
        ]
    }

    game = scraper._merge_keep_previous(new, old)["games"][0]
    assert len(game["streams"]) == 1
    assert game["resolved_count"] == 1


def test_merge_drops_carried_streams_once_they_age_out():
    old = _old_catalog(
        [{"name": "a", "media_url": "https://cdn.example/ancient.m3u8"}],
        scraped_at=_hours_ago(scraper.KEEP_STALE_HOURS + 2),
    )
    new = {"games": [{"uid": "fake:1", "id": "1", "title": "A vs B", "streams": []}]}

    game = scraper._merge_keep_previous(new, old)["games"][0]
    assert game["streams"] == [], "expired links shouldn't linger forever"


def test_merge_keeps_expired_streams_while_a_game_is_live():
    """While a game is on, a link that might still work beats an empty list,
    so the age cap is waived when there's nothing fresh to show."""
    old = _old_catalog(
        [{"name": "a", "media_url": "https://cdn.example/ancient.m3u8"}],
        scraped_at=_hours_ago(scraper.KEEP_STALE_HOURS + 2),
    )
    new = {
        "games": [
            {
                "uid": "fake:1",
                "id": "1",
                "title": "A vs B",
                "streams": [],
                "status_state": "in",
            }
        ]
    }

    game = scraper._merge_keep_previous(new, old)["games"][0]
    assert [s["media_url"] for s in game["streams"]] == ["https://cdn.example/ancient.m3u8"]
    assert game["stale"] is True


def test_merge_caps_how_many_streams_accumulate(monkeypatch):
    monkeypatch.setattr(scraper, "MAX_STREAMS_PER_GAME", 3)
    old = _old_catalog(
        [{"name": f"old{i}", "media_url": f"https://cdn.example/old{i}.m3u8"} for i in range(10)]
    )
    new = {
        "games": [
            {
                "uid": "fake:1",
                "id": "1",
                "title": "A vs B",
                "streams": [{"name": "fresh", "media_url": "https://cdn.example/fresh.m3u8"}],
            }
        ]
    }

    game = scraper._merge_keep_previous(new, old)["games"][0]
    assert len(game["streams"]) == 3
    assert game["streams"][0]["media_url"] == "https://cdn.example/fresh.m3u8"
