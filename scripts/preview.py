#!/usr/bin/env python3
"""Serve dashboard assets while reading JSON observations from another directory."""
import argparse
import http.server
import os
import urllib.parse


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    assets_dir = ""
    data_dir = ""

    def translate_path(self, path):
        requested = urllib.parse.urlsplit(path).path.lstrip("/") or "index.html"
        if requested in {"state.json", "history.json", "uptime_daily.json"}:
            return os.path.join(self.data_dir, requested)
        safe = os.path.normpath(requested)
        if safe.startswith(".."):
            return os.path.join(self.assets_dir, "404")
        return os.path.join(self.assets_dir, safe)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    parser = argparse.ArgumentParser(description="Preview the static uptime dashboard")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--assets-dir", default=os.path.join(os.path.dirname(__file__), "..", "docs"))
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    DashboardHandler.assets_dir = os.path.abspath(args.assets_dir)
    DashboardHandler.data_dir = os.path.abspath(args.data_dir)
    server = http.server.ThreadingHTTPServer((args.bind, args.port), DashboardHandler)
    print(f"Dashboard available at http://{args.bind}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
