#!/bin/sh
# Continuous crawler: scrape, write JSON, sleep, repeat.
INTERVAL="${CRAWL_INTERVAL_SECONDS:-900}"
echo "[crawler] Starting. Interval=${INTERVAL}s"
while true; do
  echo "[crawler] $(date -u +%Y-%m-%dT%H:%M:%SZ) Running scrape..."
  python -u /app/sundaysignal_scraper.py || echo "[crawler] scrape failed (will retry)"
  echo "[crawler] Sleeping ${INTERVAL}s..."
  sleep "$INTERVAL"
done
