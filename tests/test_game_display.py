"""Source-independent team casing and schedule ordering regressions."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import espn_schedule
import webapp


@pytest.mark.parametrize('title,slug,expected', [
    ('houston texans vs new york giants', '', 'Houston Texans vs New York Giants'),
    ('SAN FRANCISCO 49ERS VS NEW YORK JETS', '', 'San Francisco 49ers vs New York Jets'),
    ('', 'houston-texans-vs-new-york-giants', 'Houston Texans vs New York Giants'),
    ('Carolina Panthers vs Green Bay Packers', '', 'Carolina Panthers vs Green Bay Packers'),
])
def test_matchup_team_casing(title, slug, expected):
    parsed = webapp.display_matchup(title, slug)
    assert parsed['display_title'] == expected
    left, right = expected.split(' vs ')
    assert parsed['display_left_team'] == left
    assert parsed['display_right_team'] == right


def test_unmatched_scraped_game_is_capitalized_without_schedule(monkeypatch):
    monkeypatch.setattr(espn_schedule, 'fetch_scoreboard', lambda: [])
    data = {'games': [{'title': 'houston texans vs new york giants',
                       'away_team': 'houston texans', 'home_team': 'new york giants',
                       'streams': []}]}
    game = webapp.enrich_games(data)['games'][0]
    assert game['display_title'] == 'Houston Texans vs New York Giants'


def test_unknown_channel_name_is_preserved():
    assert webapp.parse_matchup('NFL RedZone vs Live')['display_title'] == 'NFL RedZone'
    assert webapp.parse_matchup('My custom TV channel')['display_title'] == 'My custom TV channel'


def test_undated_upcoming_games_follow_scheduled_games():
    games = [
        {'id': 'undated'},
        {'id': 'later', 'status_state': 'pre', 'start_time': '2026-11-01T21:00:00Z'},
        {'id': 'ended', 'status_state': 'post', 'start_time': '2026-10-25T17:00:00Z'},
        {'id': 'earlier', 'status_state': 'pre', 'start_time': '2026-10-29T00:15:00Z'},
        {'id': 'live', 'status_state': 'in'},
        {'id': 'null-date', 'status_state': 'pre', 'start_time': None},
        {'id': 'empty-date', 'status_state': 'pre', 'start_time': ''},
    ]
    assert [g['id'] for g in espn_schedule.sort_games_for_ui(games)] == [
        'live', 'earlier', 'later', 'undated', 'null-date', 'empty-date', 'ended',
    ]
