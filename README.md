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

SundaySignal is a self-hosted multi-sport game-day dashboard that finds playable streams and makes them available on your local network through a web interface, IPTV playlist, and native Fire TV / Android TV app.

The web dashboard groups events into sport tabs such as Football, Hockey, and Soccer. Existing NFL sources continue to resolve to native HLS playback, while API-backed sports events use isolated embedded players inside the same SundaySignal interface. Alternate feeds are presented neutrally as Source 1, Source 2, and so on.

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

## Multi-sport configuration

The default source list is `nflbite,telegram,trend48`. The multi-sport provider is internal and is not shown as a brand in the user interface.

```yaml
environment:
  - SUNDAYSIGNAL_SOURCES=nflbite,telegram,trend48
  - SUNDAYSIGNAL_TREND48_CATEGORIES=football,hockey
```

The provider calls soccer `football`; SundaySignal displays it as **Soccer**. Add other upstream categories as a comma-separated list, such as `basketball`, `baseball`, `american-football`, `fight`, or `motor-sports`.

Optional settings:

- `SUNDAYSIGNAL_TREND48_URL` — provider root; defaults to `https://trend48.st`.
- `SUNDAYSIGNAL_TREND48_ENDPOINT` — match feed; defaults to `/api/matches/all-today`.
- `SUNDAYSIGNAL_TREND48_CATEGORIES` — enabled categories; defaults to `football,hockey`.

`/api/streams?sport=hockey` returns one sport while the unfiltered endpoint returns the complete catalog. IPTV playlists include direct HLS streams; provider-managed iframe feeds remain available through the SundaySignal web player.
