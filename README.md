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
