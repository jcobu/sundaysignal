# Changelog

All notable changes to this project are documented here. Versions follow
[Semantic Versioning](https://semver.org/) (`MAJOR.MINOR.PATCH`). The
running app's exact version and build time are shown in the web UI header,
`/api/health`, and the crawler's startup log line — check those to confirm
which build you're actually running.

## [Unreleased]

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
