#!/usr/bin/env python3
"""Container healthcheck. Usage: healthcheck.py [server|crawler]

server  — the web app answers /api/health.
crawler — a crawl cycle finished recently. Freshness is measured from
          crawl_state.json rather than the stream catalog, because a cycle
          that resolves nothing deliberately leaves the catalog untouched
          (keeping the last good data) while still being a live cycle.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request

MARKER = "crawl_state.json"


def check_server() -> int:
    port = os.environ.get("WEB_PORT", os.environ.get("SERVE_PORT", "8765"))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5) as r:
            if r.status != 200:
                print(f"unhealthy: /api/health returned {r.status}")
                return 1
    except Exception as e:
        print(f"unhealthy: {e}")
        return 1
    return 0


def check_crawler() -> int:
    path = os.path.join(os.environ.get("OUTPUT_DIR", "/output"), MARKER)
    interval = float(os.environ.get("CRAWL_INTERVAL_SECONDS", "900"))
    # Tolerate two missed cycles plus slack before calling it stuck.
    max_age = interval * 2 + 300
    if not os.path.isfile(path):
        print(f"unhealthy: {path} missing (no crawl cycle has completed yet)")
        return 1
    age = time.time() - os.path.getmtime(path)
    if age > max_age:
        print(f"unhealthy: last crawl cycle was {age:.0f}s ago (max {max_age:.0f}s)")
        return 1
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "server"
    if mode == "server":
        return check_server()
    if mode == "crawler":
        return check_crawler()
    print(f"unknown healthcheck mode: {mode}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
