"""StreamKing discovery, candidate selection, and resolver integration."""
from pathlib import Path

import netfetch
import sources
from sources.streamking import StreamKingSource
import sundaysignal_scraper as scraper

FIXTURES = Path(__file__).parent / 'fixtures'


def test_listing_includes_games_channels_and_rotating_player_hosts():
    games = StreamKingSource().parse_listing((FIXTURES / 'streamking_listing.html').read_text())
    assert len(games) == 3
    assert games[0]['title'] == 'NFL RedZone vs Live'
    assert games[1]['title'] == 'Carolina Panthers vs Green Bay Packers'
    assert games[1]['url'] == 'https://beststreameast.xyz/match/carolina-panthers-vs-green-bay-packers/22099/'
    assert games[1]['referer'] == 'https://streamking.cx/categories/nfl/'
    assert games[2]['url'] == 'https://streamking.cx/match/baltimore-ravens-vs-buffalo-bills/22395/'


def test_discovery_handles_fetch_failure_and_custom_base(monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return None
    monkeypatch.setattr(netfetch, 'fetch', fetch)
    assert StreamKingSource('https://new.example/').discover_games() == []
    assert calls == ['https://new.example/categories/nfl/']


def test_extracts_player_not_ads_with_game_referer():
    url = 'https://beststreameast.xyz/match/nfl-network/19531/'
    streams = StreamKingSource().extract_streams((FIXTURES / 'streamking_player.html').read_text(), url)
    assert len(streams) == 1
    assert streams[0]['url'] == 'https://embexex.pw/js/?2'
    assert streams[0]['referer'] == url
    assert streams[0]['media_url'] is None


def test_upcoming_and_forbidden_pages_have_no_playable_candidates():
    source = StreamKingSource()
    assert source.extract_streams('<div id="not-available-msg">Stream not available yet</div>', 'https://example.test/') == []
    assert source.extract_streams('<h1>403 Forbidden</h1>', 'https://example.test/') == []


def test_relative_duplicate_and_invalid_iframes():
    streams = StreamKingSource().extract_streams('''
        <iframe allowfullscreen src="//player.example/embed"></iframe>
        <iframe allowfullscreen src="//player.example/embed"></iframe>
        <iframe allowfullscreen src="javascript:alert(1)"></iframe>
        <iframe allowfullscreen></iframe>''', 'https://game.example/match/test/1/')
    assert [s['url'] for s in streams] == ['https://player.example/embed']


def test_registered_and_enabled_by_default(monkeypatch):
    monkeypatch.delenv('SUNDAYSIGNAL_SOURCES', raising=False)
    assert 'streamking' in sources.REGISTRY
    assert 'streamking' in [s.name for s in sources.get_sources()]


def test_resolver_receives_embed_referer_and_validates_hls(monkeypatch):
    game_url = 'https://beststreameast.xyz/match/nfl-network/19531/'
    stream = StreamKingSource().extract_streams((FIXTURES / 'streamking_player.html').read_text(), game_url)[0]
    calls = []
    def fetch(url, referer=None, **kwargs):
        calls.append((url, referer))
        return '<script>const source="https://cdn.example/live.m3u8";</script>'
    monkeypatch.setattr(scraper, 'fetch', fetch)
    monkeypatch.setattr(scraper, 'validate_hls_stream', lambda url, referer=None: True)
    record = {'game': {'title': 'NFL Network'}, 'referer': 'https://streamking.cx/',
              'streams': [stream], 'candidates': [stream], 'resolved': 0, 'in_flight': 0}
    scraper._resolve_records([record], 1)
    assert calls == [('https://embexex.pw/js/?2', game_url)]
    assert stream['media_url'] == 'https://cdn.example/live.m3u8'
    assert stream['health_checked_at']


def test_unhealthy_stream_not_published(monkeypatch):
    monkeypatch.setattr(scraper, 'fetch', lambda *a, **kw: '<script>let src="https://cdn.example/broken.m3u8";</script>')
    monkeypatch.setattr(scraper, 'validate_hls_stream', lambda *a, **kw: False)
    assert scraper.resolve_media_url('https://player.example/') is None


def test_streamking_not_skipped_when_another_backend_lists_same_game(monkeypatch):
    from sources.base import Source
    first = Source()
    first.name = 'existing'
    first.base_url = 'https://other.example'
    first.discover_games = lambda: [{'id': '1', 'slug': 'panthers-vs-packers',
        'title': 'Carolina Panthers vs Green Bay Packers', 'url': 'https://other.example/game'}]
    first.extract_streams = lambda *a: [{'name': 'other', 'url': 'https://other.example/player', 'media_url': None}]
    second = StreamKingSource()
    second.discover_games = lambda: second.parse_listing((FIXTURES / 'streamking_listing.html').read_text())[1:2]
    fetched = []
    def fetch(url, **kwargs):
        fetched.append(url)
        return (FIXTURES / 'streamking_player.html').read_text()
    monkeypatch.setattr(scraper, 'fetch', fetch)
    monkeypatch.setattr(scraper, 'REQUEST_DELAY', 0)
    records = scraper._collect_records([first, second])
    assert len(fetched) == 2
    assert [r['source'] for r in records] == ['existing', 'streamking']
    assert all(r['candidates'] for r in records)
