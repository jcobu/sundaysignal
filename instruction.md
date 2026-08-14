# SundaySignal — Project Instructions & Progress

**Last updated:** 2026-08-14  
**Location:** `artifacts/sundaysignal/`  
**Purpose:** Personal tool to discover NFL game pages from nflbite.is, resolve real HLS streams, serve a local web UI + IPTV M3U, and optionally sideload a Roku channel.

> **Legal note:** This scrapes third-party stream aggregator sites and plays unofficial streams. For personal experimentation only. Prefer legitimate broadcasts when available.

---

## 1. Goals (original request → current scope)

| Goal | Status |
|------|--------|
| Docker stack that crawls nflbite.is for active NFL games | Done |
| Resolve hidden stream buttons → real playable media URLs | Done (HLS via iframe.st decrypt) |
| Output structured JSON | Done (`output/sundaysignal_streams.json`) |
| Web GUI to list/play streams without full nflbite page/chat | Done |
| IPTV M3U for VLC / TiviMate / etc. | Done (cleaned titles + logos) |
| Sideloaded Roku private channel | Built; LAN fetch often fails (see §7) |
| Auto re-crawl on an interval | Done |
| On-demand rescrape from UI | Done |
| Team logos in UI | Done (ESPN CDN PNGs) |
| Match kickoff / live / final from schedule data | Done (ESPN unofficial scoreboard) |
| Jump-to-live-edge control in player | Done |
| No hardcoded LAN IP in Roku or play URLs | Done (LAN discovery + relative `/proxy`) |

---

## 2. Architecture

```
nflbite.is ──► source adapter (sundaysignal_scraper.py)
                    │
                    │ resolve: wrapper → iframe.st → fingersoon HLS
                    │ enrich: ESPN scoreboard (kickoff, live/final)
                    ▼
              output/sundaysignal_streams.json
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
   webapp.py (:8765)      Roku sideload (optional)
   - UI + hls.js          - fetches /api/streams
   - /proxy (Referer)     - often blocked by LAN isolation
   - /playlist.m3u
```

### Stream resolution chain

1. **Game page** on nflbite.is (e.g. `/Team-vs-Team/ID`)
2. **Wrapper** HTML (e.g. `live2.totalsporteks.st/...`) — not a stream
3. **Embed** (`iframe.st/...`) with encrypted config
4. **Decrypt** → HLS playlist (`fingersoon.st/scripts/applicationN`)
5. Playlist references **short-lived** signed segment URLs (R2)

Only step 4 (`media_url`) is treated as playable. The webapp **proxies** HLS with `Referer: https://iframe.st/` so the CDN does not redirect to Yahoo.

---

## 3. Repository layout

```
sundaysignal/
├── instruction.md          ← this file
├── README.md               ← shorter overview
├── Dockerfile
├── docker-compose.yml
├── entrypoint-crawler.sh   ← loop: scrape → sleep → repeat
├── sundaysignal_scraper.py ← crawl + HLS resolve + keep-last-good JSON
├── espn_schedule.py        ← ESPN scoreboard match times/status
├── webapp.py               ← Flask UI + API + proxy + M3U
├── serve.py                ← legacy simple static server (optional)
├── output/
│   └── sundaysignal_streams.json
├── roku-channel/           ← BrightScript / SceneGraph sources
└── sundaysignal-roku.zip   ← sideload package
```

---

## 4. Quick start (on your machine)

### Docker (preferred)

```bash
cd artifacts/sundaysignal   # or your copy of this folder
docker compose up --build -d
```

Services:

| Container | Role |
|-----------|------|
| `sundaysignal-crawler` | Scrapes every `CRAWL_INTERVAL_SECONDS` (default 600) |
| `sundaysignal-server` | Web UI + API on host port **8765** |

### Without Docker

```bash
pip install flask requests beautifulsoup4 lxml
# terminal 1 — API/UI
OUTPUT_DIR=./output WEB_PORT=8765 python3 webapp.py
# terminal 2 — one-shot scrape (or run in a loop)
OUTPUT_DIR=./output python3 sundaysignal_scraper.py
```

### Open

Replace `<host>` with your Docker host’s LAN IP:

| URL | Use |
|-----|-----|
| `http://<host>:8765/` | SundaySignal web GUI |
| `http://<host>:8765/api/streams` | JSON for tools / Roku |
| `http://<host>:8765/api/health` | Health + rescrape status |
| `http://<host>:8765/playlist.m3u` | IPTV playlist |
| `POST http://<host>:8765/api/rescrape` | Trigger scrape now |

**No hardcoded IP in stream URLs.** Browser uses relative `/proxy?url=...`. M3U builds absolute links from the request `Host` header (optional override: `PUBLIC_BASE_URL` in compose).

---

## 5. Web GUI behavior

- **SundaySignal** dark UI: channel library sidebar + player stage
- Team logos (ESPN) above match title
- Cards sorted: **Live → Upcoming → Final** (ESPN status when matched)
- **Click entire card** to play first playable HLS
- **● LIVE** button on player jumps to HLS live edge
- **Rescrape now** / **Reload list** / auto-refresh ~60s
- Labels avoid provider noise (no “Totalsportek / Platinum / english”)

---

## 6. IPTV M3U format (current)

Clean entries, one row per game:

```text
#EXTM3U
#EXTINF:-1 tvg-id="68490" tvg-name="Denver Broncos @ Atlanta Falcons" group-title="NFL · Upcoming" tvg-logo="https://a.espncdn.com/i/teamlogos/nfl/500/atl.png",Denver Broncos @ Atlanta Falcons
http://<host>:8765/proxy?url=...
```

- **Name:** `Away @ Home`
- **Groups:** `NFL · Live` / `NFL · Upcoming` / `NFL · Final`
- **Logo:** ESPN team PNG via `tvg-logo`
- Stream URL always goes through local **proxy** (Referer injection)

Use in VLC, TiviMate, IPTV Smarters, etc. Refresh the playlist after rescrape.

---

## 7. Television clients

### Fire TV app (recommended)

- Source: `firetv-app/`
- Sideloadable debug package: `SundaySignal-FireTV.apk`
- Native remote-focused Java UI using the `#112852` theme and `SundaySignalIcon.jpg`
- Uses Media3/ExoPlayer for proxied HLS playback
- Discovers and remembers SundaySignal on port 8765 across the local `/24`
- Manifest includes both `LAUNCHER` and `LEANBACK_LAUNCHER` categories

Build and installation instructions are in `firetv-app/README.md`.

### Roku private channel

- Sources: `roku-channel/`
- Package: `sundaysignal-roku.zip` (and copy under `artifacts/sundaysignal-roku.zip`)
- UI uses the **SundaySignal** SceneGraph design
- The Roku derives its LAN prefix with `roDeviceInfo.GetIPAddrs()`, discovers SundaySignal on port 8765, verifies `/api/health`, and persists the successful address.
- **Known issue:** Roku often cannot reach the PC on LAN (AP/client isolation, guest Wi‑Fi, firewall). UI shows fetch timeout / “hung”. Prefer **web GUI + AirPlay** or **M3U on Fire Stick / Android TV** until LAN path is verified (phone on *same* SSID as Roku must open the API URL).

Sideload: enable Developer Mode → upload ZIP via Development Application Installer.

---

## 8. Crawler resilience

- Prefer `live2.totalsporteks` → iframe.st path
- Skip noisy/dead hosts (`selltvonline.shop`, `sportsz.one`, …); mark DNS/timeout hosts dead for the run
- **Do not wipe UI:** if a scrape resolves **0** playable streams but previous JSON had some → **keep previous file**
- Per-game: empty new resolve can retain prior streams (`stale: true`)
- ESPN enrich: unofficial `site.web.api.espn.com` scoreboard (no API key); timezone via `tzdata` in image

---

## 9. Environment variables

| Variable | Where | Meaning |
|----------|--------|---------|
| `OUTPUT_DIR` | both | JSON directory (Docker: `/output`) |
| `CRAWL_INTERVAL_SECONDS` | crawler | Sleep between scrapes (default 600) |
| `WEB_PORT` | server | Listen port (default 8765) |
| `PUBLIC_BASE_URL` | server | Optional absolute base for M3U (else request Host) |
| `PROXY_REFERER` | server | Default `https://iframe.st/` |
| `TZ` | both | e.g. `America/Los_Angeles` |

---

## 10. JSON shape (simplified)

```json
{
  "scraped_at": "ISO-8601",
  "game_count": 8,
  "games": [
    {
      "id": "68490",
      "title": "Atlanta Falcons vs Denver Broncos",
      "away_team": "Denver Broncos",
      "home_team": "Atlanta Falcons",
      "away_logo": "https://a.espncdn.com/i/teamlogos/nfl/500/den.png",
      "home_logo": "https://a.espncdn.com/i/teamlogos/nfl/500/atl.png",
      "start_time": "2026-08-14T23:00Z",
      "kickoff_local": "Fri Aug 14 · 4:00 PM PDT",
      "status_state": "pre",
      "streams": [
        {
          "name": "...",
          "media_url": "https://fingersoon.st/scripts/applicationN",
          "play_url": "/proxy?url=..."
        }
      ]
    }
  ]
}
```

`play_url` is added by the API for browsers; Roku/proxy should use `media_url` through `/proxy`.

---

## 11. Progress log (high level)

1. Reverse-engineered nflbite + totalsporteks → iframe.st → HLS  
2. Docker crawler + JSON output  
3. Flask web GUI + HLS.js + proxy Referer fix  
4. Auto-crawl loop; rescrape endpoint; preserve last good JSON  
5. Team logos; card click-to-play; SundaySignal styling  
6. ESPN schedule (kickoff / live / final); live-edge button  
7. Clean IPTV M3U (match titles, groups, tvg-logo)  
8. Roku channel iterations (compile fixes, async fetch, SundaySignal UI) — LAN reachability still the blocker  

---

## 12. Known limitations / next ideas

- **Stream expiry:** HLS playlists/segments die; rescrape or wait for crawler cycle  
- **Embed farms die often:** DNS failures on random hosts are expected; totalsporteks path is preferred  
- **ESPN API** is unofficial and can change or rate-limit  
- **Roku ↔ PC:** fix AP isolation / use same subnet; or abandon Roku for M3U on Android TV  
- **Possible next steps:**  
  - Match-only filter (hide Final by default in UI)  
  - Multi-quality stream picker if more than one HLS resolves  
  - HTTPS via reverse proxy if clients require it  
  - Package as a single `docker compose` one-liner for friends  

---

## 13. Commands cheat sheet

```bash
# Start
docker compose up --build -d

# Logs
docker compose logs -f crawler
docker compose logs -f server

# Force scrape
curl -X POST http://127.0.0.1:8765/api/rescrape

# Test playlist
curl -s http://127.0.0.1:8765/playlist.m3u | head

# Stop
docker compose down
```

---

## 14. Contact context (project decisions)

- Personal use, one Roku, comfortable with sideloading  
- Prefer clean playback (no full webpage/chat)  
- IPTV M3U + web GUI are the reliable paths; Roku secondary until LAN works  
- Roku server discovery assumes a typical home `/24` LAN and includes a retry on the **Options / \*** button.
