# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`). The
running app's exact version and build time are shown in the web UI header,
`/api/health`, and the crawler's startup log line — check those to confirm
which build you're actually running.

## [Unreleased]

## [0.6.1] - 2026-09-14

### Changed
- **Rescrape button shows a spinner instead of a "Running…" label** while
  a rescrape is in flight (user-provided dual-ring CSS loader). Same
  disable/re-enable lifecycle as before, just swapping the text for an
  icon.

## [0.6.0] - 2026-09-14

### Added
- **NFL Network gets a real logo**, the same way RedZone did — it isn't a
  team either, so it fell through to the generic non-matchup styling with
  no logo. Picked up everywhere a game's logo is used (sidebar card,
  M3U/EPG `tvg-logo`).

### Changed
- **Dropped the "/ Live" suffix on RedZone and NFL Network's titles.**
  Their real listing pairs the channel name with a literal "Live"
  placeholder in the same slug shape real games use (e.g. "NFL RedZone
  vs Live"), which rendered as "NFL RedZone / Live" — now collapses to
  just "NFL RedZone". A new `always_live` flag (either side of the "vs"
  being literally "Live") shows a proper **● LIVE** pill next to HD
  instead, since these channels don't have a scheduled kickoff/final
  state to derive one from otherwise.
- **New HD icon.** The old one tried to hand-draw "H"/"D" letterforms
  into a 12px badge and read as a muddy smudge at that size; replaced
  with a simple monitor/display glyph that's actually legible small.

## [0.5.9] - 2026-09-14

### Fixed
- **RedZone's logo rendered tiny.** Every card's `.logos img` shared one
  square box sized for round/square team badges (44-56px), but a
  non-matchup logo like RedZone's is a wide horizontal wordmark — fit
  into that square with `object-fit: contain`, it shrank down to a
  sliver. A non-matchup card has no "vs" divider or second badge sharing
  the row, so there was room to spare; it now gets its own box (up to
  220px wide, 52-72px tall) instead of the team-badge square.

## [0.5.8] - 2026-09-14

### Changed
- **Swapped the logo/favicon again** — replaced the v0.5.6 SVG with a
  user-supplied PNG (a football in an orange rounded-square frame),
  downsized from the original 1254x1254/1.4MB upload to 512x512
  (~280KB) since it's fetched on every page load. Same three spots as
  before: header logo, browser favicon, README badge.

## [0.5.7] - 2026-09-14

### Changed
- Trimmed `README.md` down to the intro and Quick Start — removed
  Features, Configuration, Adding a source, Running tests, Versioning,
  Fire TV / Android TV, Local endpoints, and Useful commands.

## [0.5.6] - 2026-09-14

### Changed
- **New logo and favicon** — replaced the JPEG icon with an SVG (user-
  provided design), used for both the header logo and the browser
  favicon. Scales cleanly at any size instead of the JPEG's fixed
  512x512 raster, and the old file is removed since nothing references
  it anymore.

Note: the Fire TV app's own launcher icon (a separate Android drawable
resource) is unchanged — say if you'd like that updated too.

## [0.5.5] - 2026-09-14

### Added
- **NFL RedZone gets a real logo instead of a blank space.** It isn't a
  team, so `team_abbr()` never matched it and it fell into the generic
  non-matchup styling with nothing to show. Any non-matchup title
  containing "redzone" (case-insensitive, with or without a space) now
  shows the NFL RedZone logo — picked up everywhere a game's logo is
  used: the sidebar card, the M3U/EPG `tvg-logo`, all of it.

## [0.5.4] - 2026-09-14

### Fixed
- **Settings panel was cut off on mobile.** It was positioned relative to
  `.header-actions` — the box wrapping just the Reload/Settings buttons —
  instead of the header as a whole. On a narrow screen the header wraps
  to two rows and that button box sits near the left edge rather than
  the true right edge of the screen, so anchoring the panel's right side
  to it pushed most of the panel off-screen to the left instead of
  hanging it under the Settings button. It's now anchored to `<header>`
  itself (already a positioning context via `position: sticky`), which
  spans the full width regardless of how the buttons wrap. Also added a
  height cap with scrolling so a long panel can't run off the bottom of
  a short viewport either. Verified at 320–1024px wide with no overflow
  at any width.

## [0.5.3] - 2026-09-13

### Changed
- **Playback now sits a fixed ~20 seconds behind live**, instead of a
  segment-count-based target that varies with however long this mirror's
  segments happen to be. `liveSyncDuration`/`liveMaxLatencyDuration`
  (seconds) replace `liveSyncDurationCount`/`liveMaxLatencyDurationCount`
  (segment counts) for hls.js; Safari's native player, which has no such
  setting, gets the same cushion via a one-time seek right after
  metadata loads. The **● LIVE** button now catches up to that same
  20s-behind position rather than the bleeding edge, so using it doesn't
  trade the buffer margin away again.

## [0.5.2] - 2026-09-13

### Changed
- **Player tuned for these mirrors instead of a real live broadcast.**
  Playback was configured for low-latency live (`lowLatencyMode`, a
  3-segment sync window, snapping to the live edge the moment metadata
  loaded), which is right for a stable origin but leaves almost nothing
  buffered ahead on a scraped third-party CDN — one network hiccup and
  playback catches up to the buffer head and stalls. Now: low-latency
  mode is off, the live-sync window is wider (6/12 segments instead of
  3/6), `maxBufferLength`/`backBufferLength` are set explicitly instead
  of the low-latency-mode defaults, and playback no longer force-seeks to
  the live edge on load — it starts from wherever hls.js naturally lands
  (already close to live) and keeps its buffer cushion. The **● LIVE**
  button still jumps to the true edge on demand.
- Segment/playlist fetch retries raised (`fragLoadingMaxRetry`,
  `manifestLoadingMaxRetry`, `levelLoadingMaxRetry`) so a mirror's
  transient blip is retried instead of immediately counted as a fatal
  error that jumps to the next source.

## [0.5.1] - 2026-09-13

### Fixed
- **Non-matchup listings no longer show "vs".** RedZone, NFL Network and
  similar whole-slate channels still arrive through the same
  `<a>-vs-<b>` URL shape the source sites use for real games, so the word
  survived the title split even though neither side is an actual team.
  Games are now flagged `is_matchup` (both sides resolve to a known NFL
  team); anything else drops the "vs" divider and second logo entirely,
  and collapses a duplicated label (`"nfl redzone vs nfl redzone"`) down
  to one clean name instead of showing it twice.
- **A finished game with no stream no longer says "NO STREAM YET."** That
  pill and the "Waiting for a stream" hint were showing on games that had
  already ended, which reads as if one might still show up. A game in the
  `FINAL` state with nothing resolved now just shows FINAL, with a hint
  that a stream was never found for it.

## [0.5.0] - 2026-09-13

### Added
- **`telegram` source adapter**, on by default alongside `nflbite`. It
  reads a public channel's web preview (no API key or account) and
  follows the game links it posts.
  - Because it uses whatever host the channel is currently posting, it
    keeps working when the main site rotates domains — the usual way
    this breaks. The sample channel posts links on `sportslinks.is`,
    a different domain from `nflbite.is` serving the same game ids.
  - Those pages run the same software, so stream extraction is now
    shared between both adapters (`sources/linkk_table.py`); a markup
    change there gets fixed once.
  - Anchored on the link shape (`/<team>-vs-<team>/<id>`) rather than
    post wording, so emoji, captions and kickoff phrasing can change
    freely. Kickoff text is ignored outright — the schedule owns timing.
  - Channel selectable via `SUNDAYSIGNAL_TELEGRAM_CHANNEL`.
- A source may now attach a `referer` to a discovered game, for when it
  links to pages on a different host than its own (as a channel does).

### Changed
- When several sources list the same fixture, its page is fetched once
  and the game listed once, with streams pooled onto one entry. A source
  whose page yields nothing doesn't count as covering a fixture, so the
  others still get their turn — the redundancy survives the optimization.

## [0.4.1] - 2026-09-13

### Changed
- The label above the player now reads "WATCHING / &lt;game&gt; · Source N"
  for whatever is actually playing, instead of the static "WATCHING /
  SUNDAY SIGNAL". It's hidden entirely until a stream is selected, and
  hidden again when a game with no stream is selected.
- Streams whose provider labels them "unknown" no longer have that
  printed back at you in the player label or the now-playing line.
- Corrected the now-playing hint, which still pointed at a "Rescrape
  now" button that moved into ⚙ Settings.

## [0.4.0] - 2026-09-13

### Changed
- **The schedule now decides which games exist.** Previously a game
  existed only if a mirror site listed it and the page parsed, so a bad
  scrape emptied the sidebar; ESPN was used merely to decorate whatever
  was found. That relationship is inverted: the scoreboard produces the
  game list, and scraping only supplies streams to attach to it. A site
  failing now costs a game its streams, not its place in the lineup.
  - Scraped games are matched to their scheduled game by team names; a
    scrape matching nothing on the schedule is still kept as its own
    entry rather than dropped.
  - Multiple sources covering the same game pool their streams onto one
    entry, tracked in `stream_sources`.
  - Falls back to the old scrape-derived list when the schedule is
    unreachable, so a schedule outage degrades instead of breaking.
  - Disable with `SUNDAYSIGNAL_SCHEDULE_SOURCE=none`.
- **Games stay listed until well after they end** —
  `SUNDAYSIGNAL_KEEP_FINAL_HOURS` (12, measured from kickoff). A live
  game is never dropped, whatever the clock says, so a scoreboard stuck
  on "in" can't pull a game out from under someone watching it.
- **The sidebar lists every scheduled game**, including ones with no
  stream yet, marked "NO STREAM YET" and dimmed. It used to filter those
  out entirely, which is what made the list look empty. Selecting one
  explains the situation rather than doing nothing.
- The catalog write is now skipped only on a total washout (no games at
  all). Since merging is a union, writing can't cost streams, and going
  ahead keeps kickoff times and live/final status current on runs that
  resolve nothing.
- Game merging matches on schedule id and title as well as uid, so a
  fixture's streams survive it being re-keyed.

### Note
Games are now keyed by schedule id, so IPTV `tvg-id` values change
once. Re-add the playlist in TiviMate/VLC if your client keys its
channel history off them.

## [0.3.0] - 2026-09-13

### Changed
- **A scrape can no longer shrink a game's stream list.** The previous
  guard was all-or-nothing: it only protected a game whose new stream
  list came back completely empty, so a run that resolved 1 of 12
  streams still replaced the list and threw away 11 working links.
  Merging is now a per-game union — freshly resolved streams lead, and
  previously-working ones are carried behind them (deduplicated, marked
  stale). A scrape can only ever add.
  - Carried links expire after `SUNDAYSIGNAL_KEEP_STALE_HOURS` (6), since
    an old HLS URL eventually stops working — **except** while a game is
    live and nothing fresh resolved, where a link that might still work
    beats an empty list.
  - `SUNDAYSIGNAL_MAX_STREAMS_PER_GAME` (12) caps how many accumulate.
  - `resolved_count` now means "resolved on this run" and `stream_count`
    the total available including carried links.
- **Rescrape moved out of the main header into ⚙ Settings**, under a new
  ADMIN section — it's an occasional maintenance action, not a primary
  control, and it no longer sits one stray click away while you're
  watching.

### Added
- `SUNDAYSIGNAL_ADMIN_TOKEN` optionally restricts `/api/rescrape`, so not
  everyone on the LAN (or a TV app) can kick off a crawl. Sent as an
  `X-SundaySignal-Token` header or `?token=`, compared in constant time.
  When set, the Settings panel shows a token field (stored per browser);
  when unset, behavior is unchanged.

### Fixed
- `hidden` had no effect on elements whose class set an explicit
  `display`, because a class rule outranks the user-agent `[hidden]`
  rule — the admin token row showed even when no token was configured.

## [0.2.1] - 2026-09-13

### Fixed
- **"Rescrape now" could wipe a working catalog.** The web UI's rescrape
  wrote `crawl()` output straight to disk, bypassing the guard that keeps
  the last good list when a scrape resolves nothing — along with
  merge-keep-previous, ESPN enrichment, dead-host persistence and failure
  tracking. A rescrape during a bad window replaced a full list of games
  with an empty one. Both the interval crawler and the button now go
  through a single shared `run_cycle()`, so no write path can skip the
  guard again.
- **A source site could get cached as dead.** One transient DNS blip or
  timeout on the source itself put it in the dead-host cache for the full
  TTL (24h by default), after which every crawl found zero games without
  ever attempting a request. Source hosts are now never cached as dead,
  and registering one clears any stale entry left by an earlier run — so
  an affected install heals itself on the next crawl.

### Changed
- A crawler that runs but finds nothing is no longer indistinguishable
  from a stopped one. The catalog's timestamp only moves on a successful
  write, so the sidecar status is now written on *every* cycle and
  surfaced via `last_attempt` in `/api/health` and `/api/streams`. The UI
  status line shows "last attempt … found no streams (showing previous
  list)", and a rescrape that resolves nothing says so instead of
  appearing to do nothing.
- Default crawl interval raised from 10 to 30 minutes
  (`CRAWL_INTERVAL_SECONDS`); drop it back down on game day.
- The crawler now honors `OUTPUT_DIR`, so it and the web app can't
  disagree about where the catalog lives.

## [0.2.0] - 2026-09-13

### Added
- **Pluggable source adapters** (`sources/`). Site-specific knowledge —
  listing games, pulling wrapper links, mirror preference — now lives
  behind a small interface, with nflbite as the first adapter. The core
  keeps the generic parts (fetching, dead-host cache, nested-iframe
  resolve chain, concurrency, output), so adding a second site is a new
  module rather than a rewrite. Select adapters with
  `SUNDAYSIGNAL_SOURCES`; unknown names are skipped with a warning
  instead of killing the crawl.
- **XMLTV guide at `/epg.xml`**, with channel ids matching the playlist
  (alternates included) and programme entries built from ESPN kickoff,
  status and venue data. The M3U now advertises it via `url-tvg`, so
  TiviMate/VLC can pick up the guide automatically.
- **Docker healthchecks** for both services: the server answers
  `/api/health`, and the crawler is judged on whether a cycle completed
  recently (tracked via `crawl_state.json`, which updates even when a
  cycle deliberately keeps the previous catalog).
- **Failure notifications** — after `SUNDAYSIGNAL_NOTIFY_AFTER`
  consecutive crawls that resolve nothing, post to
  `SUNDAYSIGNAL_NOTIFY_URL` (ntfy, Discord webhook, or any JSON
  endpoint), plus a recovery note when streams come back. Off by default.
- **CI** (`.github/workflows/tests.yml`) running the test suite and
  building the Docker image on every push.
- More tests: source-adapter extraction/ranking, registry fallback
  behavior, game/source tagging, and uid-based stale merging.

### Changed
- **Web UI header decluttered** — the IPTV M3U and JSON buttons moved
  into a **⚙ Settings** panel alongside the new EPG and health links.
  Each feed shows its full absolute URL with a Copy button (with a
  fallback for plain-http LAN origins, where the async clipboard API is
  unavailable), so URLs can be pasted straight into VLC or TiviMate.
- **Structured logging** replaces `print()` throughout, with levels via
  `SUNDAYSIGNAL_LOG_LEVEL`. Dead-mirror noise and per-hop resolve detail
  are DEBUG, so `docker compose logs` shows real problems by default;
  `SUNDAYSIGNAL_DEBUG_RESOLVE=1` still turns the resolver chatter back on
  without making everything verbose.
- Games now carry `source` and a namespaced `uid`, so two sources listing
  the same game id can't collide when merging. `id` is unchanged, so
  existing playlists and tvg-ids keep working.

## [0.1.0] - 2026-09-13

First versioned release — bundles everything shipped before build tracking
existed.

### Added
- Build/version tracking itself: `VERSION` file, an auto-captured build
  timestamp, surfaced in the web UI header, `/api/health`, and the
  crawler's startup log.
- A "Sources" picker in the web UI for games with multiple resolved
  streams, with automatic failover to the next source on a fatal
  playback error.
- One IPTV M3U row per resolved stream per game (labeled `Source 1/2/3…`)
  instead of only the first, so IPTV clients have fallbacks too.
- Regression test suite (`tests/`) covering the nested-iframe resolver
  against real captured HTML, dead-host persistence, and the per-game
  resolve cap under the shared cross-game thread pool.
- Environment-configurable tuning: `SUNDAYSIGNAL_BASE_URL`,
  `SUNDAYSIGNAL_MAX_RESOLVE_PER_GAME`, `SUNDAYSIGNAL_RESOLVE_WORKERS`,
  `SUNDAYSIGNAL_MAX_RESOLVE_HOPS`, `SUNDAYSIGNAL_RESOLVE_TIMEOUT`,
  `SUNDAYSIGNAL_PROXIES`, `SUNDAYSIGNAL_DEAD_HOST_TTL_HOURS`.

### Changed
- Stream resolution now follows real nested `<iframe>` chains (including
  protocol-relative `//host/path` srcs) up to several hops deep, instead
  of a single substring-matched embed hop — the source mirrors moved past
  the old iframe.st `_dd/_dk/_dri` decrypt scheme this was originally
  written against.
- The crawler no longer caps itself to effectively one working provider
  per game; it gathers up to `SUNDAYSIGNAL_MAX_RESOLVE_PER_GAME` distinct
  resolved streams from whichever providers actually work.
- `crawl()` fetches all game pages first, then resolves every game's
  candidate streams in one shared thread pool — round-robin interleaved
  across games and submitted in waves — instead of a fresh per-game pool
  that had to fully drain before the next game started.
- Dead-host tracking persists to disk (`dead_hosts.json`, TTL-based)
  instead of resetting every crawl cycle (each cycle runs as a new
  process).
- `webapp.py`'s `/proxy` endpoint replaced a stale hardcoded CDN-domain
  allowlist with a check on the resolved IP (rejects private/loopback/
  link-local/metadata targets, allows any other public host) — both more
  correct for new providers and a genuine SSRF hardening.

### Fixed
- `entrypoint-crawler.sh` CRLF line endings causing a baffling
  `exec: no such file or directory` on some Docker builds.
  `.gitattributes` now enforces LF, and the Dockerfile strips `\r`
  defensively at build time regardless of how the file arrives.
