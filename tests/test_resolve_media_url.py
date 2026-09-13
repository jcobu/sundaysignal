import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import espn_schedule
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


def test_telegram_source_parses_real_channel_markup():
    from sources.telegram import TelegramSource

    games = TelegramSource().parse_channel(_read("telegram_channel.html"))
    by_id = {g["id"]: g for g in games}
    assert set(by_id) == {"68553", "68554", "68547"}
    assert by_id["68547"]["title"] == "detroit lions vs new orleans saints"
    assert by_id["68547"]["url"] == "http://sportslinks.is/detroit-lions-vs-new-orleans-saints/68547"
    # The linked host isn't t.me, so its own origin is the sane Referer.
    assert by_id["68547"]["referer"] == "http://sportslinks.is/"


def test_telegram_source_dedupes_a_game_announced_more_than_once():
    from sources.telegram import TelegramSource

    html = """
    <div class="tgme_widget_message_text">
      <a href="http://sportslinks.is/houston-texans-vs-buffalo-bills/68552">Live Stream</a>
    </div>
    <div class="tgme_widget_message_text">
      <a href="http://sportslinks.is/houston-texans-vs-buffalo-bills/68552">Live Stream</a>
    </div>
    """
    assert len(TelegramSource().parse_channel(html)) == 1


def test_telegram_source_ignores_links_that_are_not_games():
    from sources.telegram import TelegramSource

    html = """
    <div class="tgme_widget_message_text">
      <a href="https://t.me/nflbite_official">Join our channel</a>
      <a href="http://sportslinks.is/about">About</a>
      <a href="http://sportslinks.is/some-page/notanumber">Nope</a>
      <a href="http://sportslinks.is/houston-texans-vs-buffalo-bills/68552">Live Stream</a>
    </div>
    """
    games = TelegramSource().parse_channel(html)
    assert [g["id"] for g in games] == ["68552"]


def test_telegram_source_follows_whatever_host_the_channel_posts():
    """The point of reading the channel: when the site rotates domains, the
    channel starts posting the new one and we follow it, instead of staying
    pinned to a host baked into config."""
    from sources.telegram import TelegramSource

    html = """
    <div class="tgme_widget_message_text">
      <a href="https://brand-new-domain.example/houston-texans-vs-buffalo-bills/68552">Live</a>
    </div>
    """
    game = TelegramSource().parse_channel(html)[0]
    assert game["url"].startswith("https://brand-new-domain.example/")
    assert game["referer"] == "https://brand-new-domain.example/"


def test_telegram_and_nflbite_extract_streams_the_same_way():
    """Both sites run the same software, so the table parser is shared —
    a markup change there should only need fixing once."""
    from sources.nflbite import NflbiteSource
    from sources.telegram import TelegramSource

    html = """
    <table><tr><td>Mirror A</td>
      <input type="hidden" id="linkk1" value="https://ovostream.example/x"></tr></table>
    """
    tg = TelegramSource().extract_streams(html, "http://sportslinks.is/a-vs-b/1")
    nb = NflbiteSource().extract_streams(html, "https://www.nflbite.is/a-vs-b/1")
    assert tg == nb
    assert tg[0]["url"] == "https://ovostream.example/x"


def test_dedupe_scraped_pools_streams_for_the_same_fixture():
    """When two sources do both produce records for one fixture, their
    streams are pooled onto a single entry rather than listed twice."""
    scraped = [
        {"id": "68552", "uid": "alpha:68552", "source": "alpha",
         "title": "Houston Texans vs Buffalo Bills", "stream_sources": ["alpha"],
         "all_wrapper_count": 3,
         "streams": [{"name": "a", "media_url": "https://cdn.example/a.m3u8"}]},
        {"id": "68552", "uid": "beta:68552", "source": "beta",
         "title": "Houston Texans vs Buffalo Bills", "stream_sources": ["beta"],
         "all_wrapper_count": 2,
         "streams": [{"name": "b", "media_url": "https://cdn.example/b.m3u8"}]},
    ]

    out = scraper._dedupe_scraped(scraped)

    assert len(out) == 1, "the same fixture must not be listed twice"
    assert sorted(s["media_url"] for s in out[0]["streams"]) == [
        "https://cdn.example/a.m3u8", "https://cdn.example/b.m3u8"
    ]
    assert out[0]["stream_sources"] == ["alpha", "beta"]
    assert out[0]["all_wrapper_count"] == 5


def test_two_sources_covering_one_game_list_it_once(monkeypatch):
    """Both adapters read the same backend and list the same game ids, so
    without a schedule folding them together the sidebar would show the
    fixture twice."""
    monkeypatch.setattr(scraper, "SCHEDULE_SOURCE", "none")

    def make_source(name):
        class S(Source):
            pass
        s = S()
        s.name = name
        s.base_url = f"https://{name}.example"
        s.discover_games = lambda: [
            {"id": "68552", "slug": "hou-vs-buf", "title": "Houston Texans vs Buffalo Bills",
             "url": f"https://{name}.example/g/68552"}
        ]
        s.extract_streams = lambda html, url: [
            {"name": name, "url": f"https://{name}.example/w", "badges": [], "media_url": None}
        ]
        s.rank_stream = lambda stream: 0
        return s

    monkeypatch.setattr(source_registry, "get_sources", lambda: [make_source("alpha"), make_source("beta")])
    monkeypatch.setattr(scraper, "fetch", lambda url, referer=None, timeout=12: "<html></html>")
    monkeypatch.setattr(scraper, "REQUEST_DELAY", 0)
    monkeypatch.setattr(
        scraper, "resolve_media_url",
        lambda url, referer=None: {"media_url": "https://cdn.example/a.m3u8", "embed_url": url,
                                   "source_type": "hls_playlist", "chain": "test"},
    )

    data = scraper.crawl(resolve=True)

    assert len(data["games"]) == 1, "the same fixture must not be listed twice"
    assert data["games"][0]["streams"]


def test_collect_records_skips_a_game_another_source_already_covered(monkeypatch):
    """Sources pointing at the same backend shouldn't each fetch the same
    page — that's pure duplicated work on every cycle."""
    fetched = []

    def fake_fetch(url, referer=None, timeout=12):
        fetched.append(url)
        return """<table><tr><td>M</td>
                  <input type="hidden" id="linkk1" value="https://w.example/x"></tr></table>"""

    monkeypatch.setattr(scraper, "fetch", fake_fetch)
    monkeypatch.setattr(scraper, "REQUEST_DELAY", 0)

    def src(name, host):
        class S(Source):
            pass
        s = S()
        s.name = name
        s.base_url = f"https://{host}"
        s.discover_games = lambda: [
            {"id": "1", "slug": "a-vs-b", "title": "A vs B", "url": f"https://{host}/a-vs-b/1"}
        ]
        s.extract_streams = lambda html, url: scraper.source_registry.REGISTRY["nflbite"]().extract_streams(html, url)
        s.rank_stream = lambda stream: 0
        return s

    records = scraper._collect_records([src("first", "one.example"), src("second", "two.example")])

    assert fetched == ["https://one.example/a-vs-b/1"], "the duplicate page shouldn't be refetched"
    assert len(records) == 1


def test_collect_records_still_tries_a_second_source_when_the_first_finds_nothing(monkeypatch):
    """The redundancy has to survive the optimization: if one source's page
    yields no candidates, the other still gets its turn."""
    fetched = []

    def fake_fetch(url, referer=None, timeout=12):
        fetched.append(url)
        # Only the second host serves a usable page.
        if "two.example" in url:
            return """<table><tr><td>M</td>
                      <input type="hidden" id="linkk1" value="https://w.example/x"></tr></table>"""
        return "<html>nothing here</html>"

    monkeypatch.setattr(scraper, "fetch", fake_fetch)
    monkeypatch.setattr(scraper, "REQUEST_DELAY", 0)

    def src(name, host):
        class S(Source):
            pass
        s = S()
        s.name = name
        s.base_url = f"https://{host}"
        s.discover_games = lambda: [
            {"id": "1", "slug": "a-vs-b", "title": "A vs B", "url": f"https://{host}/a-vs-b/1"}
        ]
        s.extract_streams = lambda html, url: scraper.source_registry.REGISTRY["nflbite"]().extract_streams(html, url)
        s.rank_stream = lambda stream: 0
        return s

    records = scraper._collect_records([src("first", "one.example"), src("second", "two.example")])

    assert len(fetched) == 2, "second source must still be tried"
    assert any(r["candidates"] for r in records)


def test_telegram_is_registered_and_on_by_default():
    assert "telegram" in source_registry.REGISTRY
    assert "telegram" in source_registry.DEFAULT_SOURCES


def test_collect_records_uses_a_games_own_referer_when_it_has_one(monkeypatch):
    """A source that links out to another host must not send its own base
    as the Referer — these sites check it."""
    seen = {}

    def fake_fetch(url, referer=None, timeout=12):
        seen[url] = referer
        return "<html></html>"

    monkeypatch.setattr(scraper, "fetch", fake_fetch)

    class LinksOut(Source):
        name = "linksout"
        base_url = "https://channel.example"

        def discover_games(self):
            return [
                {"id": "1", "slug": "a-vs-b", "title": "A vs B",
                 "url": "http://elsewhere.example/a-vs-b/1",
                 "referer": "http://elsewhere.example/"},
                {"id": "2", "slug": "c-vs-d", "title": "C vs D",
                 "url": "https://channel.example/c-vs-d/2"},
            ]

        def extract_streams(self, html, game_url):
            return []

    monkeypatch.setattr(scraper, "REQUEST_DELAY", 0)
    scraper._collect_records([LinksOut()])

    assert seen["http://elsewhere.example/a-vs-b/1"] == "http://elsewhere.example/"
    # Falls back to the source's own base when a game doesn't specify one.
    assert seen["https://channel.example/c-vs-d/2"] == "https://channel.example/"


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
    one. What matters is the invariant — a bad scrape must never cost you
    streams that were working — not whether the write was skipped.
    """
    catalog = tmp_path / "sundaysignal_streams.json"
    catalog.write_text(
        json.dumps(
            {
                "scraped_at": _hours_ago(1),
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
            "scraped_at": datetime.now(timezone.utc).isoformat(),
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

    scraper.run_cycle(str(tmp_path))

    after = json.loads(catalog.read_text(encoding="utf-8"))
    assert after["games"][0]["streams"][0]["media_url"] == "https://cdn.example/good.m3u8"


def test_run_cycle_keeps_previous_file_when_a_crawl_yields_no_games_at_all(monkeypatch, tmp_path):
    """Total washout — source down and no schedule — must leave the existing
    catalog untouched rather than replacing it with nothing."""
    catalog = tmp_path / "sundaysignal_streams.json"
    good = {
        "scraped_at": _hours_ago(1),
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
    catalog.write_text(json.dumps(good), encoding="utf-8")

    monkeypatch.setattr(
        scraper,
        "crawl",
        lambda resolve=True: {"scraped_at": datetime.now(timezone.utc).isoformat(), "game_count": 0, "games": []},
    )
    monkeypatch.setattr(scraper, "espn_schedule", None)

    result = scraper.run_cycle(str(tmp_path))

    assert result["kept_previous"] is True
    assert result["wrote"] is False
    assert json.loads(catalog.read_text(encoding="utf-8")) == good


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


def _espn_event(espn_id, away, home, state="pre", date=None):
    return {
        "espn_id": espn_id,
        "name": f"{away} at {home}",
        "date": date or _hours_ago(-2),  # two hours from now
        "away_team": away,
        "home_team": home,
        "status_state": state,
        "status_name": "",
        "status_detail": "",
        "venue": "Test Stadium",
        "tokens": espn_schedule._team_tokens(away) | espn_schedule._team_tokens(home),
    }


def test_schedule_games_appear_even_with_no_streams(monkeypatch):
    """The whole point of a schedule-driven list: a game the scrape missed
    entirely still shows up, just without streams."""
    events = [
        _espn_event("401", "Seattle Seahawks", "New England Patriots"),
        _espn_event("402", "Dallas Cowboys", "New York Giants"),
    ]
    monkeypatch.setattr(scraper, "SCHEDULE_SOURCE", "espn")
    monkeypatch.setattr(espn_schedule, "fetch_scoreboard", lambda *a, **k: events)
    # Only one of the two games is scraped, and it resolves one stream.
    games = [{"id": "1", "slug": "s-vs-n", "title": "Seattle Seahawks vs New England Patriots",
              "url": "https://example.test/game/1"}]
    _use_fake_source(
        monkeypatch,
        games,
        {"https://example.test/game/1": [
            {"name": "m", "url": "https://mirror.example/1", "badges": [], "media_url": None}
        ]},
    )

    data = scraper.crawl(resolve=True)

    titles = {g["title"] for g in data["games"]}
    assert len(data["games"]) == 2, "both scheduled games should be listed"
    assert "Dallas Cowboys vs New York Giants" in titles

    seahawks = next(g for g in data["games"] if "Seahawks" in g["title"])
    giants = next(g for g in data["games"] if "Giants" in g["title"])
    assert seahawks["streams"], "scraped streams should attach to the scheduled game"
    assert seahawks["uid"] == "espn:401"
    assert seahawks["stream_sources"] == ["fake"]
    assert giants["streams"] == [], "unscraped game is listed with no streams"


def test_scraped_game_with_no_schedule_match_is_kept(monkeypatch):
    events = [_espn_event("401", "Seattle Seahawks", "New England Patriots")]
    monkeypatch.setattr(scraper, "SCHEDULE_SOURCE", "espn")
    monkeypatch.setattr(espn_schedule, "fetch_scoreboard", lambda *a, **k: events)
    games = [{"id": "9", "slug": "mystery", "title": "Some Unlisted Matchup",
              "url": "https://example.test/game/9"}]
    _use_fake_source(
        monkeypatch,
        games,
        {"https://example.test/game/9": [
            {"name": "m", "url": "https://mirror.example/9", "badges": [], "media_url": None}
        ]},
    )

    data = scraper.crawl(resolve=True)
    titles = {g["title"] for g in data["games"]}
    assert "Some Unlisted Matchup" in titles, "an unmatched scrape shouldn't vanish"


def test_crawl_falls_back_to_scraped_games_when_schedule_unavailable(monkeypatch):
    monkeypatch.setattr(scraper, "SCHEDULE_SOURCE", "espn")
    monkeypatch.setattr(espn_schedule, "fetch_scoreboard", lambda *a, **k: [])
    games = [{"id": "1", "slug": "a-vs-b", "title": "A vs B", "url": "https://example.test/game/1"}]
    _use_fake_source(monkeypatch, games, {"https://example.test/game/1": []})

    data = scraper.crawl(resolve=False)
    assert [g["title"] for g in data["games"]] == ["A vs B"]
    assert data["schedule_source"] is None


def test_prune_keeps_upcoming_and_live_but_drops_old_finals():
    games = [
        {"title": "upcoming", "status_state": "pre", "start_time": _hours_ago(-3)},
        {"title": "live", "status_state": "in", "start_time": _hours_ago(2)},
        {"title": "just finished", "status_state": "post", "start_time": _hours_ago(4)},
        {"title": "yesterday", "status_state": "post", "start_time": _hours_ago(30)},
    ]
    kept = {g["title"] for g in scraper._prune_finished_games(games)}
    assert kept == {"upcoming", "live", "just finished"}


def test_prune_never_drops_a_live_game_however_old_the_clock_says():
    """A scoreboard stuck on "in" shouldn't yank a game out from under
    someone who's watching it."""
    games = [{"title": "marathon", "status_state": "in", "start_time": _hours_ago(50)}]
    assert len(scraper._prune_finished_games(games)) == 1


def test_merge_bridges_a_game_whose_id_changed():
    """Streams must survive the same fixture being re-keyed — which happens
    when a game starts being identified by schedule id instead of a scraped
    site's id."""
    old = {
        "scraped_at": _hours_ago(1),
        "games": [
            {
                "uid": "nflbite:12345",
                "id": "12345",
                "title": "Seattle Seahawks vs New England Patriots",
                "streams": [{"name": "s", "media_url": "https://cdn.example/carried.m3u8"}],
            }
        ],
    }
    new = {
        "games": [
            {
                "uid": "espn:401",
                "id": "401",
                "espn_id": "401",
                "title": "Seattle Seahawks vs New England Patriots",
                "streams": [],
            }
        ]
    }

    merged = scraper._merge_keep_previous(new, old)
    assert len(merged["games"]) == 1, "the same fixture shouldn't appear twice"
    assert merged["games"][0]["streams"][0]["media_url"] == "https://cdn.example/carried.m3u8"


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
