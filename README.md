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

- Dockerized crawler and web server, both reporting Docker health status
- TV-friendly web interface
- Native Fire TV / Android TV client with automatic LAN discovery
- Full-screen HLS playback, with alternate sources per game and automatic failover when one breaks
- M3U playlist and XMLTV guide for VLC, TiviMate, and similar players
- Pluggable source adapters, so a site changing or dying doesn't mean a rewrite

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
| `SUNDAYSIGNAL_RESOLVE_WORKERS` | `6` | Thread pool size for resolving streams concurrently, shared across all games at once. |
| `SUNDAYSIGNAL_MAX_RESOLVE_HOPS` | `4` | Max nested-iframe hops to follow per candidate stream before giving up on it. |
| `SUNDAYSIGNAL_RESOLVE_TIMEOUT` | `6` | Per-request timeout (seconds) for the many one-off third-party mirror fetches during resolution — kept shorter than the main site's timeout so a hung mirror fails fast. |
| `SUNDAYSIGNAL_PROXIES` | (none) | Comma-separated proxy URLs (`http://user:pass@host:port`, `socks5://host:port`); one is picked at random per outbound request to spread lookups across egress IPs. Empty = direct connection. |
| `SUNDAYSIGNAL_DEAD_HOST_TTL_HOURS` | `24` | How long a mirror host that failed DNS/timeout is skipped before being retried. The dead-host list itself persists to `dead_hosts.json` in the output volume across crawl cycles. |
| `SUNDAYSIGNAL_SOURCES` | `nflbite` | Comma-separated source adapters to crawl, in order. Unknown names are skipped with a warning. |
| `SUNDAYSIGNAL_LOG_LEVEL` | `INFO` | Log verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `SUNDAYSIGNAL_NOTIFY_URL` | (none) | Where to post an alert after repeated empty crawls — an ntfy topic, a Discord webhook, or any endpoint accepting JSON. Disabled when unset. |
| `SUNDAYSIGNAL_NOTIFY_AFTER` | `3` | Consecutive crawls resolving 0 streams before alerting. |
| `SUNDAYSIGNAL_EPG_BLOCK_HOURS` | `3.5` | Programme length used in the XMLTV guide (ESPN gives a kickoff but no end time). |
| `SUNDAYSIGNAL_DEBUG_RESOLVE` | (unset) | Set to `1` for verbose per-stream resolve logging without making everything else verbose. |
| `SUNDAYSIGNAL_DEBUG_DUMP_DIR` | (unset) | Directory to save raw HTML for the first few resolve failures of each kind — useful when a provider changes its page structure. |

Both containers report Docker health status: the server is healthy when `/api/health` answers, the crawler when a cycle completed recently. `docker ps` will show `(unhealthy)` if either gets stuck.

## Adding a source

These aggregator sites change markup and rotate domains constantly, so each one lives behind a small adapter in `sources/` — the core owns fetching, the dead-host cache, the nested-iframe resolve chain and output.

To add one, implement `Source` (see `sources/base.py`) with `discover_games()`, `extract_streams()`, and optionally `rank_stream()`, register it in `sources/__init__.py`, and add it to `SUNDAYSIGNAL_SOURCES`. Nothing in the core needs to change, and a source that breaks doesn't take the others down with it.

## Running tests

```bash
pip install pytest
pytest
```

The suite in `tests/` uses real HTML captured from source mirrors to guard against the embed-chain format silently drifting again.

## Versioning

The running app's version and build time are shown in the web UI header (hover the badge next to the logo for the build timestamp), in `GET /api/health`, and in the crawler's startup log line — check any of those to confirm exactly which build you're running or testing.

Versions follow [Semantic Versioning](https://semver.org/) and are tracked in the `VERSION` file at the repo root; `CHANGELOG.md` documents what shipped in each one. `VERSION` is bumped by hand whenever a meaningful set of changes lands — patch for fixes/tuning, minor for new features, major for breaking changes to config or data format. The build timestamp itself is generated automatically at `docker compose build` time, so even between version bumps you can tell whether you're running a freshly built image.

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
| `/playlist.m3u` | IPTV playlist (one row per resolved stream, so alternates are available as fallbacks) |
| `/epg.xml` | XMLTV guide matching the playlist's channels |
| `/api/health` | Server discovery and health check |

The playlist, guide and JSON URLs are also listed under **⚙ Settings** in the web interface, each with a Copy button so they can be pasted straight into VLC or TiviMate.

## Useful commands

```bash
docker compose logs -f crawler
docker compose restart crawler
docker compose down
```

> SundaySignal is intended for personal use with streams you are authorized to access.
