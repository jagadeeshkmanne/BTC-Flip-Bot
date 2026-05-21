#!/usr/bin/env python3
"""server.py — standalone dashboard server for the Bybit divflip bot.

Completely independent of the main project's server.py:
  - separate process, separate port (default 8889 vs the main bot's 8888)
  - serves only this folder's dashboard.html + this bot's data/ JSON

Routes:
  GET /                 -> dashboard.html
  GET /dashboard.html   -> dashboard.html
  GET /api/status       -> data/status.json   (live position, P&L, signal)
  GET /api/state        -> data/state.json    (trade log, stats)
  GET /api/health       -> {ok, stale_seconds} — is the bot ticking?
"""
from __future__ import annotations
import json, os
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BOT_DIR, "data")
PORT = int(os.environ.get("BYBIT_DASH_PORT", "8889"))


class Handler(BaseHTTPRequestHandler):
    timeout = 15  # drop a stuck client instead of hanging its thread

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _json_file(self, path):
        if not os.path.exists(path):
            return self._send(200, "{}", "application/json")
        try:
            with open(path, "rb") as f:
                self._send(200, f.read(), "application/json")
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}), "application/json")

    def _health(self):
        """Report whether the bot is still ticking (status.json freshness)."""
        path = os.path.join(DATA_DIR, "status.json")
        out = {"ok": False, "stale_seconds": None}
        if os.path.exists(path):
            try:
                with open(path) as f:
                    st = json.load(f)
                ts = st.get("updated_at")
                if ts:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    age = (datetime.now(timezone.utc) - dt).total_seconds()
                    out = {"ok": age < 180, "stale_seconds": round(age)}
            except Exception as e:
                out["error"] = str(e)
        self._send(200, json.dumps(out), "application/json")

    def do_GET(self):
        route = self.path.split("?")[0].rstrip("/") or "/"
        if route in ("/", "/dashboard.html", "/index.html"):
            p = os.path.join(BOT_DIR, "dashboard.html")
            if os.path.exists(p):
                with open(p, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            else:
                self._send(404, "dashboard.html not found", "text/plain")
        elif route == "/api/status":
            self._json_file(os.path.join(DATA_DIR, "status.json"))
        elif route == "/api/state":
            self._json_file(os.path.join(DATA_DIR, "state.json"))
        elif route == "/api/health":
            self._health()
        else:
            self._send(404, "not found", "text/plain")

    def log_message(self, *args):
        pass  # keep the journal quiet


if __name__ == "__main__":
    print(f"Bybit divflip dashboard — http://0.0.0.0:{PORT}")
    # ThreadingHTTPServer — each request gets its own thread, so one slow or
    # stuck client can't block the whole dashboard (the single-threaded
    # HTTPServer would hang the listen queue and stop responding entirely).
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
