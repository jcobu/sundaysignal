"""Dynamic channel fallback and per-request HLS playback headers."""
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import netfetch
import sundaysignal_scraper as scraper
import webapp


def test_dynamic_channels_fall_back_with_correct_referer(monkeypatch):
    pages = {
        'https://sports.example/game': '<div data-embed-url="/embed?url=https://bad.example/player"></div><div data-embed-url="/embed?url=https://good.example/player"></div>',
        'https://sports.example/embed?url=https://bad.example/player': '<iframe src="https://bad.example/player"></iframe>',
        'https://sports.example/embed?url=https://good.example/player': '<iframe src="https://good.example/player"></iframe>',
        'https://bad.example/player': '<script>let src="https://cdn.example/bad.m3u8";</script>',
        'https://good.example/player': '<script>let src="https://cdn.example/good.m3u8";</script>',
    }
    seen = []
    def fetch(url, referer=None, **kw):
        seen.append((url, referer))
        return pages.get(url)
    monkeypatch.setattr(scraper, 'fetch', fetch)
    monkeypatch.setattr(scraper, 'validate_hls_stream', lambda url, referer=None: url.endswith('good.m3u8'))
    result = scraper.resolve_media_url('https://sports.example/game')
    assert result['media_url'] == 'https://cdn.example/good.m3u8'
    assert result['embed_url'] == 'https://good.example/player'
    assert ('https://good.example/player', 'https://sports.example/embed?url=https://good.example/player') in seen
    assert result['chain'] == 'wrapper→channel→iframe→m3u8'


def test_dynamic_player_cycles_and_hop_limit(monkeypatch):
    seen = []
    def fetch(url, **kw):
        seen.append(url)
        return '<div data-embed-url="/loop"></div>'
    monkeypatch.setattr(scraper, 'fetch', fetch)
    assert scraper.resolve_media_url('https://example.test/loop') is None
    assert len(seen) == 1
    monkeypatch.setattr(scraper, 'MAX_RESOLVE_HOPS', 1)
    seen.clear()
    assert scraper.resolve_media_url('https://example.test/start') is None
    assert len(seen) == 1


def test_playlist_rewrites_variants_keys_and_segments_with_parent_referer():
    parent = 'https://cdn.example/path/master.m3u8?sig=a%2Fb'
    body = b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin?x=1%2F2"\n#EXT-X-STREAM-INF:BANDWIDTH=100\nchild.m3u8\n'
    rewritten = webapp._rewrite_playlist(body, parent).decode()
    import re
    key = re.search(r'URI="([^"]+)"', rewritten).group(1)
    child = rewritten.splitlines()[-1]
    for link in (key, child):
        assert parse_qs(urlsplit(link).query)['referer'] == [parent]
    assert parse_qs(urlsplit(key).query)['url'] == ['https://cdn.example/path/key.bin?x=1%2F2']


@pytest.fixture
def proxy_client(monkeypatch):
    monkeypatch.setattr(webapp, 'current_user', lambda: {'id': 1, 'admin': True})
    monkeypatch.setattr(webapp, '_is_safe_public_host', lambda host: True)
    return webapp.app.test_client()


def test_proxy_uses_embed_headers_and_preserves_signed_escapes(proxy_client, monkeypatch):
    calls = []
    media = 'https://cdn.example/master.m3u8?sig=a%2Fb%2Bc'
    embed = 'https://player.example:8443/watch/1'
    def get(url, **kw):
        calls.append((url, kw))
        return SimpleNamespace(url=media, content=b'#EXTM3U\n#EXTINF:6,\nseg.ts', status_code=200, headers={'Content-Type': 'application/vnd.apple.mpegurl'})
    monkeypatch.setattr(webapp.SESSION, 'get', get)
    response = proxy_client.get(webapp._proxy_media_url(media, embed))
    assert response.status_code == 200
    assert calls[0][0] == media
    assert calls[0][1]['headers']['Referer'] == embed
    assert calls[0][1]['headers']['Origin'] == 'https://player.example:8443'
    segment = response.text.splitlines()[-1]
    assert parse_qs(urlsplit(segment).query)['referer'] == [embed]


@pytest.mark.parametrize('referer', ['javascript:alert(1)', 'https://x.example/\r\nInjected: yes', 'https://user:password@x.example/'])
def test_proxy_rejects_invalid_referers(proxy_client, referer):
    assert proxy_client.get(webapp._proxy_media_url('https://cdn.example/video.ts', referer)).status_code == 400


def test_catalog_and_iptv_preserve_each_stream_embed(monkeypatch):
    monkeypatch.setattr(webapp, 'espn_schedule', None)
    stream = {'media_url': 'https://cdn.example/live.m3u8', 'embed_url': 'https://player.example/live'}
    data = webapp.enrich_games({'games': [{'id': '1', 'title': 'NFL Network vs Live', 'streams': [stream]}]})
    assert parse_qs(urlsplit(stream['play_url']).query)['referer'] == [stream['embed_url']]
    with webapp.app.test_request_context('/'):
        rows = list(webapp.iter_playable_streams(data))
    assert rows[0]['embed_url'] == stream['embed_url']


def test_hls_probe_sends_origin_for_its_referer(monkeypatch):
    captured = []
    class Response:
        url = 'https://cdn.example/seg.ts'
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, **kw): yield b'media'
    def get(url, **kw):
        captured.append(kw['headers'])
        return Response()
    monkeypatch.setattr(netfetch.SESSION, 'get', get)
    assert netfetch.fetch_bytes('https://cdn.example/seg.ts', referer='https://embed.example/player')
    assert captured[0]['Origin'] == 'https://embed.example'


def test_player_api_handoff_without_executing_scripts(monkeypatch):
    pages = {
        'https://nexa.example/ch.php?id=1': '<script>fetch("api/player.php?id=" + encodeURIComponent(safeId))</script>',
        'https://nexa.example/api/player.php?id=1': '{"url":"https://player.example/live"}',
        'https://player.example/live': '<script>const src="https://cdn.example/live.m3u8";</script>',
    }
    seen = []
    def fetch(url, referer=None, **kw):
        seen.append((url, referer))
        return pages.get(url)
    monkeypatch.setattr(scraper, 'fetch', fetch)
    monkeypatch.setattr(scraper, 'validate_hls_stream', lambda *a, **kw: True)
    result = scraper.resolve_media_url('https://nexa.example/ch.php?id=1')
    assert result['media_url'] == 'https://cdn.example/live.m3u8'
    assert ('https://player.example/live', 'https://nexa.example/ch.php?id=1') in seen


def test_same_player_referer_survives_master_variant_and_segment(monkeypatch):
    refs = []
    def fetch(url, referer=None, **kw):
        refs.append(referer)
        if url.endswith('master.m3u8'):
            return b'#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100\nvariant.m3u8', url
        if url.endswith('variant.m3u8'):
            return b'#EXTM3U\n#EXTINF:6,\nseg.ts', url
        return b'media', url
    monkeypatch.setattr(netfetch, 'fetch_bytes', fetch)
    assert scraper.validate_hls_stream('https://cdn.example/master.m3u8', 'https://embed.example/player')
    assert refs == ['https://embed.example/player'] * 3


def test_encoded_player_config_resolves_without_running_javascript(monkeypatch):
    import base64
    import json
    raw = json.dumps({'stream_url': 'https://cdn.example/live.m3u8?sig=a%2Fb'}).encode()
    raw += b' ' * (-len(raw) % 12)
    encoded = base64.b64encode(raw).decode()
    size = len(encoded) // 4
    chunks = [base64.b64encode(encoded[i * size:(i + 1) * size].encode()).decode() for i in range(4)]
    packed = ''.join(chunks[i][:3] + 'X' + chunks[i][3:] for i in (2, 0, 3, 1))
    payload = base64.b64encode(packed.encode()).decode()
    html = "<script>window._econfig='" + payload + "';</script>"
    monkeypatch.setattr(scraper, 'fetch', lambda *a, **kw: html)
    monkeypatch.setattr(scraper, 'validate_hls_stream', lambda *a, **kw: True)
    result = scraper.resolve_media_url('https://embed.example/live')
    assert result['media_url'] == 'https://cdn.example/live.m3u8?sig=a%2Fb'
    assert result['embed_url'] == 'https://embed.example/live'
    assert result['chain'] == 'wrapper→config→hls'
    assert scraper._encoded_player_media("window._econfig='not valid base64'") == []
