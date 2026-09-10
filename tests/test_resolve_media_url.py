import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import sundaysignal_scraper as scraper

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


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


def test_dead_host_save_and_load_round_trip(tmp_path):
    scraper._DEAD_HOSTS.clear()
    scraper._mark_dead("example.invalid")
    path = tmp_path / "dead_hosts.json"
    scraper.save_dead_hosts(str(path))

    scraper._DEAD_HOSTS.clear()
    scraper.load_dead_hosts(str(path))
    assert scraper._is_dead("example.invalid")


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
    fake_game = {"id": "1", "slug": "a-vs-b", "title": "A vs B", "url": "https://example.test/game/1"}

    monkeypatch.setattr(scraper, "extract_game_links", lambda html: [dict(fake_game)])
    monkeypatch.setattr(scraper, "extract_streams", lambda html, url: [dict(s) for s in fake_streams])
    monkeypatch.setattr(scraper, "fetch", lambda url, referer=None, timeout=12: "<html></html>")

    def fake_resolve(url):
        return {
            "media_url": f"https://cdn.example/{url.split('//', 1)[1]}.m3u8",
            "embed_url": url,
            "source_type": "hls_playlist",
            "chain": "test",
        }

    monkeypatch.setattr(scraper, "resolve_media_url", fake_resolve)

    data = scraper.crawl(resolve=True, max_resolve_per_game=6)
    game = data["games"][0]
    assert game["resolved_count"] == 3
    assert len({s["media_url"] for s in game["streams"]}) == 3
