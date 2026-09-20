import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import sundaysignal_scraper as scraper


def _fake_fetch(mapping, seen):
    def fetch_bytes(url, referer=None, timeout=12, max_bytes=2048, range_request=True):
        seen.append((url, referer, max_bytes))
        value = mapping.get(url)
        if value is None:
            return None
        return value.encode(), url
    return fetch_bytes


def test_hls_health_accepts_media_playlist_with_reachable_segment(monkeypatch):
    seen = []
    mapping = {
        "https://cdn.example/live/index.m3u8": "#EXTM3U\n#EXTINF:6,\nseg-1.ts\n",
        "https://cdn.example/live/seg-1.ts": "media bytes",
    }
    monkeypatch.setattr(scraper.netfetch, "fetch_bytes", _fake_fetch(mapping, seen))

    assert scraper.validate_hls_stream("https://cdn.example/live/index.m3u8", "https://embed.example/")
    assert seen[1][0] == "https://cdn.example/live/seg-1.ts"
    assert seen[1][1] == "https://cdn.example/live/index.m3u8"


def test_hls_health_follows_master_playlist_to_working_variant(monkeypatch):
    seen = []
    mapping = {
        "https://cdn.example/master.m3u8": (
            "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000\n720/index.m3u8\n"
        ),
        "https://cdn.example/720/index.m3u8": "#EXTM3U\n#EXTINF:6,\nsegment.ts\n",
        "https://cdn.example/720/segment.ts": "media bytes",
    }
    monkeypatch.setattr(scraper.netfetch, "fetch_bytes", _fake_fetch(mapping, seen))

    assert scraper.validate_hls_stream("https://cdn.example/master.m3u8")
    assert [call[0] for call in seen] == [
        "https://cdn.example/master.m3u8",
        "https://cdn.example/720/index.m3u8",
        "https://cdn.example/720/segment.ts",
    ]


def test_hls_health_rejects_html_disguised_as_m3u8(monkeypatch):
    seen = []
    mapping = {"https://bad.example/live.m3u8": "<html>expired</html>"}
    monkeypatch.setattr(scraper.netfetch, "fetch_bytes", _fake_fetch(mapping, seen))

    assert not scraper.validate_hls_stream("https://bad.example/live.m3u8")


def test_hls_health_rejects_playlist_with_dead_segment(monkeypatch):
    seen = []
    mapping = {
        "https://bad.example/live.m3u8": "#EXTM3U\n#EXTINF:6,\nmissing.ts\n",
    }
    monkeypatch.setattr(scraper.netfetch, "fetch_bytes", _fake_fetch(mapping, seen))

    assert not scraper.validate_hls_stream("https://bad.example/live.m3u8")


def test_hls_health_rejects_html_returned_for_segment(monkeypatch):
    seen = []
    mapping = {
        "https://bad.example/live.m3u8": "#EXTM3U\n#EXTINF:6,\nsegment.ts\n",
        "https://bad.example/segment.ts": "<html>access denied</html>",
    }
    monkeypatch.setattr(scraper.netfetch, "fetch_bytes", _fake_fetch(mapping, seen))

    assert not scraper.validate_hls_stream("https://bad.example/live.m3u8")


def test_resolver_does_not_publish_unhealthy_hls(monkeypatch):
    monkeypatch.setattr(
        scraper,
        "fetch",
        lambda url, referer=None, timeout=12: (
            '<script>const stream = "https://bad.example/live.m3u8";</script>'
        ),
    )
    monkeypatch.setattr(scraper, "validate_hls_stream", lambda url, referer=None: False)

    assert scraper.resolve_media_url("https://wrapper.example/player") is None
