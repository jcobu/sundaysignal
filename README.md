<p align="center">
  <img src="static/sundaysignal-logo.svg" alt="SundaySignal" width="420">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" alt="Docker Compose">
  <img src="https://img.shields.io/badge/Fire_TV-Android_TV-3DDC84?logo=android&logoColor=white" alt="Fire TV and Android TV">
  <img src="https://img.shields.io/badge/LAN-Port_8765-112852" alt="LAN port 8765">
</p>

# SundaySignal

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
