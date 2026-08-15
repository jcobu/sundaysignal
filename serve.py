#!/usr/bin/env python3
"""Tiny static and JSON server for clients on the local network."""
import http.server
import socketserver
import os
import json
from pathlib import Path

PORT = int(os.environ.get("SERVE_PORT", "8765"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/output"))

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(OUTPUT_DIR), **kwargs)

    def end_headers(self):
        # Allow local web and TV clients to fetch the JSON.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, format, *args):
        print(f"[serve] {args[0]}")

if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Ensure there is an empty catalog before the first successful scrape.
    json_path = OUTPUT_DIR / "sundaysignal_streams.json"
    if not json_path.exists():
        json_path.write_text(json.dumps({
            "scraped_at": None,
            "game_count": 0,
            "games": [],
            "message": "No scrape yet – run the crawler first"
        }, indent=2))

    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Serving {OUTPUT_DIR} on http://0.0.0.0:{PORT}")
        print(f"Catalog: http://<this-machine-ip>:{PORT}/sundaysignal_streams.json")
        httpd.serve_forever()
