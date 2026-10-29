"""Telegram history, alternate mirrors and recursive stream resolution."""
from datetime import datetime, timedelta, timezone

import espn_schedule
import netfetch
import sundaysignal_scraper as scraper
from sources.base import Source
from sources.telegram import TelegramSource


def post(url, date=None, label='Live Stream'):
    time = f'<time datetime="{date}"></time>' if date else ''
    return f'<div class="tgme_widget_message">{time}<div class="tgme_widget_message_text"><a href="{url}">{label}</a></div></div>'


def test_alternate_hosts_for_same_id_are_preserved_newest_first():
    old = 'https://old.example/chiefs-vs-bills/1'
    new = 'https://new.example/match/chiefs-vs-bills/1?ref=channel'
    games = TelegramSource().parse_channel(post(old) + post(new) + post(new))
    assert [g['url'] for g in games] == [new, old]
    assert all(g['id'] == '1' for g in games)


def test_explicit_matchup_anchor_can_link_to_opaque_player():
    games = TelegramSource().parse_channel(post('https://player.example/live/abc', label='Kansas City Chiefs vs Buffalo Bills - Live Stream'))
    assert len(games) == 1
    assert games[0]['title'] == 'kansas city chiefs vs buffalo bills'
    assert TelegramSource().parse_channel(post('https://ads.example/', label='Join and win!')) == []


def test_paginated_discovery_dedupes_and_excludes_old_announcements(monkeypatch):
    now = datetime.now(timezone.utc)
    recent = now.isoformat()
    old = (now-timedelta(days=15)).isoformat()
    base = 'https://t.me/s/nflbite_official'
    url1 = 'https://one.example/chiefs-vs-bills/1'
    url2 = 'https://two.example/chiefs-vs-bills/1'
    pages = {
        base: post(url1, recent) + '<a class="tme_messages_more" href="?before=100">Older</a>',
        base+'?before=100': post(url2, recent) + post('https://old.example/texans-vs-giants/2', old),
    }
    seen = []
    def fetch(url, **kw):
        seen.append(url)
        return pages.get(url)
    monkeypatch.setattr(netfetch, 'fetch', fetch)
    games = TelegramSource().discover_games()
    assert [g['url'] for g in games] == [url1, url2]
    assert len(seen) == 2


def test_history_page_limit_and_external_pagination_are_respected(monkeypatch):
    seen = []
    def fetch(url, **kw):
        seen.append(url)
        return '<a class="tme_messages_more" href="?before=50">Older</a>'
    monkeypatch.setattr(netfetch, 'fetch', fetch)
    monkeypatch.setenv('SUNDAYSIGNAL_TELEGRAM_MAX_PAGES', '1')
    TelegramSource().discover_games()
    assert len(seen) == 1
    monkeypatch.setenv('SUNDAYSIGNAL_TELEGRAM_MAX_PAGES', '3')
    monkeypatch.setattr(netfetch, 'fetch', lambda *a, **kw: '<a class="tme_messages_more" href="https://ads.example/s/nflbite_official?before=50">Older</a>')
    assert TelegramSource().discover_games() == []


def test_nested_stream_tables_skip_dead_mirror_and_resolve_healthy_iframe(monkeypatch):
    pages = {
        'https://aggregate.example/links': '<table><tr><td>Dead</td><input type="hidden" id="linkk1" value="https://dead.example/player"></tr><tr><td>Good</td><input type="hidden" id="linkk2" value="https://next.example/table"></tr></table>',
        'https://next.example/table': '<input type="hidden" id="linkk1" value="https://player.example/watch">',
        'https://player.example/watch': '<iframe src="https://embed.example/live"></iframe>',
        'https://embed.example/live': '<script>let src="https://cdn.example/live.m3u8";</script>',
    }
    seen = []
    def fetch(url, referer=None, **kw):
        seen.append((url, referer))
        return pages.get(url)
    monkeypatch.setattr(scraper, 'fetch', fetch)
    monkeypatch.setattr(scraper, 'validate_hls_stream', lambda *a, **kw: True)
    result = scraper.resolve_media_url('https://aggregate.example/links')
    assert result['media_url'] == 'https://cdn.example/live.m3u8'
    assert result['chain'] == 'wrapper→table→table→iframe→m3u8'
    assert ('https://player.example/watch', 'https://next.example/table') in seen


def test_telegram_gets_independent_attempts_but_identical_pages_fetch_once(monkeypatch):
    source = TelegramSource()
    urls = ['https://mirror.example/chiefs-vs-bills/1', 'https://alternate.example/chiefs-vs-bills/1']
    source.discover_games = lambda: source.parse_channel(''.join(post(url) for url in urls))
    first = Source()
    first.name = 'nflbite'
    first.base_url = 'https://nflbite.example'
    first.discover_games = lambda: [{'id': '1', 'title': 'chiefs vs bills', 'url': urls[0]}]
    table = '<input type="hidden" id="linkk1" value="https://player.example/live">'
    first.extract_streams = lambda *a: source.extract_streams(table, urls[0])
    monkeypatch.setattr(scraper, 'fetch', lambda *a, **kw: table)
    monkeypatch.setattr(scraper, 'REQUEST_DELAY', 0)
    records = scraper._collect_records([first, source])
    assert len(records) == 2
    assert {r['game']['url'] for r in records} == set(urls)


def test_direct_player_announcements_use_shared_recursive_resolver():
    streams = TelegramSource().extract_streams('<div data-embed-url="/player"></div>', 'https://channel.example/watch')
    assert streams[0]['url'] == 'https://channel.example/watch'
    assert streams[0]['media_url'] is None


def test_old_opponent_not_attached_to_new_fixture():
    event = {'away_team': 'Philadelphia Eagles', 'home_team': 'Washington Commanders',
             'tokens': espn_schedule._team_tokens('Philadelphia Eagles') | espn_schedule._team_tokens('Washington Commanders')}
    assert espn_schedule.match_event('Philadelphia Eagles vs Dallas Cowboys', [event]) is None
    assert espn_schedule.match_event('Washington Commanders vs Philadelphia Eagles', [event]) is event


def test_current_channel_card_markup_extracts_named_providers_only():
    from pathlib import Path
    html = (Path(__file__).parent / 'fixtures' / 'telegram_stream_cards.html').read_text()
    streams = TelegramSource().extract_streams(html, 'https://sportslinks.example/game/1')
    assert [(s['name'], s['url']) for s in streams] == [
        ('SportsRope', 'https://bestropes.example/player.php/game'),
        ('Backup', 'https://sportslinks.example/alternate/player'),
    ]
    assert streams[0]['badges'] == ['HD']
    assert all(s['media_url'] is None for s in streams)


def test_recursive_resolver_also_follows_stream_cards(monkeypatch):
    pages = {
        'https://table.example/game': '<div class="stream-list"><a class="card" href="/player"><span class="streamer-name">Backup</span></a></div>',
        'https://table.example/player': '<script>const src="https://cdn.example/live.m3u8";</script>',
    }
    monkeypatch.setattr(scraper, 'fetch', lambda url, **kw: pages.get(url))
    monkeypatch.setattr(scraper, 'validate_hls_stream', lambda *a, **kw: True)
    assert scraper.resolve_media_url('https://table.example/game')['chain'] == 'wrapper→table→m3u8'
