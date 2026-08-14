# SundaySignal

A self-hosted game-day dashboard that discovers listed games and stream links, resolves playable HLS media, serves it on your LAN, and provides web, IPTV, Fire TV, and optional Roku clients.

## Fire TV app

The recommended television client is the native Fire TV app:

- **APK:** `SundaySignal-FireTV.apk`
- **Source:** `firetv-app/`
- **Discovery:** scans the local `/24` network for SundaySignal on port 8765
- **Playback:** native Media3/ExoPlayer HLS
- **Controls:** directional pad to browse, Select to play, Back to close video,
  Menu or **Find Server** to rediscover

See [`firetv-app/README.md`](firetv-app/README.md) for build and sideload steps.

## Architecture

```
┌─────────────────────┐     scrape      ┌──────────────────┐
│  nflbite.is         │ ───────────────►│  Docker crawler  │
└─────────────────────┘                 │  (JSON output)   │
                                        └────────┬─────────┘
                                                 │ writes
                                                 ▼
                                        ┌──────────────────┐
                                        │  ./output/       │
                                        │ SundaySignal JSON│
                                        └────────┬─────────┘
                                                 │ served by
                                                 ▼
┌─────────────────────┐   HTTP :8765    ┌──────────────────┐
│  Fire TV / Roku     │ ◄───────────────│  Docker server   │
│  (sideloaded        │                 │  (UI + API + HLS)│
│   local clients)    │                 └──────────────────┘
└─────────────────────┘
```



## Stream source chain (important)

A link like `https://live2.totalsporteks.st/...` is a **full webpage**, not a stream.

The crawler tracks the real source as:

1. **Wrapper** (totalsporteks HTML + ads/chat)
2. **Embed** (`iframe.st/rampages/...` player page)
3. **Decrypt** embedded config → **HLS playlist** e.g. `https://fingersoon.st/scripts/application5`
4. Playlist points at **short-lived signed segment URLs** on Cloudflare R2

Only step 3 (`media_url`) is stored as playable. Players (web GUI, Roku, VLC) should open the HLS playlist, not the wrapper.

## Web GUI

After `docker compose up --build -d`:

- **UI (watch streams):** `http://<host>:8765/`
- **JSON API (Roku):** `http://<host>:8765/api/streams`  
  (also `/sundaysignal_streams.json`)

The UI lists only games with resolved HLS `media_url` and plays them with hls.js — no full wrapper webpage.

## Automatic mode (recommended)

```bash
cd sundaysignal
docker compose up --build -d
```

This starts two containers:

1. **crawler** – discovers games, resolves real HLS `media_url`s, writes `./output/sundaysignal_streams.json`, then sleeps and repeats.
2. **server** – serves that JSON at `http://<this-machine-ip>:8765/sundaysignal_streams.json` for the Roku channel.

Default scrape interval: **900 seconds (15 minutes)**. Change it with:

```bash
# e.g. every 5 minutes during game day
CRAWL_INTERVAL_SECONDS=300 docker compose up -d
```

Or edit `docker-compose.yml` → `CRAWL_INTERVAL_SECONDS`.

Useful commands:

```bash
docker compose logs -f crawler    # watch scrapes
docker compose restart crawler    # force a new cycle soon after restart
docker compose down               # stop everything
```

Only streams that successfully resolve to playable HLS are kept in the JSON.

## Quick start

### 1. Run the stack on a computer on the same network as the Roku

```bash
cd sundaysignal
docker compose up --build -d
```

This starts:
- A one-shot scrape (writes `./output/sundaysignal_streams.json`)
- A persistent server on port **8765** that serves the JSON

To re-scrape later (e.g. closer to game time):

```bash
docker compose run --rm crawler
```

For continuous refresh during a game day you can temporarily change the crawler command to a loop (see below).

### 2. Find the IP of the machine running Docker

```bash
# Linux / macOS
hostname -I | awk '{print $1}'
# or
ip addr show | grep 'inet '
```

Example: `192.168.1.42`

### 3. Let the Roku channel find SundaySignal

The channel discovers the server automatically. It reads the Roku's local IP,
scans the same `/24` network for port **8765**, verifies `/api/health`, and
saves the working server address for later launches. Keep the Roku and Docker
host on the same LAN without guest/client isolation.

### 4. Package the channel

```bash
cd roku-channel
zip -r ../sundaysignal-roku.zip . -x "*.DS_Store"
cd ..
```

The zip must contain `manifest` at the **root** of the archive (not inside an extra folder).

### 5. Sideload onto your Roku

1. On the Roku remote press: **Home × 3**, **Up × 2**, **Right**, **Left**, **Right**, **Left**, **Right**
2. Accept the developer agreement and set a password. Note the IP shown.
3. On your computer open a browser to `http://<roku-ip>`
4. Login with username `rokudev` + the password you set.
5. Upload `sundaysignal-roku.zip` and click **Install**.

The channel appears under the developer row.

### 6. Use it

- Left list = games  
- Right list = streams for the selected game  
- OK on a stream = attempt playback  
- **Back** stops video / returns to list  
- **Options / * button** refreshes the data from the server  

**Reality check:** Many of the current links are intermediate web pages, not pure HLS. Roku’s video player will fail on those. When that happens the channel tells you – just AirPlay the same link from your phone/Mac (which works well on your Roku). As real `.m3u8` links appear closer to kickoff, more will play natively.

## Continuous scrape during the season

Edit `docker-compose.yml` crawler service and set:

```yaml
command: ["bash", "-c", "while true; do python -u sundaysignal_scraper.py; sleep 900; done"]
restart: unless-stopped
```

(900 s = 15 min). Then `docker compose up -d --build`.

## AirPlay fallback (recommended for most links)

1. Roku → Settings → Apple AirPlay and HomeKit → On  
2. On iPhone/Mac open the stream URL → Control Center → Screen Mirroring → your Roku  

This bypasses the need for clean media URLs entirely.

## Files

| Path | Purpose |
|------|---------|
| `sundaysignal_scraper.py` | Source adapter and crawler |
| `serve.py` | Tiny HTTP server for the JSON |
| `docker-compose.yml` | Crawler + server |
| `roku-channel/` | Sideloadable private channel source |
| `output/sundaysignal_streams.json` | Latest scrape results |

## Notes

- Personal use only. The upstream streams are third-party / unofficial.
- The Roku channel is private (Developer Mode). It cannot be submitted to the Channel Store.
- If the machine IP changes, press the Roku remote's **Options / \*** button to run discovery again.


## IPTV / M3U

After `docker compose up --build -d`:

```
http://<host>:8765/playlist.m3u
```

Paste that URL into VLC, TiviMate, IPTV Smarters, etc.

Each entry uses the local `/proxy` so CDN Referer is applied (direct fingersoon links redirect to Yahoo without it).

If your host IP changes, update `PUBLIC_BASE_URL` in `docker-compose.yml` and recreate the server container.
