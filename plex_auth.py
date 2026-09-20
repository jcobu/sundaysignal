"""Plex "Sign in with Plex" login gate — the pattern Overseerr/Tautulli use.

Off by default: leave SUNDAYSIGNAL_PLEX_OWNER_TOKEN unset and the app
behaves exactly as before, no login wall anywhere. Set it and the
interactive web UI (plus the JSON it loads and the playback proxy) start
requiring a Plex account that either owns this server's Plex account or
has been shared any library on it — same access-control model Overseerr
uses for its own login gate. IPTV/monitoring endpoints (M3U, EPG, health,
the rescrape API) are untouched either way; those keep working for tools
that can't do a browser login.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

PLEX_TV = "https://plex.tv"
PRODUCT = "SundaySignal"
_REQUEST_TIMEOUT = 10

OWNER_TOKEN = os.environ.get("SUNDAYSIGNAL_PLEX_OWNER_TOKEN", "").strip()
ENABLED = bool(OWNER_TOKEN)

#: Extra emails/usernames let in alongside the owner + anyone the owner's
#: Plex account has shared a server/library with. Rarely needed — mainly an
#: escape hatch if the friends-list lookup doesn't cover someone who should
#: have access.
ALLOWED_USERS = {
    u.strip().lower()
    for u in os.environ.get("SUNDAYSIGNAL_PLEX_ALLOWED_USERS", "").split(",")
    if u.strip()
}

FRIENDS_CACHE_SECONDS = 300

_STATE_DIR = Path(os.environ.get("OUTPUT_DIR", "/output"))
_friends_cache: dict = {"at": 0.0, "users": []}
_owner_account: dict | None = None


def persisted_value(filename: str, generate) -> str:
    """Read a small state file if present, else generate + save it once.

    Used for values that must stay stable across container restarts (the
    Plex client identifier, the Flask session secret) without requiring the
    admin to set an env var for something this app can just remember.
    """
    path = _STATE_DIR / filename
    try:
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
    except OSError:
        pass
    value = generate()
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
    except OSError as e:
        log.warning("could not persist %s: %s", filename, e)
    return value


CLIENT_IDENTIFIER = os.environ.get("SUNDAYSIGNAL_PLEX_CLIENT_ID") or persisted_value(
    "plex_client_id.txt", lambda: str(uuid.uuid4())
)


def _headers(token: str | None = None) -> dict:
    headers = {
        "Accept": "application/json",
        "X-Plex-Product": PRODUCT,
        "X-Plex-Client-Identifier": CLIENT_IDENTIFIER,
    }
    if token:
        headers["X-Plex-Token"] = token
    return headers


def create_pin() -> dict | None:
    """Start a login: a PIN the user redeems by signing in on plex.tv."""
    try:
        resp = requests.post(
            f"{PLEX_TV}/api/v2/pins",
            headers=_headers(),
            # Strong PINs are long opaque codes intended for the hosted auth
            # redirect.  They cannot be entered at plex.tv/link.  A regular
            # PIN is the four-character code that works both in our popup and
            # on another device (including the Fire TV flow).
            data={"strong": "false"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("plex pin creation failed: %s", e)
        return None


def auth_url(pin_code: str) -> str:
    """Plex's own hosted login page for this PIN, opened in the user's browser."""
    params = {
        "clientID": CLIENT_IDENTIFIER,
        "code": pin_code,
        "context[device][product]": PRODUCT,
    }
    return f"https://app.plex.tv/auth#?{urlencode(params)}"


def check_pin(pin_id: int) -> dict | None:
    """Poll a PIN's state; `authToken` is set once the user finishes signing in."""
    try:
        resp = requests.get(
            f"{PLEX_TV}/api/v2/pins/{pin_id}",
            headers=_headers(),
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("plex pin check failed: %s", e)
        return None


def fetch_account(token: str) -> dict | None:
    """The Plex account (id/username/email) that owns a given token."""
    try:
        resp = requests.get(f"{PLEX_TV}/api/v2/user", headers=_headers(token), timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("plex account lookup failed: %s", e)
        return None


def _owner_account_cached() -> dict | None:
    global _owner_account
    if _owner_account is None and OWNER_TOKEN:
        _owner_account = fetch_account(OWNER_TOKEN) or {}
    return _owner_account or None


def _fetch_friends() -> list[dict]:
    """Accounts the owner's Plex account has shared any server/library with."""
    try:
        resp = requests.get(f"{PLEX_TV}/api/users", headers={"X-Plex-Token": OWNER_TOKEN}, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        return [
            {"id": el.get("id"), "username": el.get("username"), "email": el.get("email")}
            for el in root.findall("User")
        ]
    except (requests.RequestException, ET.ParseError) as e:
        log.warning("plex friends list fetch failed: %s", e)
        return []


def _friends_cached() -> list[dict]:
    now = time.time()
    if now - _friends_cache["at"] > FRIENDS_CACHE_SECONDS:
        _friends_cache["users"] = _fetch_friends()
        _friends_cache["at"] = now
    return _friends_cache["users"]


def is_authorized(account: dict) -> bool:
    """True if this Plex account may use the site: the owner, an explicit
    allowlist entry, or anyone the owner's account shares a server/library
    with."""
    if not ENABLED:
        return False
    account_id = str(account.get("id") or "")
    email = (account.get("email") or "").strip().lower()
    username = (account.get("username") or "").strip().lower()

    owner = _owner_account_cached()
    if owner and account_id and str(owner.get("id")) == account_id:
        return True
    if ALLOWED_USERS and (email in ALLOWED_USERS or username in ALLOWED_USERS):
        return True
    for friend in _friends_cached():
        if account_id and str(friend.get("id")) == account_id:
            return True
        if email and (friend.get("email") or "").strip().lower() == email:
            return True
    return False
