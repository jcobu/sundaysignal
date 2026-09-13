import pathlib
import sys

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


def test_merge_keep_previous_matches_on_uid(monkeypatch):
    old = {
        "scraped_at": "2026-09-13T00:00:00Z",
        "games": [
            {
                "uid": "fake:1",
                "id": "1",
                "title": "A vs B",
                "streams": [{"name": "s", "media_url": "https://cdn.example/old.m3u8"}],
            }
        ],
    }
    new = {"games": [{"uid": "fake:1", "id": "1", "title": "A vs B", "streams": []}]}

    merged = scraper._merge_keep_previous(new, old)
    game = merged["games"][0]
    assert game["stale"] is True
    assert game["streams"][0]["media_url"] == "https://cdn.example/old.m3u8"
