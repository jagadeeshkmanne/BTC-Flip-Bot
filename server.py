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
import gzip
import io
import urllib.request
import urllib.parse
from urllib.parse import parse_qs, urlparse

BOT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BOT_DIR)
ENV_PATH = os.path.join(BOT_DIR, ".env")

# 2026-06-05: in-memory caches for Bybit-proxy endpoints. Prevents thread
# starvation when 6+ concurrent dashboard requests all proxy Bybit
# simultaneously. Threading-safe enough for our use case (read-modify-write
# is fine — worst case is a duplicate fetch).
_TICKER_CACHE: dict = {}        # {'val': {'data': ..., 'cached_at_ms': ...}}
_KLINES_CACHE: dict = {}        # {'<interval>:<limit>': {'data': ..., 'cached_at_ms': ...}}


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
        # 2026-06-05: hashed-filename assets are `immutable` — browser caches
        # forever, never re-validates. New deploys get new hashes (Vite
        # injects content hash into filename) so browser auto-fetches fresh.
        # WITHOUT immutable, the prior `must-revalidate` made browser ask
        # the server EVERY time → server hammered → timeouts under load.
        # Everything else (HTML, API, state.json): no-cache.
        p = getattr(self, 'path', '') or ''
        if '/static/bots/assets/' in p or '/bots/assets/' in p:
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable')
        else:
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()

    def _json_response(self, data, code=200):
        # 2026-06-05: gzip JSON responses when client supports it.
        # Most modern browsers always send Accept-Encoding: gzip — cuts /api/bots/all
        # from 7KB → 2KB on the wire.
        body = json.dumps(data).encode()
        accept_enc = (self.headers.get('Accept-Encoding') or '').lower()
        if 'gzip' in accept_enc and len(body) > 512:
            body = gzip.compress(body, compresslevel=6)
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Encoding', 'gzip')
            self.send_header('Vary', 'Accept-Encoding')
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
        if path.startswith('/data/') or path.startswith('/api/bot/') or path == '/api/bots/all':
            return True
        # 2026-06-05: lightweight Preact dashboard built into static/bots/
        if path == '/bots' or path.startswith('/bots/'):
            return True
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        # Auth status is always open
        if path == '/api/auth/status':
            pw_set = bool(get_dashboard_password())
            return self._json_response({"password_set": pw_set})

        # 2026-06-05: root + classic dashboard both redirect to the new Preact dashboard.
        # (Classic dashboard.html still on disk for reference but no longer served.)
        if path == '/' or path == '/dashboard.html':
            self.send_response(302)
            self.send_header('Location', '/bots/v1')
            self.end_headers()
            return

        # 2026-06-05: favicon stub so browsers don't see 401 in console.
        if path == '/favicon.ico':
            self.send_response(204)  # No Content
            self.end_headers()
            return

        # ─────────────────────────────────────────────────────────
        # Bybit-proxied public price feed.
        # Added 2026-06-05: some mobile carriers (esp. India) block direct
        # browser calls to fapi.binance.com / fstream.binance.com, leaving
        # the dashboard with stale prices. These two endpoints proxy through
        # the dashboard server (GCP, unblocked) and return data in Binance
        # format so the existing dashboard JS doesn't need parser changes.
        # ─────────────────────────────────────────────────────────
        if path == '/api/ticker':
            # Live BTC price. Returns Binance-style {symbol, price, time}.
            # 2026-06-05: 2-sec cache to prevent every browser request from
            # hitting Bybit. Was causing thread starvation when 6+ concurrent
            # dashboard requests all proxied Bybit simultaneously.
            now_ms = int(time.time() * 1000)
            cached = _TICKER_CACHE.get('val')
            if cached and (now_ms - cached.get('cached_at_ms', 0)) < 2000:
                return self._json_response(cached['data'])
            try:
                with urllib.request.urlopen(
                    "https://api.bybit.com/v5/market/tickers?category=linear&symbol=BTCUSDT",
                    timeout=3) as r:
                    data = json.loads(r.read())
                row = data.get("result", {}).get("list", [{}])[0]
                px = row.get("lastPrice") or row.get("markPrice")
                resp = {
                    "symbol": "BTCUSDT",
                    "price":  str(px) if px is not None else None,
                    "time":   int(data.get("time") or 0),
                }
                _TICKER_CACHE['val'] = {'data': resp, 'cached_at_ms': now_ms}
                return self._json_response(resp)
            except Exception as e:
                # On error, return stale cache if available (better than 502)
                if cached:
                    return self._json_response(cached['data'])
                return self._json_response({"error": str(e)}, code=502)

        if path == '/api/klines':
            # 5m kline proxy. Bybit returns newest-first; reorder to oldest-first
            # and reshape each row to [openTime, o, h, l, c, v, closeTime] so the
            # dashboard JS that already speaks Binance format works untouched.
            from urllib.parse import parse_qs as _pq
            _qs = _pq(parsed.query)
            limit = int((_qs.get('limit', ['1']) or ['1'])[0])
            limit = max(1, min(limit, 1000))   # Bybit cap
            interval = (_qs.get('interval', ['5m']) or ['5m'])[0]
            ivl_map = {'1m':'1','3m':'3','5m':'5','15m':'15','30m':'30',
                       '1h':'60','2h':'120','4h':'240','6h':'360','12h':'720','1d':'D'}
            bybit_ivl = ivl_map.get(interval, '5')
            step_ms = {'1m':60000,'3m':180000,'5m':300000,'15m':900000,'30m':1800000,
                       '1h':3600000,'2h':7200000,'4h':14400000,'6h':21600000,
                       '12h':43200000,'1d':86400000}.get(interval, 300000)
            # 2026-06-05: 30-sec cache per (interval, limit) to avoid
            # hammering Bybit on every dashboard refresh.
            cache_key = f"{interval}:{limit}"
            now_ms = int(time.time() * 1000)
            cached = _KLINES_CACHE.get(cache_key)
            if cached and (now_ms - cached.get('cached_at_ms', 0)) < 30000:
                return self._json_response(cached['data'])
            try:
                url = (f"https://api.bybit.com/v5/market/kline?category=linear"
                       f"&symbol=BTCUSDT&interval={bybit_ivl}&limit={limit}")
                with urllib.request.urlopen(url, timeout=5) as r:
                    data = json.loads(r.read())
                rows = list(data.get("result", {}).get("list", []))
                rows.reverse()    # Bybit newest-first → dashboard wants oldest-first
                out = []
                for b in rows:
                    open_t = int(b[0])
                    out.append([open_t, b[1], b[2], b[3], b[4], b[5], open_t + step_ms - 1])
                _KLINES_CACHE[cache_key] = {'data': out, 'cached_at_ms': now_ms}
                return self._json_response(out)
            except Exception as e:
                return self._json_response({"error": str(e)}, code=502)

        # 2026-06-05: batch endpoint — return all 3 RSI bots' status + state in ONE
        # response. Cuts the dashboard's per-bot poll storm (3 statuses + 3 states =
        # 6 connections every 5s) down to 1 connection. Big perf win for the
        # toy http.server.
        if path == '/api/bots/all':
            ids = ['rsiscalp_trend', 'rsiscalp_trend_v11', 'rsiscalp_trend_v2', 'rsiscalp_trend_v5']
            id_to_dir = {
                'rsiscalp_trend':     'paper_rsiscalp_trend',
                'rsiscalp_trend_v11': 'paper_rsiscalp_trend_v11',
                'rsiscalp_trend_v2':  'paper_rsiscalp_trend_v2',
                'rsiscalp_trend_v5':  'paper_rsiscalp_trend_v5',
            }
            out = {}
            for sid in ids:
                d = id_to_dir[sid]
                sf = os.path.join(BOT_DIR, 'data', d, 'state.json')
                stf = os.path.join(BOT_DIR, 'data', d, 'status.json')
                entry = {'state': None, 'status': None}
                try:
                    if os.path.exists(stf):
                        with open(stf) as f: entry['status'] = json.load(f)
                except Exception: pass
                try:
                    if os.path.exists(sf):
                        with open(sf) as f: entry['state'] = json.load(f)
                except Exception: pass
                out[sid] = entry
            return self._json_response(out)

        # ── Bot API (public, no auth). Active strategies: ──
        # Routes by ?strategy= (preferred) or legacy ?env=:
        #   ?strategy=sr_dca       → V2.2 S/R paper bot (data/paper/state_paper.json)
        #   ?strategy=divflip      → Divergence-flip v1 paper bot (data/paper_divflip/state.json)
        #   ?strategy=divflip_v2   → Divflip v2 paper bot, 1h trend filter (data/paper_divflip_v2/state.json)
        #   ?strategy=divflip_sharp  → Divflip v1b paper bot, L3 disabled (data/paper_divflip_sharp/state.json)
        #   ?strategy=divflip_pro    → Divflip Pro paper bot, EMA200+ATR filters (data/paper_divflip_pro/state.json)
        #   ?env=testnet           → legacy testnet bot data (mostly empty after paper migration)
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
        elif strategy_q == 'divflip_sharp':
            env_dir = 'paper_divflip_sharp'
            state_filename = 'state.json'
            status_filename = 'status.json'
            log_filename = 'bot.log'
        elif strategy_q == 'divflip_pro':
            env_dir = 'paper_divflip_pro'
            state_filename = 'state.json'
            status_filename = 'status.json'
            log_filename = 'bot.log'
        elif strategy_q == 'rsiscalp_trend':
            env_dir = 'paper_rsiscalp_trend'
            state_filename = 'state.json'
            status_filename = 'status.json'
            log_filename = 'bot.log'
        elif strategy_q == 'rsiscalp_trend_v2':
            env_dir = 'paper_rsiscalp_trend_v2'
            state_filename = 'state.json'
            status_filename = 'status.json'
            log_filename = 'bot.log'
        elif strategy_q == 'rsiscalp_trend_v3':
            env_dir = 'paper_rsiscalp_trend_v3'
            state_filename = 'state.json'
            status_filename = 'status.json'
            log_filename = 'bot.log'
        elif strategy_q == 'rsiscalp_trend_v4':
            env_dir = 'paper_rsiscalp_trend_v4'
            state_filename = 'state.json'
            status_filename = 'status.json'
            log_filename = 'bot.log'
        elif strategy_q == 'rsiscalp_trend_v5':
            env_dir = 'paper_rsiscalp_trend_v5'
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
            if env_dir == 'paper_divflip_sharp':
                return self._json_response(_query_paper_position(
                    state_subdir='paper_divflip_sharp',
                    state_filename='state.json',
                    balance_key='balance',
                ))
            if env_dir == 'paper_divflip_pro':
                return self._json_response(_query_paper_position(
                    state_subdir='paper_divflip_pro',
                    state_filename='state.json',
                    balance_key='balance',
                ))
            if env_dir == 'paper_rsiscalp_trend':
                return self._json_response(_query_paper_position(
                    state_subdir='paper_rsiscalp_trend',
                    state_filename='state.json',
                    balance_key='balance',
                ))
            if env_dir == 'paper_rsiscalp_trend_v2':
                return self._json_response(_query_paper_position(
                    state_subdir='paper_rsiscalp_trend_v2',
                    state_filename='state.json',
                    balance_key='balance',
                ))
            if env_dir == 'paper_rsiscalp_trend_v3':
                return self._json_response(_query_paper_position(
                    state_subdir='paper_rsiscalp_trend_v3',
                    state_filename='state.json',
                    balance_key='balance',
                ))
            if env_dir == 'paper_rsiscalp_trend_v4':
                return self._json_response(_query_paper_position(
                    state_subdir='paper_rsiscalp_trend_v4',
                    state_filename='state.json',
                    balance_key='balance',
                ))
            if env_dir == 'paper_rsiscalp_trend_v5':
                return self._json_response(_query_paper_position(
                    state_subdir='paper_rsiscalp_trend_v5',
                    state_filename='state.json',
                    balance_key='balance',
                ))
            return self._json_response(_query_binance_position())

        # 2026-06-05: Preact dashboard at /bots/* — MUST come before _is_public
        # check (which would route everything to the default SimpleHTTPRequestHandler
        # static handler and 404 on the SPA paths).
        if path == '/bots' or path == '/bots/':
            self.send_response(302)
            self.send_header('Location', '/bots/v1')
            self.end_headers()
            return
        if path.startswith('/bots/'):
            static_root = os.path.join(BOT_DIR, 'static', 'bots')
            rel = path[len('/bots/'):]  # e.g. "v2" or "assets/index-abc.js"
            candidate = os.path.normpath(os.path.join(static_root, rel))
            if not candidate.startswith(static_root):
                self.send_error(403); return
            if os.path.isfile(candidate):
                # 2026-06-05: serve pre-compressed .gz if it exists and client supports it.
                # ~3× smaller transfer for JS/CSS bundles. No on-the-fly CPU cost.
                accept_enc = (self.headers.get('Accept-Encoding') or '').lower()
                gz_path = candidate + '.gz'
                if 'gzip' in accept_enc and os.path.isfile(gz_path):
                    try:
                        with open(gz_path, 'rb') as f: data = f.read()
                        ctype = self.guess_type(candidate)  # type of ORIGINAL file
                        self.send_response(200)
                        self.send_header('Content-Type', ctype)
                        self.send_header('Content-Length', str(len(data)))
                        self.send_header('Content-Encoding', 'gzip')
                        self.send_header('Vary', 'Accept-Encoding')
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    except OSError:
                        pass  # fall through to uncompressed
                self.path = '/static/bots/' + rel
                return super().do_GET()
            index = os.path.join(static_root, 'index.html')
            if os.path.isfile(index):
                # Same gzip path for SPA fallback HTML
                accept_enc = (self.headers.get('Accept-Encoding') or '').lower()
                gz_idx = index + '.gz'
                if 'gzip' in accept_enc and os.path.isfile(gz_idx):
                    try:
                        with open(gz_idx, 'rb') as f: data = f.read()
                        self.send_response(200)
                        self.send_header('Content-Type', 'text/html; charset=utf-8')
                        self.send_header('Content-Length', str(len(data)))
                        self.send_header('Content-Encoding', 'gzip')
                        self.send_header('Vary', 'Accept-Encoding')
                        self.end_headers()
                        self.wfile.write(data)
                        return
                    except OSError:
                        pass
                self.path = '/static/bots/index.html'
                return super().do_GET()
            return self._json_response({"error": "dashboard not built yet"}, code=503)

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


# 2026-06-05: port configurable via env (BTC_BOT_PORT). Default 8888 to keep
# back-compat. nginx setup uses 8889 (internal-only) with nginx fronting 8888.
_PORT = int(os.environ.get("BTC_BOT_PORT", "8888"))
_BIND = os.environ.get("BTC_BOT_BIND", "")  # empty = all interfaces; '127.0.0.1' to lock to localhost
print(f"Starting server on http://{_BIND or 'localhost'}:{_PORT}")
print(f"Dashboard:  http://localhost:{_PORT}/dashboard.html")
http.server.ThreadingHTTPServer((_BIND, _PORT), BotHandler).serve_forever()
