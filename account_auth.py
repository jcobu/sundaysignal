"""Persistent, invitation-only local accounts. No external identity provider."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import time
from werkzeug.security import generate_password_hash, check_password_hash

STATE_DIR = Path(os.environ.get('OUTPUT_DIR', '/output'))
_DUMMY_PASSWORD = generate_password_hash(secrets.token_hex(32))


def secure_equal(left, right):
    return secrets.compare_digest(left.encode('utf-8'), right.encode('utf-8'))


def persisted_value(filename, generate):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = STATE_DIR / filename
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    value = generate()
    with os.fdopen(fd, 'w') as stream:
        stream.write(value)
    return value


@contextmanager
def db():
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE_DIR / 'accounts.sqlite3', timeout=15)
    conn.row_factory = sqlite3.Row
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY, email TEXT UNIQUE NOT NULL,
      password TEXT, admin INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1, session_version INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS invites (
      digest TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS devices (
      id INTEGER PRIMARY KEY, secret TEXT NOT NULL, code TEXT UNIQUE NOT NULL,
      expires REAL NOT NULL, user_id INTEGER);
    CREATE TABLE IF NOT EXISTS attempts (key TEXT PRIMARY KEY, count INTEGER, expires REAL);
    ''')
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def validate(email, password=None):
    email = email.strip().lower()
    if len(email) > 254 or '@' not in email or any(c.isspace() for c in email):
        raise ValueError('Enter a valid email address.')
    if password is not None and not 12 <= len(password) <= 256:
        raise ValueError('Use a password between 12 and 256 characters.')
    return email


def user(user_id):
    with db() as conn:
        row = conn.execute('SELECT id,email,admin FROM users WHERE id=? AND active=1 AND password IS NOT NULL', (user_id,)).fetchone()
        return dict(row) if row else None


def initialized():
    with db() as conn:
        return conn.execute('SELECT 1 FROM users WHERE admin=1').fetchone() is not None


def setup(email, password, token):
    expected = os.environ.get('SUNDAYSIGNAL_SETUP_TOKEN') or persisted_value('admin_setup_token.txt', lambda: secrets.token_urlsafe(32))
    if not secure_equal(token, expected):
        raise ValueError('Invalid setup token.')
    email = validate(email, password)
    hashed = generate_password_hash(password)
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM users WHERE admin=1').fetchone():
            raise ValueError('Setup is already complete.')
        return conn.execute('INSERT INTO users(email,password,admin) VALUES (?,?,1)', (email, hashed)).lastrowid


def authenticate(email, password):
    with db() as conn:
        row = conn.execute('SELECT * FROM users WHERE email=? AND active=1', (email.strip().lower(),)).fetchone()
    # Always run the password hash to avoid a cheap account-existence oracle.
    hashed = row['password'] if row and row['password'] else _DUMMY_PASSWORD
    return row['id'] if check_password_hash(hashed, password) and row and row['password'] else None


def create_user(email, password=None):
    email = validate(email, password)
    with db() as conn:
        try:
            return conn.execute('INSERT INTO users(email,password) VALUES (?,?)', (email, generate_password_hash(password) if password else None)).lastrowid
        except sqlite3.IntegrityError:
            raise ValueError('An account with that email already exists.') from None


def invite(user_id):
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute('DELETE FROM invites WHERE user_id=?', (user_id,))
        conn.execute('INSERT INTO invites VALUES (?,?,?)', (digest(token), user_id, time.time() + 7 * 86400))
    return token


def accept(token, password):
    validate('invite@example.com', password)
    hashed = generate_password_hash(password)
    with db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT user_id FROM invites WHERE digest=? AND expires>?', (digest(token), time.time())).fetchone()
        if not row:
            raise ValueError('This invitation is invalid or expired.')
        conn.execute('UPDATE users SET password=?,active=1 WHERE id=?', (hashed, row['user_id']))
        conn.execute('DELETE FROM invites WHERE user_id=?', (row['user_id'],))
        conn.execute('DELETE FROM devices WHERE user_id=?', (row['user_id'],))
        return row['user_id']


def throttled(key, limit=10):
    now = time.time()
    with db() as conn:
        conn.execute('DELETE FROM attempts WHERE expires<?', (now,))
        conn.execute('INSERT INTO attempts VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, now + 900))
        return conn.execute('SELECT count FROM attempts WHERE key=?', (key,)).fetchone()[0] > limit


def auth_stamp(user_id):
    with db() as conn:
        row = conn.execute('SELECT password,session_version FROM users WHERE id=?', (user_id,)).fetchone()
        return digest(row['password'] + ':' + str(row['session_version'])) if row and row['password'] else ''


def invite_info(token):
    """Who an unexpired invite or reset link is for, without consuming it."""
    with db() as conn:
        row = conn.execute(
            'SELECT users.email, users.password FROM invites JOIN users ON users.id=invites.user_id '
            'WHERE invites.digest=? AND invites.expires>?', (digest(token), time.time())).fetchone()
    return {'email': row['email'], 'reset': row['password'] is not None} if row else None


def change_password(user_id, current, new):
    validate('change@example.com', new)
    with db() as conn:
        row = conn.execute('SELECT password FROM users WHERE id=? AND active=1', (user_id,)).fetchone()
    if not row or not row['password'] or not check_password_hash(row['password'], current):
        raise ValueError('Your current password is incorrect.')
    with db() as conn:
        # A new hash changes auth_stamp, which signs out every other session.
        conn.execute('UPDATE users SET password=? WHERE id=?', (generate_password_hash(new), user_id))
        conn.execute('DELETE FROM devices WHERE user_id=?', (user_id,))
