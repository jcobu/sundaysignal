<p align="center">
  <img src="static/sundaysignal_icon.jpg" alt="SundaySignal icon" width="128">
</p>

<h1 align="center">SundaySignal</h1>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" alt="Docker Compose">
  <img src="https://img.shields.io/badge/Fire_TV-Android_TV-3DDC84?logo=android&logoColor=white" alt="Fire TV and Android TV">
  <img src="https://img.shields.io/badge/LAN-Port_8765-112852" alt="LAN port 8765">
</p>

SundaySignal is a self-hosted game-day dashboard that finds playable HLS streams and makes them available on your local network through a web interface, IPTV playlist, and native Fire TV / Android TV app.

## Features

- Dockerized crawler and web server
- TV-friendly web interface
- Native Fire TV / Android TV client with automatic LAN discovery
- Full-screen HLS playback
- M3U playlist for VLC, TiviMate, and similar players

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

## Configuration

All of these are optional environment variables on the `crawler` service in `docker-compose.yml`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SUNDAYSIGNAL_BASE_URL` | `https://www.nflbite.is` | Source site to crawl. Set this if the source rotates domains. |
| `SUNDAYSIGNAL_MAX_RESOLVE_PER_GAME` | `6` | Max distinct playable streams to resolve per game. |
| `SUNDAYSIGNAL_RESOLVE_WORKERS` | `6` | Thread pool size for resolving streams concurrently. |
| `SUNDAYSIGNAL_PROXIES` | (none) | Comma-separated proxy URLs (`http://user:pass@host:port`, `socks5://host:port`); one is picked at random per outbound request to spread lookups across egress IPs. Empty = direct connection. |
| `SUNDAYSIGNAL_DEAD_HOST_TTL_HOURS` | `24` | How long a mirror host that failed DNS/timeout is skipped before being retried. The dead-host list itself persists to `dead_hosts.json` in the output volume across crawl cycles. |
| `SUNDAYSIGNAL_DEBUG_RESOLVE` | (unset) | Set to `1` for verbose per-stream resolve logging. |
| `SUNDAYSIGNAL_DEBUG_DUMP_DIR` | (unset) | Directory to save raw HTML for the first few resolve failures of each kind — useful when a provider changes its page structure. |

## Running tests

```bash
pip install pytest
pytest
```

The suite in `tests/` uses real HTML captured from source mirrors to guard against the embed-chain format silently drifting again.

## Fire TV / Android TV

Build the app from `firetv-app/`:

```bash
cd firetv-app
JAVA_HOME=/opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home ./gradlew assembleDebug
```

The APK is created at:

```text
firetv-app/app/build/outputs/apk/debug/app-debug.apk
```

See [firetv-app/README.md](firetv-app/README.md) for sideloading instructions.

## Local endpoints

| Endpoint | Purpose |
| --- | --- |
| `/` | Web interface |
| `/api/streams` | Stream catalog JSON |
| `/playlist.m3u` | IPTV playlist |
| `/api/health` | Server discovery and health check |

## Useful commands

```bash
docker compose logs -f crawler
docker compose restart crawler
docker compose down
```

> SundaySignal is intended for personal use with streams you are authorized to access.
