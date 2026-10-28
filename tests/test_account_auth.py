"""Exercise invitation-only account lifecycle and HTTP authorization boundaries."""
import re
import sys
import time
from pathlib import Path

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import account_auth as auth
import webapp


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, 'STATE_DIR', tmp_path)
    monkeypatch.setenv('SUNDAYSIGNAL_SETUP_TOKEN', 'bootstrap-secret')
    monkeypatch.setattr(webapp, 'ADMIN_TOKEN', '')
    monkeypatch.setattr(webapp, 'JSON_PATH', tmp_path / 'catalog.json')
    webapp.app.config.update(TESTING=True, SECRET_KEY='test-secret')
    return webapp.app.test_client()


def post(client, path, **fields):
    client.get(path)
    with client.session_transaction() as session:
        token = session['csrf']
    return client.post(path, data={'csrf': token, **fields})


def bootstrap(client):
    response = post(client, '/setup', token='bootstrap-secret', email='admin@example.com', password='long-admin-password')
    assert response.status_code == 302


def test_setup_protected_and_single_use(client):
    assert client.get('/').location == '/setup'
    assert client.get('/api/streams').status_code == 401
    assert client.get('/api/health').status_code == 200
    assert client.post('/setup', data={}).status_code == 400
    assert 'Invalid setup token' in post(client, '/setup', token='bad', email='a@b.com', password='long-password').text
    bootstrap(client)
    assert client.get('/setup').location == '/login'
    assert client.get('/').status_code == 200
    assert 'Manage users' in client.get('/').text
    assert client.get('/auth/plex/poll/1').status_code == 404


def test_invite_single_use_expiry_and_member_permissions(client):
    bootstrap(client)
    response = post(client, '/admin/users', email='friend@example.com')
    invitation = re.search(r'/invite\?token=([A-Za-z0-9_-]+)', response.text).group(1)
    member = webapp.app.test_client()
    assert post(member, '/invite?token='+invitation, password='friend-password-123').status_code == 302
    assert member.get('/api/streams').status_code == 200
    assert member.get('/playlist.m3u').status_code == 200
    assert member.get('/admin/users').status_code == 403
    assert member.get('/api/rescrape').status_code == 403
    assert 'invalid or expired' in post(webapp.app.test_client(), '/invite?token='+invitation, password='another-password').text
    user_id = auth.authenticate('FRIEND@example.com', 'friend-password-123')
    assert user_id
    expired = auth.invite(user_id)
    with auth.db() as conn:
        conn.execute('UPDATE invites SET expires=?', (time.time()-1,))
    with pytest.raises(ValueError):
        auth.accept(expired, 'a-long-password')
    post(client, '/admin/users', action='toggle', user_id=user_id)
    assert member.get('/api/streams').status_code == 401
    assert auth.authenticate('friend@example.com', 'friend-password-123') is None
    post(client, '/admin/users', action='toggle', user_id=user_id)
    assert member.get('/api/streams').status_code == 401


def test_direct_account_reset_revokes_sessions_and_logout_csrf(client):
    bootstrap(client)
    assert post(client, '/admin/users', email='user@example.com', password='initial-password').status_code == 200
    user_id = auth.authenticate('user@example.com', 'initial-password')
    member = webapp.app.test_client()
    assert post(member, '/login', email='user@example.com', password='initial-password').status_code == 302
    assert member.get('/').status_code == 200
    token = auth.invite(user_id)
    auth.accept(token, 'replacement-password')
    assert member.get('/api/streams').status_code == 401
    assert client.post('/logout').status_code == 400
    assert post(client, '/logout').status_code == 302
    assert client.get('/').location == '/login'


def test_device_code_bound_to_requesting_tv_and_consumed(client):
    bootstrap(client)
    tv = webapp.app.test_client()
    pin = tv.post('/auth/device/pin').get_json()
    path = '/auth/device/poll/'+str(pin['id'])
    assert webapp.app.test_client().get(path).status_code == 400
    assert tv.get(path).get_json()['authenticated'] is False
    assert 'TV approved' in post(client, '/device', code=pin['code']).text
    assert tv.get(path).get_json()['authenticated'] is True
    assert tv.get('/api/streams').status_code == 200
    assert tv.get(path).status_code == 400


def test_token_access_and_playlist_rewriting(client, monkeypatch):
    assert client.get('/playlist.m3u').status_code == 401
    assert client.get('/api/rescrape').status_code == 403
    monkeypatch.setattr(webapp, 'ADMIN_TOKEN', 'automation-secret')
    assert client.get('/playlist.m3u?token=wrong').status_code == 401
    assert client.get('/playlist.m3u?token=automation-secret').status_code == 200
    assert client.get('/proxy?url=invalid&token=automation-secret').status_code == 400
    with webapp.app.test_request_context('/proxy?token=automation-secret'):
        rewritten = webapp._rewrite_playlist(b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key"\nsegment.ts', 'https://cdn.example/live/')
        assert rewritten.count(b'&token=automation-secret') == 2


def test_login_rate_limit_and_password_storage(client):
    bootstrap(client)
    with auth.db() as conn:
        stored = conn.execute('SELECT password FROM users').fetchone()[0]
    assert stored != 'long-admin-password'
    other = webapp.app.test_client()
    for _ in range(10):
        assert post(other, '/login', email='admin@example.com', password='wrong').status_code == 200
    assert post(other, '/login', email='admin@example.com', password='long-admin-password').status_code == 429


def test_admin_rescrape_requires_csrf_but_automation_token_works(client, monkeypatch):
    bootstrap(client)
    client.get('/')
    with client.session_transaction() as session:
        csrf = session['csrf']
    monkeypatch.setitem(webapp._rescrape_state, 'running', True)
    assert client.get('/api/rescrape').status_code == 403
    assert client.post('/api/rescrape').status_code == 403
    assert client.post('/api/rescrape', headers={'X-CSRF-Token': csrf}).status_code == 200
    monkeypatch.setattr(webapp, 'ADMIN_TOKEN', 'external-secret')
    assert webapp.app.test_client().post('/api/rescrape', headers={'X-SundaySignal-Token': 'external-secret'}).status_code == 200


def test_invalid_unicode_token_is_rejected(client):
    assert client.get('/setup').status_code == 200
    assert client.post('/setup', data={'csrf': '☃'}).status_code == 400
    assert 'Invalid setup token' in post(client, '/setup', token='☃', email='a@b.com', password='long-password').text
