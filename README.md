<p align="center">
  <img src="static/sundaysignal_icon.png" alt="SundaySignal icon" width="128">
</p>

<h1 align="center">SundaySignal</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" alt="Docker Compose">
  <img src="https://img.shields.io/badge/Fire_TV-Android_TV-3DDC84?logo=android&logoColor=white" alt="Fire TV and Android TV">
  <img src="https://img.shields.io/badge/LAN-Port_8765-112852" alt="LAN port 8765">
</p>

SundaySignal is a self-hosted game-day dashboard that finds playable HLS streams and makes them available on your local network through a web interface, IPTV playlist, and native Fire TV / Android TV app.

## Quick start

```bash
git clone https://github.com/jcobu/sundaysignal.git
cd sundaysignal
docker compose up --build -d
```

Open `http://localhost:8765` on the Docker host, or use its LAN IP from another device:

```text
http://<host-ip>:8765
```

The crawler refreshes every 10 minutes by default. Change `CRAWL_INTERVAL_SECONDS` in `docker-compose.yml` if needed.

## Accounts and invitations

SundaySignal requires a local account. Plex login and Plex environment variables
are no longer used. Existing Plex sessions do not grant access.

1. Open the site once to generate the first administrator setup token.
2. Read it with `docker compose exec server cat /output/admin_setup_token.txt`
   or configure
   `SUNDAYSIGNAL_SETUP_TOKEN` before starting the service.
3. Visit `/setup`, enter that token, and choose your administrator email and a
   password of at least 12 characters. Setup closes after the admin is created.
4. In Settings, select **Manage users**. Create an account with an initial
   password, or leave the password blank to get a one-use invitation link.
   Share links yourself; email delivery is not configured. Links expire in 7 days.

The admin can disable accounts or issue a new invitation to reset a password.
Disabled accounts and password changes invalidate existing sessions. There is no
public registration. Accounts, invitations, and session signing keys persist in
`OUTPUT_DIR` (the Docker `/output` volume); keep that volume when upgrading.
Use a URL reachable by the recipient when copying an invitation link.

Rebuild and reinstall the Fire TV app for this auth change. It displays a code;
on a phone or computer, sign in and visit `<server-address>/device` to approve it.
Codes expire after 10 minutes. Previously built APKs use the removed Plex flow.

For IPTV clients and automation, configure `SUNDAYSIGNAL_ADMIN_TOKEN` and use
`/playlist.m3u?token=YOUR_TOKEN` or the `X-SundaySignal-Token` header. Token-based
playlists carry the token through playback requests. Rescrapes require an admin
session or this token. Ordinary users can view streams and the playlist.
When serving via HTTPS, set `SUNDAYSIGNAL_SECURE_COOKIES=1`.

## Stream sources

The default sources are `nflbite,telegram,streamking`. If you already set
`SUNDAYSIGNAL_SOURCES`, add `streamking` to that comma-separated list to enable it.
StreamKing reads the NFL category at `https://streamking.cx/categories/nfl/`,
including NFL Network and RedZone. Override its origin with
`SUNDAYSIGNAL_STREAMKING_BASE_URL` if the site moves.

StreamKing candidates use the game's page as their Referer and go through the
same HLS manifest and segment health checks as other sources. Upcoming pages
without an embedded player produce no candidates. A listing alone does not
mean playback is available: during integration testing, the two channel embeds
returned HTTP 403/404 and the upcoming game had no player, so live StreamKing
playback could not be confirmed. These failures do not replace working streams
from other sources.


## Playback compatibility

The resolver supports static iframes, declarative `data-embed-url` channel
selectors, Nexa-style public player JSON, and the encoded `_econfig` player
format. It follows a bounded set of alternate player paths and still requires a
healthy HLS playlist and media segment before publishing a source.

Each resolved stream's player URL is preserved as its playback Referer. The
proxy derives Origin from that URL and carries the same player context through
nested playlists, media segments, and encryption-key URLs. Older catalog entries
without a player URL use the configured `PROXY_REFERER` fallback. Refresh streams
after upgrading to regenerate playback links with the latest player metadata.

## Telegram discovery

The default `telegram` source reads the public preview at
`https://t.me/s/nflbite_official`; no Telegram account or API key is needed.
It scans newest announcements first, retains distinct mirror URLs for a fixture,
and makes an independent attempt even when NFLbite lists that same fixture.
Identical pages shared between sources are fetched only once.

`SUNDAYSIGNAL_TELEGRAM_MAX_PAGES` defaults to 3 (maximum 10), and
`SUNDAYSIGNAL_TELEGRAM_LOOKBACK_HOURS` defaults to 168 (maximum 336).
Only pagination on the configured channel is followed. Deleted posts and links
not present in Telegram's public preview cannot be recovered.

Both hidden-input tables and the current Sportslinks provider cards are
supported. Cascading tables, iframe players, dynamic channel selectors, player
APIs and supported encoded configurations feed into the same bounded recursive
resolver. Candidates are published only after HLS manifest and media-segment
health checks succeed. Matching both opponents prevents older announcements
from supplying streams for this week's different fixture.


## APK download

[Download SundaySignal.apk](dist/SundaySignal.apk?raw=true) — version 2.1.1.
The repository includes the current installable, debug-signed APK. Install it
over the previous APK to retain your settings. Server-side scraping and web UI
changes also require updating the server.
