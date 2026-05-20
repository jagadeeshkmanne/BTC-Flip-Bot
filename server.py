#!/usr/bin/env python3
"""HTTP server with auth, no caching, API for settings & bot control."""
import http.server
import json
import os
import sys
import subprocess
import signal
import hashlib
import hmac
import secrets
import base64
import time
import urllib.request
import urllib.parse
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


def _query_binance_position(env_name="testnet"):
    """Hit Binance's account + open-orders + premiumIndex endpoints and return
    a single JSON blob the dashboard can render directly. No bot-state caching
    in the path — this is the real-time truth.

    Cached for 1 second to absorb dashboard polling without hammering the API.
    """
    cache = getattr(_query_binance_position, "_cache", None)
    if cache and time.time() - cache["t"] < 1.0:
        return cache["v"]

    env = load_env()
    if env_name == "testnet":
        key = env.get("TESTNET_API_KEY", ""); sec = env.get("TESTNET_API_SECRET", "")
        base = "https://testnet.binancefuture.com"
    else:
        key = env.get("PRODUCTION_API_KEY", ""); sec = env.get("PRODUCTION_API_SECRET", "")
        base = "https://fapi.binance.com"
    if not key or not sec:
        return {"error": f"no api keys for {env_name}"}

    def _signed_get(path, params=None):
        params = dict(params or {})
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        q = urllib.parse.urlencode(params)
        sig = hmac.new(sec.encode(), q.encode(), hashlib.sha256).hexdigest()
        url = f"{base}{path}?{q}&signature={sig}"
        req = urllib.request.Request(url, headers={"X-MBX-APIKEY": key})
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def _public_get(path, params=None):
        q = urllib.parse.urlencode(params or {})
        url = f"{base}{path}" + (f"?{q}" if q else "")
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.loads(r.read())

    try:
        acc = _signed_get("/fapi/v2/account")
        oo = _signed_get("/fapi/v1/openOrders", {"symbol": "BTCUSDT"})
        prem = _public_get("/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"})
        tk = _public_get("/fapi/v1/ticker/price", {"symbol": "BTCUSDT"})
    except Exception as e:
        return {"error": str(e)}

    pos = None
    for p in acc.get("positions", []):
        if p["symbol"] == "BTCUSDT" and float(p.get("positionAmt", 0)) != 0:
            amt = float(p["positionAmt"])
            entry = float(p["entryPrice"])
            upnl = float(p["unrealizedProfit"])
            lev = int(float(p.get("leverage", 0)))
            mark_implied = entry - upnl/amt if amt != 0 else 0
            pos = {
                "side": "SHORT" if amt < 0 else "LONG",
                "qty": abs(amt),
                "entry": entry,
                "uPnL": upnl,
                "leverage": lev,
                "marginType": p.get("marginType", ""),
                "isolatedWallet": float(p.get("isolatedWallet", 0)),
                "notional": abs(amt) * entry,
                "markImplied": mark_implied,
            }
            break

    exit_orders = []
    for o in oo:
        exit_orders.append({
            "type": o.get("type"), "side": o.get("side"),
            "qty": float(o.get("origQty", 0)),
            "price": float(o.get("price", 0)) or None,
            "stopPrice": float(o.get("stopPrice", 0)) or None,
            "reduceOnly": o.get("reduceOnly", False),
            "status": o.get("status"),
        })

    out = {
        "env": env_name,
        "wallet_balance": float(acc.get("totalWalletBalance", 0)),
        "unrealized_pnl": float(acc.get("totalUnrealizedProfit", 0)),
        "margin_balance": float(acc.get("totalMarginBalance", 0)),
        "position": pos,
        "open_orders": exit_orders,
        "mark_price": float(prem.get("markPrice", 0)),
        "index_price": float(prem.get("indexPrice", 0)),
        "last_trade": float(tk.get("price", 0)),
        "ts": time.time(),
    }
    _query_binance_position._cache = {"t": time.time(), "v": out}
    return out


def _query_paper_position(state_subdir='paper', state_filename='state_paper.json', balance_key='paper_balance'):
    """Return a synthetic 'binance position' shape for the dashboard built
    from a paper bot's state.json + mainnet ticker. Paper bots have no real
    exchange position; this lets the dashboard reuse its rendering code.

    Defaults to V2.2 paper bot (data/paper/state_paper.json).
    For div-flip pass state_subdir='paper_divflip', state_filename='state.json',
    balance_key='balance'.

    Cached for 1 second per (subdir) key to absorb dashboard polling.
    """
    cache_key = f"_cache_{state_subdir}"
    cache = getattr(_query_paper_position, cache_key, None)
    if cache and time.time() - cache["t"] < 1.0:
        return cache["v"]

    state_file = os.path.join(BOT_DIR, 'data', state_subdir, state_filename)
    state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file) as f: state = json.load(f)
        except: pass

    # Live mainnet ticker for mark/last price
    last = mark = 0.0
    try:
        with urllib.request.urlopen("https://fapi.binance.com/fapi/v1/ticker/price?symbol=BTCUSDT", timeout=5) as r:
            tk = json.loads(r.read())
            last = mark = float(tk.get("price", 0))
    except Exception:
        pass
    try:
        with urllib.request.urlopen("https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT", timeout=5) as r:
            prem = json.loads(r.read())
            mark = float(prem.get("markPrice", mark))
    except Exception:
        pass

    pos_dict = state.get("position")
    pos = None
    if pos_dict:
        side = pos_dict.get("side", "")
        qty = float(pos_dict.get("qty_total", 0))
        first_entry = float(pos_dict.get("first_entry", 0))
        # Compute weighted avg entry from per-leg entries. PnL must use avg
        # (not first_entry) so post-DCA losses don't get over-reported by the
        # better-priced second leg being ignored.
        entries = pos_dict.get("entries") or [{"px": first_entry, "qty": qty}]
        total_q = sum(float(e.get("qty", 0)) for e in entries) or qty
        avg_entry = (sum(float(e.get("px", 0)) * float(e.get("qty", 0)) for e in entries) / total_q) if total_q > 0 else first_entry
        worst_entry = float(pos_dict.get("worst_entry", first_entry))
        # Synthetic uPnL based on avg entry × total qty
        upnl = (mark - avg_entry) * qty if side == "LONG" else (avg_entry - mark) * qty
        pos = {
            "side": side, "qty": abs(qty),
            "entry": avg_entry,           # show avg as the displayed "entry" post-DCA
            "first_entry": first_entry,   # original L1 fill (for chart marker)
            "worst_entry": worst_entry,   # L2 fill price post-DCA
            "filled": int(pos_dict.get("filled") or len(entries)),
            "uPnL": upnl,
            "leverage": 2, "marginType": "isolated", "isolatedWallet": 0.0,
            "notional": abs(qty) * avg_entry, "markImplied": mark,
        }

    paper_balance = float(state.get(balance_key, state.get("balance", 5000.0)))
    out = {
        "env": state_subdir,
        "wallet_balance": paper_balance,
        "unrealized_pnl": pos["uPnL"] if pos else 0.0,
        "margin_balance": paper_balance + (pos["uPnL"] if pos else 0.0),
        "position": pos,
        "open_orders": [],  # paper has no real resting orders
        "mark_price": mark,
        "index_price": mark,
        "last_trade": last,
        "ts": time.time(),
    }
    setattr(_query_paper_position, cache_key, {"t": time.time(), "v": out})
    return out


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
        """Public pages: dashboards + data files. No auth needed."""
        if path in ('/', '/dashboard.html'):
            return True
        if path.startswith('/data/') or path.startswith('/api/bot/'):
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
            self.send_header('Location', '/dashboard.html')
            self.end_headers()
            return

        # ── Bot API (public, no auth). Day bot is the only active strategy. ──
        # Routes by ?strategy= (preferred) or legacy ?env=:
        #   ?strategy=sr_dca     → V2.2 S/R paper bot (data/paper/state_paper.json)
        #   ?strategy=divflip    → Divergence-flip v1 paper bot (data/paper_divflip/state.json)
        #   ?strategy=divflip_v2 → Divergence-flip v2 paper bot (data/paper_divflip_v2/state.json)
        #   ?env=testnet         → legacy testnet bot data (mostly empty after paper migration)
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query)
        strategy_q = (qs.get('strategy', ['']) or [''])[0]
        env_q = (qs.get('env', ['']) or [''])[0]

        if strategy_q == 'divflip':
            env_dir = 'paper_divflip'
            state_filename = 'state.json'
            status_filename = 'status.json'
            log_filename = 'bot.log'
        elif strategy_q == 'divflip_v2':
            env_dir = 'paper_divflip_v2'
            state_filename = 'state.json'
            status_filename = 'status.json'
            log_filename = 'bot.log'
        elif env_q == 'testnet':
            env_dir = 'testnet'
            state_filename = 'state_day.json'
            status_filename = 'status_day.json'
            log_filename = 'bot_day.log'
        else:
            env_dir = 'paper'
            state_filename = 'state_paper.json'
            status_filename = 'status_paper.json'
            log_filename = 'bot_paper.log'

        if path == '/api/bot/day/state':
            sf = os.path.join(BOT_DIR, 'data', env_dir, state_filename)
            if os.path.exists(sf):
                try:
                    with open(sf) as f: return self._json_response(json.load(f))
                except: pass
            return self._json_response({"position": None, "stats": {"total": 0, "wins": 0, "pnl": 0}})

        if path == '/api/bot/day/status':
            sf = os.path.join(BOT_DIR, 'data', env_dir, status_filename)
            if os.path.exists(sf):
                try:
                    with open(sf) as f: return self._json_response(json.load(f))
                except: pass
            return self._json_response({})

        if path == '/api/bot/day/log':
            lf = os.path.join(BOT_DIR, 'data', env_dir, log_filename)
            lines = []
            if os.path.exists(lf):
                with open(lf) as f: lines = f.readlines()[-100:]
            return self._json_response({"lines": [l.strip() for l in lines]})

        # Live Binance position — for paper mode, return synthetic position
        # built from state_paper.json + mainnet ticker. For testnet, query the
        # real testnet account (as before, requires API keys).
        if path == '/api/bot/day/binance':
            if env_dir == 'paper':
                return self._json_response(_query_paper_position())
            if env_dir == 'paper_divflip':
                return self._json_response(_query_paper_position(
                    state_subdir='paper_divflip',
                    state_filename='state.json',
                    balance_key='balance',
                ))
            if env_dir == 'paper_divflip_v2':
                return self._json_response(_query_paper_position(
                    state_subdir='paper_divflip_v2',
                    state_filename='state.json',
                    balance_key='balance',
                ))
            return self._json_response(_query_binance_position())

        # Dashboard + static files are public (read-only)
        if self._is_public(path):
            return super().do_GET()

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
http.server.ThreadingHTTPServer(('', 8888), BotHandler).serve_forever()
