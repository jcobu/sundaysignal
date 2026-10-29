"""Cross-provider channel rows must merge without losing playable alternatives."""
from copy import deepcopy

import webapp
import sundaysignal_scraper as scraper
from channel_catalog import merge_channels


def channel(id, title, source, urls=()):
    return {'id': id, 'uid': source+':'+id, 'title': title, 'source': source,
            'streams': [{'media_url': url, 'embed_url': 'https://player.example/',
                         'health_checked_at': '2026-10-29T00:00:00Z'} for url in urls]}


def test_duplicate_channels_merge_casing_and_alternatives_without_touching_games():
    games = [channel('1','NFL Redzone vs Live','nflbite',['https://cdn.example/a.m3u8']),
             channel('2','NFL NETWORK vs Live','nflbite'),
             channel('3','NFL RedZone vs Live','streamking',['https://cdn.example/a.m3u8','https://cdn.example/b.m3u8']),
             channel('4','NFL Network','telegram'),
             {'id':'5', 'title':'Houston Texans vs New York Giants'}]
    original = deepcopy(games)
    merged = merge_channels(games)
    assert len(merged) == 3
    assert merged[0]['title'] == 'NFL RedZone vs Live'
    assert merged[0]['uid'] == 'nflbite:1'
    assert merged[0]['stream_count'] == 2
    assert merged[0]['stream_sources'] == ['nflbite','streamking']
    assert merged[1]['title'] == 'NFL Network vs Live'
    assert merged[2] == games[4]
    assert games == original
    assert merge_channels(merged) == merged


def test_schedule_path_dedupes_unmatched_channels():
    games = [channel('1','NFL Network vs Live','nflbite'), channel('2','nfl network vs live','streamking')]
    scheduled = [{'id':'game','title':'Texans vs Giants'}]
    assert len(scraper._attach_to_schedule(scheduled,games,[])) == 2
    assert len(scraper._dedupe_scraped(games)) == 1


def test_existing_catalog_immediately_has_one_row_per_channel(monkeypatch):
    monkeypatch.setattr(webapp, 'espn_schedule', None)
    result = webapp.enrich_games({'games': [channel('1','NFL Redzone vs Live','nflbite'),
        channel('2','NFL RedZone','streamking'), channel('3','NFL NETWORK vs Live','nflbite'),
        channel('4','NFL Network','streamking')]})
    assert result['game_count'] == 2
    assert [g['display_title'] for g in result['games']] == ['NFL RedZone','NFL Network']
    assert all(g['always_live'] and not g['is_matchup'] for g in result['games'])


def test_previous_duplicate_rows_preserve_all_healthy_streams():
    old = {'games': [channel('1','NFL Redzone vs Live','nflbite',['https://cdn.example/a.m3u8']),
                     channel('2','NFL RedZone vs Live','streamking',['https://cdn.example/b.m3u8'])]}
    new = {'games': [channel('1','NFL RedZone vs Live','nflbite')]}
    result = scraper._merge_keep_previous(new,old)
    assert len(result['games']) == 1
    assert {s['media_url'] for s in result['games'][0]['streams']} == {'https://cdn.example/a.m3u8','https://cdn.example/b.m3u8'}
