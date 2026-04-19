#!/usr/bin/env python3
"""HTTP server with auth, no caching, API for settings & bot control."""
import http.server
import json
import os
import sys
import subprocess
import signal
import hashlib
import secrets
import base64
from urllib.parse import parse_qs, urlparse

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BOT_DIR)
ENV_PATH = os.path.join(BOT_DIR, ".env")


# ═══════════════════════════════════════════════════════════════
# AUTH — password stored as hash in .env
# ═══════════════════════════════════════════════════════════════

def hash_password(password):
    """SHA-256 hash a password."""
    return hashlib.sha256(password.encode()).hexdigest()


def get_dashboard_password():
    """Get the dashboard password hash from .env."""
    env = load_env()
    return env.get("DASHBOARD_PASS_HASH", "")


def set_dashboard_password(password):
    """Save dashboard password hash to .env."""
    env = load_env()
    env["DASHBOARD_PASS_HASH"] = hash_password(password)
    save_env(env)


def check_auth(handler):
    """Check HTTP Basic Auth. Returns True if authenticated."""
    pw_hash = get_dashboard_password()

    # If no password set yet, allow access (first-time setup)
    if not pw_hash:
        return True

    auth_header = handler.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False

    try:
        decoded = base64.b64decode(auth_header[6:]).decode()
        username, password = decoded.split(":", 1)
        return hash_password(password) == pw_hash
    except Exception:
        return False


def send_auth_required(handler):
    """Send 401 response asking for login."""
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="BTC Flip Bot"')
    handler.send_header("Content-Type", "text/html")
    handler.end_headers()
    handler.wfile.write(b"""
    <html><body style="background:#0d1117;color:#e6edf3;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;">
    <div style="text-align:center;">
        <h2>BTC Flip Bot</h2>
        <p style="color:#8b949e;">Login required. Username: <b>admin</b></p>
    </div>
    </body></html>
    """)


# ═══════════════════════════════════════════════════════════════
# ENV FILE
# ═══════════════════════════════════════════════════════════════

def load_env():
    """Load .env file as dict."""
    env = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                env[key.strip()] = val.strip()
    return env


def save_env(env):
    """Save dict to .env file."""
    with open(ENV_PATH, "w") as f:
        f.write("# ─── API Keys (NEVER push this file to GitHub) ───\n\n")
        f.write("# Binance Futures Testnet\n")
        f.write(f"TESTNET_API_KEY={env.get('TESTNET_API_KEY', '')}\n")
        f.write(f"TESTNET_API_SECRET={env.get('TESTNET_API_SECRET', '')}\n\n")
        f.write("# Binance Futures Production\n")
        f.write(f"PRODUCTION_API_KEY={env.get('PRODUCTION_API_KEY', '')}\n")
        f.write(f"PRODUCTION_API_SECRET={env.get('PRODUCTION_API_SECRET', '')}\n\n")
        f.write("# Email notifications\n")
        f.write(f"BOT_EMAIL={env.get('BOT_EMAIL', '')}\n")
        f.write(f"BOT_EMAIL_PASS={env.get('BOT_EMAIL_PASS', '')}\n")
        f.write(f"BOT_EMAIL_TO={env.get('BOT_EMAIL_TO', '')}\n\n")
        f.write("# Dashboard login (SHA-256 hash)\n")
        f.write(f"DASHBOARD_PASS_HASH={env.get('DASHBOARD_PASS_HASH', '')}\n")


# ═══════════════════════════════════════════════════════════════
# BOT CONTROL
# ═══════════════════════════════════════════════════════════════

def get_bot_status(env):
    """Check if a bot environment is running and enabled."""
    data_dir = os.path.join(BOT_DIR, "data", env)
    os.makedirs(data_dir, exist_ok=True)
    disabled_flag = os.path.join(data_dir, ".disabled")
    state_file = os.path.join(data_dir, "state.json")

    enabled = not os.path.exists(disabled_flag)

    running = False
    pids = []
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"bot.py --env {env}"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            pids = result.stdout.strip().split("\n")
            running = True
    except Exception:
        pass

    last_run = None
    active_positions = 0
    total_trades = 0
    total_pnl = 0.0
    if os.path.exists(state_file):
        try:
            with open(state_file) as f:
                state = json.load(f)
            last_run = state.get("last_run")
            active_positions = len(state.get("positions", {}))
            stats = state.get("stats", {})
            total_trades = stats.get("total_trades", 0)
            total_pnl = stats.get("total_profit_usd", 0.0)
        except Exception:
            pass

    return {
        "env": env,
        "enabled": enabled,
        "running": running,
        "pids": pids,
        "last_run": last_run,
        "active_positions": active_positions,
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
    }


def set_bot_enabled(env, enabled):
    """Enable or disable a bot environment."""
    data_dir = os.path.join(BOT_DIR, "data", env)
    os.makedirs(data_dir, exist_ok=True)
    disabled_flag = os.path.join(data_dir, ".disabled")

    if enabled:
        if os.path.exists(disabled_flag):
            os.remove(disabled_flag)
    else:
        with open(disabled_flag, "w") as f:
            f.write("disabled")
        try:
            subprocess.run(["pkill", "-f", f"bot.py --env {env}"], timeout=5)
        except Exception:
            pass

    return {"ok": True, "enabled": enabled}


def run_bot_now(env):
    """Trigger a single bot run in the background."""
    data_dir = os.path.join(BOT_DIR, "data", env)
    os.makedirs(data_dir, exist_ok=True)
    log_file = os.path.join(data_dir, "bot.log")

    disabled_flag = os.path.join(data_dir, ".disabled")
    if os.path.exists(disabled_flag):
        return {"ok": False, "error": f"{env} is disabled. Enable it first."}

    try:
        result = subprocess.run(
            ["pgrep", "-f", f"bot.py --env {env}"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            return {"ok": False, "error": "Bot is already running"}
    except Exception:
        pass

    with open(log_file, "a") as lf:
        subprocess.Popen(
            [sys.executable, os.path.join(BOT_DIR, "bot.py"), "--env", env],
            stdout=lf, stderr=lf,
            cwd=BOT_DIR, start_new_session=True
        )

    return {"ok": True, "message": f"Bot started for {env}"}


# ═══════════════════════════════════════════════════════════════
# HTTP HANDLER
# ═══════════════════════════════════════════════════════════════

class BotHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    def _json_response(self, data, code=200):
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _require_auth(self):
        """Returns True if auth OK, sends 401 and returns False if not."""
        if check_auth(self):
            return True
        send_auth_required(self)
        return False

    def _is_public(self, path):
        """Public pages: dashboards + data files + API. No auth needed."""
        if path in ('/', '/dashboard.html', '/dashboard_grid.html', '/dashboard_bb.html'):
            return True
        if path.startswith('/data/') or path.startswith('/api/'):
            return True
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Auth status is always open
        if path == '/api/auth/status':
            pw_set = bool(get_dashboard_password())
            return self._json_response({"password_set": pw_set})

        # Redirect root to dashboard
        if path == '/':
            self.send_response(302)
            self.send_header('Location', '/dashboard_grid.html?env=testnet')
            self.end_headers()
            return

        # Dashboard + state.json are public (read-only data)
        if self._is_public(path):
            return super().do_GET()

        # ── Public V5 dashboard endpoints (no auth) ──
        if path == '/api/status':
            env = parse_qs(parsed.query).get('env', ['testnet'])[0]
            status_file = os.path.join(BOT_DIR, 'data', env, 'status.json')
            if os.path.exists(status_file):
                try:
                    with open(status_file) as f:
                        return self._json_response(json.load(f))
                except Exception:
                    return self._json_response({"error": "status read failed"})
            return self._json_response({"state": "NO_DATA", "env": env})

        if path == '/api/trades':
            env = parse_qs(parsed.query).get('env', ['testnet'])[0]
            state_file = os.path.join(BOT_DIR, 'data', env, 'state.json')
            if os.path.exists(state_file):
                try:
                    with open(state_file) as f:
                        st = json.load(f)
                    return self._json_response(st.get('trade_log', []))
                except Exception:
                    return self._json_response([])
            return self._json_response([])

        # Everything else requires auth
        if not self._require_auth():
            return

        if path == '/api/settings':
            env = load_env()
            def mask(v):
                if not v or len(v) < 8:
                    return ""
                return "•" * (len(v) - 4) + v[-4:]

            data = {
                "testnet_key": mask(env.get("TESTNET_API_KEY", "")),
                "testnet_secret": mask(env.get("TESTNET_API_SECRET", "")),
                "testnet_key_set": bool(env.get("TESTNET_API_KEY")),
                "testnet_secret_set": bool(env.get("TESTNET_API_SECRET")),
                "production_key": mask(env.get("PRODUCTION_API_KEY", "")),
                "production_secret": mask(env.get("PRODUCTION_API_SECRET", "")),
                "production_key_set": bool(env.get("PRODUCTION_API_KEY")),
                "production_secret_set": bool(env.get("PRODUCTION_API_SECRET")),
                "email": env.get("BOT_EMAIL", ""),
                "email_pass_set": bool(env.get("BOT_EMAIL_PASS")),
                "email_to": env.get("BOT_EMAIL_TO", ""),
                "dashboard_pass_set": bool(env.get("DASHBOARD_PASS_HASH")),
            }
            return self._json_response(data)

        if path == '/api/bot/status':
            testnet = get_bot_status("testnet")
            production = get_bot_status("production")
            return self._json_response({"testnet": testnet, "production": production})

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get('Content-Length', 0))
        raw = self.rfile.read(length) if length else b'{}'
        body = json.loads(raw) if raw else {}

        # Password setup — allowed without auth if no password set yet
        if path == '/api/auth/set-password':
            current_hash = get_dashboard_password()
            new_pass = body.get("password", "")

            if not new_pass or len(new_pass) < 4:
                return self._json_response({"ok": False, "error": "Password must be at least 4 characters"}, 400)

            # If password already set, require current auth
            if current_hash and not check_auth(self):
                send_auth_required(self)
                return

            set_dashboard_password(new_pass)
            return self._json_response({"ok": True, "message": "Password set. Use admin / your-password to login."})

        if not self._require_auth():
            return

        if path == '/api/settings':
            env = load_env()
            field_map = {
                "testnet_key": "TESTNET_API_KEY",
                "testnet_secret": "TESTNET_API_SECRET",
                "production_key": "PRODUCTION_API_KEY",
                "production_secret": "PRODUCTION_API_SECRET",
                "email": "BOT_EMAIL",
                "email_pass": "BOT_EMAIL_PASS",
                "email_to": "BOT_EMAIL_TO",
            }
            for ui_key, env_key in field_map.items():
                if ui_key in body and body[ui_key]:
                    env[env_key] = body[ui_key]
            save_env(env)
            return self._json_response({"ok": True})

        if path == '/api/bot/enable':
            env_name = body.get("env")
            enabled = body.get("enabled", True)
            if env_name not in ("testnet", "production"):
                return self._json_response({"ok": False, "error": "Invalid env"}, 400)
            result = set_bot_enabled(env_name, enabled)
            return self._json_response(result)

        if path == '/api/bot/run':
            env_name = body.get("env")
            if env_name not in ("testnet", "production"):
                return self._json_response({"ok": False, "error": "Invalid env"}, 400)
            result = run_bot_now(env_name)
            return self._json_response(result)

        self.send_response(404)
        self.end_headers()


print("Starting server on http://localhost:8888")
print("Dashboard:  http://localhost:8888/dashboard.html")
print("Settings:   http://localhost:8888/settings.html")
http.server.HTTPServer(('', 8888), BotHandler).serve_forever()
