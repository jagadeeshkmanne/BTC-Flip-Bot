#!/usr/bin/env python3
"""
Futures Bot v3 — BTC 15m MACD+RSI Momentum
Binance Futures Testnet

MODULE 1: BTC 15m MACD+RSI Momentum (OPTIMIZED v3 — Multi-Timeframe)
  Backtest (180d real Binance data, Apr 2026):
    v2 (15m only):   +95.6% | 34.0% WR | PF 1.19 | 29.0% max DD | 2x leverage
    v3 (15m + 4H):  +185.6% | 35.6% WR | PF 1.49 |  9.7% max DD | 2x leverage ← CURRENT
  Entry: MACD 12/26/9 cross + RSI 30-70 zone filter + 4H trend alignment
    LONG:  15m MACD bullish cross + RSI 30-70 + 4H trend BULLISH or NEUTRAL
    SHORT: 15m MACD bearish cross + RSI 30-70 + 4H trend BEARISH or NEUTRAL
  4H Trend: MACD histogram > 0 AND RSI > 45 = BULLISH
            MACD histogram < 0 AND RSI < 55 = BEARISH
            Otherwise = NEUTRAL (both directions allowed)
  Exit: Signal Flip | SL -5% hard (no TP — MACD flip exits are best)
  Protection: Cooldown (6 bars/1.5h after SL) | Volume filter (skip if vol < 80% SMA20)
  TSL: OFF (optimizer proved no benefit when TP is wide)
  NO DCA — backtest showed DCA hurts performance
  Timeframe: 15m entries, 4H trend filter
  Pair: BTCUSDT only

MODULE 2: Top Gainer Short Scanner (unchanged)
  Scans 25 coins for 24h pumps > 15%
  Shorts after exhaustion (RSI drop from >75, red candles, below upper BB)
  Hard SL -15% | Max hold 72h

STRATEGY SELECTION (60-day backtest on real Binance data):
  MACD+RSI:    +30.4% | PF 1.32 | 9.7% DD  ← WINNER
  BB+RSI:      +14.6% | PF 1.22 | 17.6% DD (old strategy)
  EMA+RSI:      -4.3% | PF 0.94 | 13.1% DD
  EMA+ADX:     -21.9% | PF 0.54 | 22.1% DD
  Triple EMA:   -0.1% | PF 0.99 | 8.5% DD
"""

import os
import sys
import json
import time
import logging
import hmac
import hashlib
import argparse
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import numpy as np
import pandas as pd
import requests

# ─── Load .env file (MUST come before any os.environ.get calls) ───
BOT_DIR = os.path.dirname(os.path.abspath(__file__))

def load_dotenv(path):
    """Load .env file into os.environ."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ.setdefault(key.strip(), val.strip())

load_dotenv(os.path.join(BOT_DIR, ".env"))

# ─── Email Configuration (loaded AFTER .env) ───
EMAIL_CONFIG = {
    "enabled": True,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "sender_email": os.environ.get("BOT_EMAIL", ""),
    "sender_password": os.environ.get("BOT_EMAIL_PASS", ""),
    "recipient_email": os.environ.get("BOT_EMAIL_TO", ""),
}

# ─── Parse --env argument ───
def parse_env():
    parser = argparse.ArgumentParser(description="BTC MACD+RSI Trading Bot")
    parser.add_argument("--env", default="testnet", choices=["testnet", "production"],
                        help="Environment: testnet or production (default: testnet)")
    args, _ = parser.parse_known_args()
    return args.env

ENV = parse_env()

# ─── Check if environment is enabled ───
def check_enabled(env):
    """Check if this environment is enabled. If disabled, exit silently."""
    disabled_flag = os.path.join(BOT_DIR, "data", env, ".disabled")
    if os.path.exists(disabled_flag):
        print(f"[{env}] Bot is DISABLED. Remove {disabled_flag} or enable via settings UI.")
        sys.exit(0)

check_enabled(ENV)

# ─── Load config from JSON ───
def load_config(env):
    config_path = os.path.join(BOT_DIR, "config", f"{env}.json")
    if not os.path.exists(config_path):
        print(f"ERROR: Config file not found: {config_path}")
        sys.exit(1)

    with open(config_path) as f:
        cfg = json.load(f)

    # Resolve API keys from environment variables
    cfg["api_key"] = os.environ.get(cfg.pop("api_key_env", ""), "")
    cfg["api_secret"] = os.environ.get(cfg.pop("api_secret_env", ""), "")

    if not cfg["api_key"] or not cfg["api_secret"]:
        print(f"ERROR: API keys not found in .env for '{env}' environment")
        sys.exit(1)

    # Set data paths based on environment
    data_dir = os.path.join(BOT_DIR, "data", env)
    os.makedirs(data_dir, exist_ok=True)
    cfg["state_file"] = os.path.join(data_dir, "state.json")
    cfg["log_file"] = os.path.join(data_dir, "trades.log")
    cfg["env"] = env

    return cfg

CONFIG = load_config(ENV)

# ─── Module 2: Top Gainer Short Config ───
GAINER_CONFIG = {
    "min_24h_gain": 0.15,
    "rsi_was_high_threshold": 75,
    "rsi_entry_below": 70,
    "min_red_candles": 2,
    "leverage": 2,
    "capital_per_trade": 200,
    "max_gainer_positions": 3,
    "trailing_tp_activate": 0.012,
    "trailing_tp_trail": 0.006,
    "dca_levels": [
        {"deviation": 0.015, "multiplier": 1.5},
        {"deviation": 0.03, "multiplier": 2.0},
    ],
    "emergency_sl_pct": 0.08,
    "hard_sl_pct": 0.15,
    "max_hold_hours": 72,
    "scan_pairs": [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
        "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT", "LINKUSDT",
        "MATICUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT", "OPUSDT",
        "SUIUSDT", "INJUSDT", "TIAUSDT", "SEIUSDT", "JUPUSDT",
        "WIFUSDT", "PEPEUSDT", "BONKUSDT", "FLOKIUSDT", "ORDIUSDT",
    ],
}

# ─── Logging ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(CONFIG["log_file"]),
        logging.StreamHandler(),
    ]
)
log = logging.getLogger("FuturesBotV2")

# ─── State Management ───
def load_state():
    if os.path.exists(CONFIG["state_file"]):
        with open(CONFIG["state_file"]) as f:
            return json.load(f)
    return {
        "positions": {},
        "trade_history": [],
        "last_run": None,
        "stats": {
            "total_trades": 0, "wins": 0, "losses": 0,
            "total_profit_usd": 0,
            "long_wins": 0, "long_losses": 0,
            "short_wins": 0, "short_losses": 0,
            "flip_trades": 0, "tp_trades": 0, "sl_trades": 0, "dca_sl_trades": 0,
        }
    }

def save_state(state):
    state["last_run"] = datetime.now(timezone.utc).isoformat()
    with open(CONFIG["state_file"], "w") as f:
        json.dump(state, f, indent=2)


# ═══════════════════════════════════════════════════════════════
# BINANCE FUTURES API CLIENT
# ═══════════════════════════════════════════════════════════════

class FuturesClient:
    def __init__(self, api_key, api_secret, base_url):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": api_key})

    def _sign(self, params):
        query = urlencode(params)
        sig = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    def _request(self, method, endpoint, params=None, signed=False):
        url = self.base_url + endpoint
        params = params or {}
        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = 10000
            params = self._sign(params)
        r = self.session.request(method, url, params=params, timeout=15)
        if not r.ok:
            log.error(f"API {method} {endpoint}: {r.status_code} {r.text}")
        r.raise_for_status()
        return r.json()

    def get_klines(self, symbol, interval, limit=100):
        return self._request("GET", "/fapi/v1/klines",
            {"symbol": symbol, "interval": interval, "limit": limit})

    def get_price(self, symbol):
        d = self._request("GET", "/fapi/v1/ticker/price", {"symbol": symbol})
        return float(d["price"])

    def get_account(self):
        return self._request("GET", "/fapi/v2/account", signed=True)

    def set_leverage(self, symbol, leverage):
        try:
            return self._request("POST", "/fapi/v1/leverage",
                {"symbol": symbol, "leverage": leverage}, signed=True)
        except Exception:
            pass

    def set_margin_type(self, symbol, margin_type="ISOLATED"):
        try:
            return self._request("POST", "/fapi/v1/marginType",
                {"symbol": symbol, "marginType": margin_type}, signed=True)
        except Exception:
            pass

    def place_order(self, symbol, side, quantity, reduce_only=False):
        params = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": quantity}
        if reduce_only:
            params["reduceOnly"] = "true"
        return self._request("POST", "/fapi/v1/order", params, signed=True)

    def place_stop_market(self, symbol, side, stop_price, quantity=None, close_position=False):
        """Place a STOP_MARKET order via Algo Order API (/fapi/v1/algoOrder).
        Binance migrated conditional orders to this endpoint as of Dec 2025.
        Requires algoType=CONDITIONAL and uses triggerPrice instead of stopPrice."""
        params = {
            "symbol": symbol,
            "side": side,
            "algoType": "CONDITIONAL",
            "type": "STOP_MARKET",
            "triggerPrice": f"{stop_price:.2f}",
            "workingType": "MARK_PRICE",
        }
        if close_position:
            params["closePosition"] = "true"
        elif quantity:
            params["quantity"] = f"{quantity}"
            params["reduceOnly"] = "true"
        return self._request("POST", "/fapi/v1/algoOrder", params, signed=True)

    def place_take_profit_market(self, symbol, side, stop_price, quantity=None, close_position=False):
        """Place a TAKE_PROFIT_MARKET order via Algo Order API."""
        params = {
            "symbol": symbol,
            "side": side,
            "algoType": "CONDITIONAL",
            "type": "TAKE_PROFIT_MARKET",
            "triggerPrice": f"{stop_price:.2f}",
            "workingType": "MARK_PRICE",
        }
        if close_position:
            params["closePosition"] = "true"
        elif quantity:
            params["quantity"] = f"{quantity}"
            params["reduceOnly"] = "true"
        return self._request("POST", "/fapi/v1/algoOrder", params, signed=True)

    def place_trailing_stop(self, symbol, side, callback_rate, activation_price=None, quantity=None, close_position=False):
        """Place a TRAILING_STOP_MARKET order via Algo Order API.
        callback_rate: % drop from peak to trigger (e.g., 1.0 = 1%)
        activation_price: price at which trailing starts (optional)"""
        params = {
            "symbol": symbol,
            "side": side,
            "algoType": "CONDITIONAL",
            "type": "TRAILING_STOP_MARKET",
            "callbackRate": f"{callback_rate:.1f}",
            "workingType": "MARK_PRICE",
        }
        if activation_price:
            params["activationPrice"] = f"{activation_price:.2f}"
        if close_position:
            params["closePosition"] = "true"
        elif quantity:
            params["quantity"] = f"{quantity}"
            params["reduceOnly"] = "true"
        return self._request("POST", "/fapi/v1/algoOrder", params, signed=True)

    def cancel_all_orders(self, symbol):
        """Cancel all open orders for a symbol — both regular and algo orders."""
        # Cancel regular orders
        try:
            self._request("DELETE", "/fapi/v1/allOpenOrders",
                {"symbol": symbol}, signed=True)
        except Exception as e:
            log.warning(f"  Cancel regular orders failed for {symbol}: {e}")
        # Cancel algo orders (STOP_MARKET, TP, trailing placed via /fapi/v1/algoOrder)
        try:
            self._request("DELETE", "/fapi/v1/algoOpenOrders",
                {"symbol": symbol}, signed=True)
        except Exception as e:
            log.warning(f"  Cancel algo orders failed for {symbol}: {e}")
        return None

    def get_open_orders(self, symbol):
        """Get all open orders for a symbol."""
        try:
            return self._request("GET", "/fapi/v1/openOrders",
                {"symbol": symbol}, signed=True)
        except Exception:
            return []

    def get_exchange_info(self, symbol):
        data = self._request("GET", "/fapi/v1/exchangeInfo")
        for s in data["symbols"]:
            if s["symbol"] == symbol:
                for f in s["filters"]:
                    if f["filterType"] == "LOT_SIZE":
                        step = float(f["stepSize"])
                        return {
                            "step_size": step,
                            "min_qty": float(f["minQty"]),
                            "precision": max(0, int(round(-np.log10(step)))) if step > 0 else 8,
                        }
        return None

    def get_positions(self):
        """Get all open positions from Binance (for sync check)."""
        try:
            account = self._request("GET", "/fapi/v2/account", signed=True)
            positions = []
            for p in account.get("positions", []):
                amt = float(p.get("positionAmt", 0))
                if amt != 0:
                    positions.append({
                        "symbol": p["symbol"],
                        "side": "LONG" if amt > 0 else "SHORT",
                        "qty": abs(amt),
                        "entry_price": float(p.get("entryPrice", 0)),
                        "unrealized_pnl": float(p.get("unrealizedProfit", 0)),
                    })
            return positions
        except Exception as e:
            log.error(f"Failed to get positions: {e}")
            return []


# ═══════════════════════════════════════════════════════════════
# INDICATORS
# ═══════════════════════════════════════════════════════════════

def get_candles(client, symbol, interval, limit):
    klines = client.get_klines(symbol, interval, limit)
    df = pd.DataFrame(klines, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"
    ])
    for col in ["close", "high", "low", "open", "volume"]:
        df[col] = df[col].astype(float)
    return df

def compute_sma(series, period):
    return series.rolling(window=period).mean()

def compute_rsi(df, period=14):
    delta = df["close"].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def compute_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def compute_bb(df, period=20, std_mult=2.0):
    mid = compute_sma(df["close"], period)
    std = df["close"].rolling(window=period).std()
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return upper, mid, lower

def compute_bb_bandwidth(bb_upper, bb_lower, bb_mid):
    """BB bandwidth = (upper - lower) / mid. High = volatile, low = squeeze."""
    return (bb_upper - bb_lower) / bb_mid

def compute_macd(df, fast=12, slow=26, signal=9):
    ema_fast = compute_ema(df["close"], fast)
    ema_slow = compute_ema(df["close"], slow)
    macd_line = ema_fast - ema_slow
    signal_line = compute_ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def round_qty(qty, step_size, precision):
    if step_size:
        qty = qty - (qty % step_size)
    return round(qty, precision)


# ═══════════════════════════════════════════════════════════════
# MODULE 1: BTC 15m BB+RSI DCA+Flip
# ═══════════════════════════════════════════════════════════════

def compute_atr(df, period=14):
    """Average True Range for volatility-based stops."""
    high = df["high"]
    low = df["low"]
    close = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs()
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1/period, min_periods=period).mean()


# ═══════════════════════════════════════════════════════════════
# MULTI-TIMEFRAME: 4H Trend Filter
# ═══════════════════════════════════════════════════════════════

def get_htf_trend(client, symbol="BTCUSDT", interval="4h", limit=100):
    """
    Compute higher-timeframe (4H) trend using RSI only.
    5-year backtest winner: PF 1.55, DD 33.7%, WR 36.2%
    Profitable in bull (2021), bear (2022), recovery (2023), halving (2024), and beyond.

    RSI > 50 = bullish (allow LONG only)
    RSI < 50 = bearish (allow SHORT only)
    No neutral zone — always picks a direction.

    Returns: (trend, htf_info)
      trend:  1 = bullish (allow LONG), -1 = bearish (allow SHORT)
      htf_info: dict with 4H indicator values for logging/dashboard
    """
    try:
        df_htf = get_candles(client, symbol, interval, limit)

        if len(df_htf) < 50:
            log.warning(f"  HTF: Not enough 4H candles ({len(df_htf)}), skipping filter")
            return 0, {}

        # Compute 4H RSI — the only indicator needed for trend direction
        rsi = compute_rsi(df_htf, 14)
        curr_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50

        htf_info = {
            "htf_rsi": curr_rsi,
        }

        # RSI-only filter (5-year backtest champion)
        if curr_rsi > 50:
            trend = 1   # Bullish — allow LONG, block SHORT
        else:
            trend = -1  # Bearish — allow SHORT, block LONG
        # No neutral zone — always picks a direction

        return trend, htf_info

    except Exception as e:
        log.warning(f"  HTF trend error: {e}")
        return 0, {}  # On error, allow both directions (safe fallback)


def get_15m_signal(df):
    """
    BTC 15m MACD+RSI Momentum signal.

    LONG:  MACD line crosses above signal line + RSI in 40-70 zone
    SHORT: MACD line crosses below signal line + RSI in 30-60 zone

    This is a MOMENTUM strategy (not mean reversion).
    MACD catches trend changes, RSI filters out extreme overbought/oversold
    where reversals are likely.

    Returns: (signal, indicators_dict, reason)
    """
    rsi = compute_rsi(df, CONFIG["rsi_period"])
    macd_line, signal_line, macd_hist = compute_macd(
        df, CONFIG["macd_fast"], CONFIG["macd_slow"], CONFIG["macd_signal"]
    )
    atr = compute_atr(df, CONFIG.get("atr_period", 14))

    # Also compute BB for dashboard display (indicators)
    bb_upper, bb_mid, bb_lower = compute_bb(df, 20, 2.0)
    bb_bw = compute_bb_bandwidth(bb_upper, bb_lower, bb_mid)

    curr_price = float(df["close"].iloc[-1])
    curr_rsi = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else 50
    curr_macd = float(macd_line.iloc[-1]) if not pd.isna(macd_line.iloc[-1]) else 0
    prev_macd = float(macd_line.iloc[-2]) if not pd.isna(macd_line.iloc[-2]) else 0
    curr_signal = float(signal_line.iloc[-1]) if not pd.isna(signal_line.iloc[-1]) else 0
    prev_signal = float(signal_line.iloc[-2]) if not pd.isna(signal_line.iloc[-2]) else 0
    curr_hist = float(macd_hist.iloc[-1]) if not pd.isna(macd_hist.iloc[-1]) else 0
    curr_atr = float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else 0

    # BB values for dashboard display
    curr_bb_u = float(bb_upper.iloc[-1]) if not pd.isna(bb_upper.iloc[-1]) else curr_price
    curr_bb_l = float(bb_lower.iloc[-1]) if not pd.isna(bb_lower.iloc[-1]) else curr_price
    curr_bb_m = float(bb_mid.iloc[-1]) if not pd.isna(bb_mid.iloc[-1]) else curr_price
    curr_bw = float(bb_bw.iloc[-1]) if not pd.isna(bb_bw.iloc[-1]) else 0.02

    # Volume SMA for volume filter
    vol = df["volume"].astype(float)
    vol_sma = vol.rolling(CONFIG.get("vol_sma_period", 20)).mean()
    curr_vol = float(vol.iloc[-1]) if not pd.isna(vol.iloc[-1]) else 0
    curr_vol_sma = float(vol_sma.iloc[-1]) if not pd.isna(vol_sma.iloc[-1]) else 0

    # Detect fresh crossovers (happened THIS candle, not a previous one)
    fresh_bull_cross = (prev_macd <= prev_signal and curr_macd > curr_signal)
    fresh_bear_cross = (prev_macd >= prev_signal and curr_macd < curr_signal)

    indicators = {
        "price": curr_price, "rsi": curr_rsi,
        "bb_upper": curr_bb_u, "bb_lower": curr_bb_l, "bb_mid": curr_bb_m,
        "bb_bandwidth": curr_bw, "macd_hist": curr_hist,
        "macd_line": curr_macd, "signal_line": curr_signal, "atr": curr_atr,
        "macd_line_prev": prev_macd, "signal_line_prev": prev_signal,
        "fresh_bull_cross": fresh_bull_cross, "fresh_bear_cross": fresh_bear_cross,
        "volume": curr_vol, "vol_sma": curr_vol_sma,
    }

    if pd.isna(macd_line.iloc[-1]) or pd.isna(rsi.iloc[-1]):
        return "HOLD", indicators, "Indicators not ready"

    # ── Check LONG signal: MACD bullish cross + RSI 40-70 ──
    macd_bull_cross = (prev_macd <= prev_signal and curr_macd > curr_signal)
    if macd_bull_cross:
        if CONFIG["rsi_long_min"] <= curr_rsi <= CONFIG["rsi_long_max"]:
            reason = f"MACD_BULL_CROSS(RSI={curr_rsi:.1f},MACD={curr_hist:.2f})"
            return "LONG", indicators, reason
        else:
            return "HOLD", indicators, f"MACD bull cross but RSI={curr_rsi:.1f} outside 40-70"

    # ── Check SHORT signal: MACD bearish cross + RSI 30-60 ──
    macd_bear_cross = (prev_macd >= prev_signal and curr_macd < curr_signal)
    if macd_bear_cross:
        if CONFIG["rsi_short_min"] <= curr_rsi <= CONFIG["rsi_short_max"]:
            reason = f"MACD_BEAR_CROSS(RSI={curr_rsi:.1f},MACD={curr_hist:.2f})"
            return "SHORT", indicators, reason
        else:
            return "HOLD", indicators, f"MACD bear cross but RSI={curr_rsi:.1f} outside 30-60"

    return "HOLD", indicators, ""


def calc_position(pos):
    """Calculate avg entry, total qty, total cost."""
    total_cost = sum(o["price"] * o["qty"] for o in pos["orders"])
    total_qty = sum(o["qty"] for o in pos["orders"])
    avg_price = total_cost / total_qty if total_qty > 0 else 0
    return avg_price, total_qty, total_cost

def calc_pnl(pos, current_price, leverage=None):
    """Calculate P&L % and USD."""
    lev = leverage or CONFIG["leverage"]
    avg, qty, cost = calc_position(pos)
    if avg == 0:
        return 0, 0
    if pos["side"] == "LONG":
        pnl_pct = (current_price - avg) / avg * lev
    else:
        pnl_pct = (avg - current_price) / avg * lev
    pnl_usd = pnl_pct * cost
    return pnl_pct, pnl_usd

def send_trade_email(trade, stats, balance):
    """Send email notification when a trade closes."""
    cfg = EMAIL_CONFIG
    if not cfg["enabled"] or not cfg["sender_email"] or not cfg["sender_password"]:
        return
    try:
        pnl_sign = "+" if trade["pnl_usd"] >= 0 else ""
        result_emoji = "\u2705" if trade["result"] == "WIN" else "\u274c"
        wins = stats.get("wins", 0)
        total = stats.get("total_trades", 0)
        wr = (wins / total * 100) if total > 0 else 0

        subject = f"{result_emoji} BTC Bot: {trade['side']} {trade['result']} {pnl_sign}${trade['pnl_usd']:.2f} ({pnl_sign}{trade['pnl_pct']:.2f}%)"

        # Duration
        dur = "—"
        if trade.get("entry_time") and trade.get("time"):
            try:
                entry_dt = datetime.fromisoformat(trade["entry_time"])
                exit_dt = datetime.fromisoformat(trade["time"])
                hrs = (exit_dt - entry_dt).total_seconds() / 3600
                dur = f"{hrs:.1f}h" if hrs >= 1 else f"{max(1, int(hrs * 60))}m"
            except:
                pass

        body = f"""
        <html><body style="font-family:Arial,sans-serif;background:#0d1117;color:#e6edf3;padding:20px;">
        <div style="max-width:500px;margin:0 auto;background:#161b22;border-radius:12px;padding:24px;border:1px solid #30363d;">
            <h2 style="margin:0 0 16px;color:{'#22c55e' if trade['pnl_usd']>=0 else '#ef4444'};">
                {result_emoji} Trade Closed — {trade['result']}
            </h2>
            <table style="width:100%;border-collapse:collapse;color:#e6edf3;">
                <tr><td style="padding:8px 0;color:#8b949e;">Pair</td><td style="padding:8px 0;text-align:right;font-weight:bold;">{trade['symbol']}</td></tr>
                <tr><td style="padding:8px 0;color:#8b949e;">Side</td><td style="padding:8px 0;text-align:right;"><span style="color:{'#22c55e' if trade['side']=='LONG' else '#ef4444'};font-weight:bold;">{trade['side']}</span></td></tr>
                <tr><td style="padding:8px 0;color:#8b949e;">Entry</td><td style="padding:8px 0;text-align:right;">${trade['entry_avg']:,.2f}</td></tr>
                <tr><td style="padding:8px 0;color:#8b949e;">Exit</td><td style="padding:8px 0;text-align:right;">${trade['exit_price']:,.2f}</td></tr>
                <tr><td style="padding:8px 0;color:#8b949e;">Qty</td><td style="padding:8px 0;text-align:right;">{trade['qty']} BTC</td></tr>
                <tr><td style="padding:8px 0;color:#8b949e;">Duration</td><td style="padding:8px 0;text-align:right;">{dur}</td></tr>
                <tr style="border-top:1px solid #30363d;">
                    <td style="padding:12px 0;color:#8b949e;font-weight:bold;">P&L</td>
                    <td style="padding:12px 0;text-align:right;font-size:18px;font-weight:bold;color:{'#22c55e' if trade['pnl_usd']>=0 else '#ef4444'};">
                        {pnl_sign}${trade['pnl_usd']:.2f} ({pnl_sign}{trade['pnl_pct']:.2f}%)
                    </td>
                </tr>
                <tr><td style="padding:8px 0;color:#8b949e;">Reason</td><td style="padding:8px 0;text-align:right;">{trade['reason'].replace('_',' ')}</td></tr>
            </table>
            <hr style="border:none;border-top:1px solid #30363d;margin:16px 0;">
            <h3 style="margin:0 0 8px;color:#8b949e;">Overall Stats</h3>
            <table style="width:100%;border-collapse:collapse;color:#e6edf3;">
                <tr><td style="padding:6px 0;color:#8b949e;">Total Trades</td><td style="padding:6px 0;text-align:right;">{total}</td></tr>
                <tr><td style="padding:6px 0;color:#8b949e;">Win Rate</td><td style="padding:6px 0;text-align:right;">{wins}/{total} ({wr:.1f}%)</td></tr>
                <tr><td style="padding:6px 0;color:#8b949e;">Total P&L</td><td style="padding:6px 0;text-align:right;color:{'#22c55e' if stats.get('total_profit_usd',0)>=0 else '#ef4444'};font-weight:bold;">${stats.get('total_profit_usd',0):+.2f}</td></tr>
                <tr><td style="padding:6px 0;color:#8b949e;">Balance</td><td style="padding:6px 0;text-align:right;font-weight:bold;">${balance:,.2f}</td></tr>
            </table>
            <p style="margin:16px 0 0;color:#484f58;font-size:12px;">BTC MACD+RSI Bot · 2x Leverage · 15m Timeframe</p>
        </div>
        </body></html>"""

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = cfg["sender_email"]
        msg["To"] = cfg["recipient_email"]
        msg.attach(MIMEText(body, "html"))

        with smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"]) as server:
            server.starttls()
            server.login(cfg["sender_email"], cfg["sender_password"])
            server.sendmail(cfg["sender_email"], cfg["recipient_email"], msg.as_string())

        log.info(f"  \u2709 Email sent: {subject}")
    except Exception as e:
        log.warning(f"  Email failed: {e}")


def record_trade(state, symbol, pos, exit_price, reason, pnl_pct, pnl_usd):
    """Record a completed trade."""
    avg, qty, cost = calc_position(pos)
    is_win = pnl_usd > 0

    state["trade_history"].append({
        "symbol": symbol,
        "side": pos["side"],
        "strategy": pos.get("strategy", "BB_RSI_FLIP"),
        "entry_avg": round(avg, 6),
        "exit_price": round(exit_price, 6),
        "qty": round(qty, 8),
        "pnl_pct": round(pnl_pct * 100, 2),
        "pnl_usd": round(pnl_usd, 4),
        "reason": reason,
        "result": "WIN" if is_win else "LOSS",
        "dca_filled": pos.get("dca_filled", 0),
        "entry_reason": pos.get("entry_reason", ""),
        "entry_time": pos.get("start_time", ""),
        "time": datetime.now(timezone.utc).isoformat(),
    })

    s = state["stats"]
    s["total_trades"] += 1
    s["total_profit_usd"] += pnl_usd
    side_key = pos["side"].lower()
    if is_win:
        s["wins"] += 1
        s[f"{side_key}_wins"] += 1
    else:
        s["losses"] += 1
        s[f"{side_key}_losses"] += 1

    # Track exit reason stats
    if "FLIP" in reason:
        s["flip_trades"] = s.get("flip_trades", 0) + 1
    elif "TP" in reason:
        s["tp_trades"] = s.get("tp_trades", 0) + 1
    elif "SL" in reason:
        s["sl_trades"] = s.get("sl_trades", 0) + 1

    # Send email notification
    send_trade_email(state["trade_history"][-1], s, state.get("balance", 0))


def place_protection_orders(client, symbol, pos, current_price):
    """
    Place server-side stop loss on Binance to protect position 24/7.

    Strategy: Signal + SL 5% (no TP)
    - MACD signal flips handle exits (better than fixed TP over 6 months)
    - SL 5% is the safety net for when bot is offline or flash crash

    Places:
    1. STOP_MARKET at hard SL price (-5% PnL = -2.5% price move at 2x)
    2. Fixed TP only if configured (fixed_tp_pct > 0)
    """
    avg_price, total_qty, total_cost = calc_position(pos)
    leverage = CONFIG["leverage"]

    # Guard: if we don't have a valid entry price / qty, skip protection
    if avg_price <= 0 or total_qty <= 0:
        log.warning(f"  Skipping protection orders: invalid avg_price={avg_price} qty={total_qty}")
        pos["protection_placed"] = False
        return

    # Get exchange info to properly round quantity for protection orders
    sym_info = client.get_exchange_info(symbol)
    if sym_info:
        protect_qty = round_qty(total_qty, sym_info["step_size"], sym_info["precision"])
    else:
        protect_qty = total_qty

    # Cancel existing stop orders first (we'll replace them)
    client.cancel_all_orders(symbol)

    close_side = "SELL" if pos["side"] == "LONG" else "BUY"

    # 1. Hard Stop Loss: -5% PnL = price moves 2.5% against us at 2x leverage
    sl_price_move = CONFIG["hard_sl_pct"] / leverage
    if pos["side"] == "LONG":
        sl_price = avg_price * (1 - sl_price_move)
    else:
        sl_price = avg_price * (1 + sl_price_move)

    try:
        result = client.place_stop_market(symbol, close_side, sl_price, quantity=protect_qty)
        if result:
            log.info(f"  ★ Server SL placed: {close_side} {protect_qty} @ ${sl_price:,.2f} (-{CONFIG['hard_sl_pct']*100}% PnL)")
    except Exception as e:
        log.warning(f"  Failed to place server SL: {e}")

    # 2. Fixed TP — only if configured (currently disabled: signals handle exits)
    tp_pct = CONFIG.get("fixed_tp_pct", 0)
    if tp_pct > 0:
        tp_price_move = tp_pct / leverage
        if pos["side"] == "LONG":
            tp_price = avg_price * (1 + tp_price_move)
        else:
            tp_price = avg_price * (1 - tp_price_move)
        try:
            result = client.place_take_profit_market(symbol, close_side, tp_price, quantity=protect_qty)
            if result:
                log.info(f"  ★ Server TP placed: {close_side} {protect_qty} @ ${tp_price:,.2f} (+{tp_pct*100}% PnL)")
        except Exception as e:
            log.warning(f"  Failed to place server TP: {e}")
        pos["tp_price"] = round(tp_price, 2)
    else:
        log.info(f"  ℹ No server TP — MACD signal flips handle exits")

    pos["protection_placed"] = True
    pos["sl_price"] = round(sl_price, 2)


def close_position(client, state, symbol, pos, current_price, reason):
    """Close a position and record the trade.
    Cancels server-side stop orders first, then queries actual Binance position size."""
    # Cancel any server-side stop orders before closing
    client.cancel_all_orders(symbol)

    avg_price, total_qty, total_cost = calc_position(pos)
    pnl_pct, pnl_usd = calc_pnl(pos, current_price)

    sym_info = client.get_exchange_info(symbol)
    if not sym_info:
        log.error(f"  Cannot get exchange info for {symbol}")
        return False

    # FIX: Query actual position size from Binance to avoid mismatch
    actual_qty = total_qty
    try:
        binance_positions = client.get_positions()
        for bp in binance_positions:
            if bp["symbol"] == symbol:
                actual_qty = bp["qty"]
                if abs(actual_qty - total_qty) > sym_info["step_size"]:
                    log.warning(f"  Qty mismatch! Tracked: {total_qty}, Binance: {actual_qty}. Using Binance value.")
                break
    except Exception:
        pass  # Fall back to tracked qty

    close_side = "SELL" if pos["side"] == "LONG" else "BUY"
    close_qty = round_qty(actual_qty, sym_info["step_size"], sym_info["precision"])

    result = client.place_order(symbol, close_side, close_qty, reduce_only=True)
    if result:
        record_trade(state, symbol, pos, current_price, reason, pnl_pct, pnl_usd)
        if symbol in state["positions"]:
            del state["positions"][symbol]
        # Track SL exits for cooldown — skip re-entry for N bars after a stop loss
        if "SL" in reason or "HARD_SL" in reason:
            state["last_sl_time"] = datetime.now(timezone.utc).isoformat()
            log.info(f"  Cooldown activated — will skip entries for {CONFIG.get('cooldown_bars', 4)} bars")
        log.info(f"  Closed {pos['side']} | {reason} | P&L: {pnl_pct*100:+.2f}% (${pnl_usd:+.2f})")
        return True
    return False


def open_position(client, state, symbol, side, current_price, reason, strategy="MACD_RSI"):
    """Open a new position."""
    sym_info = client.get_exchange_info(symbol)
    if not sym_info:
        return False

    # Use actual account balance × deploy % for true compounding
    # Keeps 5% reserve for funding fees + slippage
    try:
        account = client.get_account()
        available_balance = float(account.get("availableBalance", 0))
        if available_balance <= 0:
            # Fallback: use totalWalletBalance
            available_balance = float(account.get("totalWalletBalance", CONFIG["total_capital_usdt"]))
        deploy_capital = available_balance * CONFIG["capital_deploy_pct"]
    except Exception as e:
        log.warning(f"  Could not fetch account balance: {e}, using config capital")
        deploy_capital = CONFIG["total_capital_usdt"] * CONFIG["capital_deploy_pct"]

    order_size = deploy_capital * CONFIG["leverage"]

    order_side = "BUY" if side == "LONG" else "SELL"
    qty = round_qty(order_size / current_price, sym_info["step_size"], sym_info["precision"])

    log.info(f"  Opening {side} | Balance: ${deploy_capital:.2f} × {CONFIG['leverage']}x = ${order_size:.2f} ({qty} {symbol})")

    result = client.place_order(symbol, order_side, qty)
    if not result:
        log.error(f"  Order placement returned empty response")
        return False

    # Parse fill info — market orders on testnet often return avgPrice=0, executedQty=0
    # because the response is sent before the fill is confirmed.
    fill_price = 0.0
    fill_qty = 0.0
    try:
        fill_price = float(result.get("avgPrice", 0) or 0)
        fill_qty = float(result.get("executedQty", 0) or 0)
    except (ValueError, TypeError):
        pass

    # If response didn't include fill info, query actual position from Binance
    if fill_price <= 0 or fill_qty <= 0:
        log.info(f"  Order response had no fill info, querying actual position...")
        time.sleep(0.7)  # Give Binance a moment to register the fill
        try:
            positions = client.get_positions()
            for p in positions:
                if p["symbol"] == symbol:
                    fill_price = p.get("entry_price", 0)
                    fill_qty = p.get("qty", 0)
                    log.info(f"  Found position on Binance: {fill_qty} @ ${fill_price:,.2f}")
                    break
        except Exception as e:
            log.warning(f"  Could not query position after order: {e}")

    # Final fallback: use market price + ordered qty
    if fill_price <= 0:
        fill_price = current_price
        log.warning(f"  Using market price ${current_price:,.2f} as entry")
    if fill_qty <= 0:
        fill_qty = qty
        log.warning(f"  Using ordered qty {qty} as fill")

    state["positions"][symbol] = {
        "side": side,
        "strategy": strategy,
        "orders": [{"price": fill_price, "qty": fill_qty, "type": "BASE",
                    "time": datetime.now(timezone.utc).isoformat()}],
        "peak_pnl": 0,
        "dca_filled": 0,
        "start_time": datetime.now(timezone.utc).isoformat(),
        "entry_reason": reason,
    }
    log.info(f"  ✔ Opened {side} {fill_qty} {symbol} @ ${fill_price:,.2f}")

    # Place server-side protection orders (SL + trailing TP)
    place_protection_orders(client, symbol, state["positions"][symbol], fill_price)
    return True


# ═══════════════════════════════════════════════════════════════
# MODULE 1: MAIN BOT LOOP
# ═══════════════════════════════════════════════════════════════

def run_module1(client, state):
    """BTC 15m MACD+RSI Momentum strategy."""
    symbol = CONFIG["pair"]
    log.info(f"\n{'━'*60}")
    log.info(f"  MODULE 1: BTC 15m MACD+RSI Momentum")
    log.info(f"{'━'*60}")

    try:
        # Setup
        client.set_leverage(symbol, CONFIG["leverage"])
        client.set_margin_type(symbol, "ISOLATED")

        # Get 15m candles
        df = get_candles(client, symbol, CONFIG["interval"], CONFIG["candles_needed"])
        current_price = float(df["close"].iloc[-1])

        # Get signal
        signal, ind, reason = get_15m_signal(df)

        # Get 4H trend filter (multi-timeframe)
        htf_trend = 0
        htf_info = {}
        if CONFIG.get("mtf_enabled", False):
            htf_trend, htf_info = get_htf_trend(client, symbol,
                                                  CONFIG.get("mtf_interval", "4h"),
                                                  CONFIG.get("mtf_candles", 100))
            ind.update(htf_info)  # Add HTF data to indicators for dashboard

        log.info(f"  Price: ${current_price:,.2f}")
        log.info(f"  RSI: {ind['rsi']:.1f} | MACD: {ind.get('macd_line',0):.2f} | Signal: {ind.get('signal_line',0):.2f} | Hist: {ind['macd_hist']:.2f}")
        log.info(f"  ATR: {ind.get('atr',0):.2f} | BB: [{ind['bb_lower']:,.0f} — {ind['bb_mid']:,.0f} — {ind['bb_upper']:,.0f}]")
        if CONFIG.get("mtf_enabled", False):
            trend_label = {1: "BULLISH ↑", -1: "BEARISH ↓", 0: "NEUTRAL ↔"}.get(htf_trend, "?")
            log.info(f"  4H Trend: {trend_label} | 4H MACD: {htf_info.get('htf_hist',0):.2f} | 4H RSI: {htf_info.get('htf_rsi',0):.1f}")
        vol_ratio = (ind.get('volume',0) / ind.get('vol_sma',1) * 100) if ind.get('vol_sma',0) > 0 else 0
        log.info(f"  Volume: {ind.get('volume',0):,.0f} ({vol_ratio:.0f}% of SMA20)")
        log.info(f"  Trade Signal: {signal}" + (f" ({reason})" if reason else ""))

        # Save indicators to state for dashboard display
        state["last_indicators"] = ind

        pos = state["positions"].get(symbol)
        # Skip if position belongs to gainer strategy
        if pos and pos.get("strategy") == "GAINER_SHORT":
            log.info(f"  {symbol} has active GAINER_SHORT position. Skipping Module 1.")
            return

        if pos:
            # ═══════ ACTIVE POSITION — Manage it ═══════
            avg_price, total_qty, total_cost = calc_position(pos)
            pnl_pct, pnl_usd = calc_pnl(pos, current_price)

            if pnl_pct > pos.get("peak_pnl", 0):
                pos["peak_pnl"] = pnl_pct

            peak = pos.get("peak_pnl", 0)
            dca_filled = pos.get("dca_filled", 0)
            start_time = datetime.fromisoformat(pos.get("start_time", datetime.now(timezone.utc).isoformat()))
            hours_held = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600

            log.info(f"  {pos['side']} | Avg: ${avg_price:,.2f} | Hold: {hours_held:.1f}h")
            log.info(f"  P&L: {pnl_pct*100:+.2f}% (${pnl_usd:+.2f}) | Peak: {peak*100:+.2f}%")

            # ── 1. HARD STOP LOSS (-5%) — Always active ──
            if pnl_pct <= -CONFIG["hard_sl_pct"]:
                log.info(f"  ✖ HARD STOP LOSS at {pnl_pct*100:.2f}%")
                close_position(client, state, symbol, pos, current_price, "HARD_SL")
                return

            # ── 2. MAX HOLD TIMEOUT (72h) ──
            if hours_held >= CONFIG["max_hold_hours"]:
                log.info(f"  ⏱ TIMEOUT at {hours_held:.0f}h")
                close_position(client, state, symbol, pos, current_price, "TIMEOUT")
                return

            # ── 3. FIXED TAKE PROFIT ──
            fixed_tp = CONFIG.get("fixed_tp_pct", 0)
            if fixed_tp > 0 and pnl_pct >= fixed_tp:
                log.info(f"  ★ FIXED TP HIT! P&L {pnl_pct*100:+.2f}% >= {fixed_tp*100}% target")
                close_position(client, state, symbol, pos, current_price, "FIXED_TP")
                return

            # ── 3b. TRAILING TAKE PROFIT (disabled if trailing_tp_activate=0) ──
            if CONFIG["trailing_tp_activate"] > 0 and peak >= CONFIG["trailing_tp_activate"]:
                trail_drop = peak - pnl_pct
                log.info(f"  Trail TP active! Peak={peak*100:.2f}%, Drop={trail_drop*100:.2f}%")

                if trail_drop >= CONFIG["trailing_tp_trail"]:
                    log.info(f"  ★ TRAILING TP! Locking {pnl_pct*100:+.2f}%")
                    close_position(client, state, symbol, pos, current_price, "TRAIL_TP")
                    return

            # ── 4. TRAILING STOP LOSS (step-based) ──
            # Moves SL up as trade profits — never let a winner become a big loser
            tsl_act = CONFIG.get("tsl_activate", 0)
            if tsl_act > 0 and pnl_pct >= tsl_act:
                levels_above = int(pnl_pct / tsl_act)
                tsl_floor = max(0, (levels_above - 1) * tsl_act)
                log.info(f"  TSL active! Profit={pnl_pct*100:.2f}%, Floor={tsl_floor*100:.1f}%")
                if pnl_pct <= tsl_floor:
                    log.info(f"  ★ TRAILING SL! Locking {pnl_pct*100:+.2f}% (floor was {tsl_floor*100:.1f}%)")
                    close_position(client, state, symbol, pos, current_price, f"TRAIL_SL({tsl_floor*100:.1f}%)")
                    return

            # ── 5. ATR TRAILING STOP (volatility-based) ──
            if CONFIG.get("use_atr_stop"):
                atr_val = ind.get("atr", 0)
                if atr_val > 0:
                    peak_price = pos.get("peak_price", avg_price)
                    # Update peak price
                    if pos["side"] == "LONG":
                        peak_price = max(peak_price, current_price)
                    else:
                        peak_price = min(peak_price, current_price)
                    pos["peak_price"] = peak_price

                    atr_stop_dist = atr_val * CONFIG["atr_stop_mult"]
                    if pos["side"] == "LONG" and current_price < peak_price - atr_stop_dist:
                        log.info(f"  ✖ ATR STOP at ${current_price:,.2f} (peak ${peak_price:,.2f} - {atr_stop_dist:.2f})")
                        close_position(client, state, symbol, pos, current_price, "ATR_STOP")
                        return
                    if pos["side"] == "SHORT" and current_price > peak_price + atr_stop_dist:
                        log.info(f"  ✖ ATR STOP at ${current_price:,.2f} (peak ${peak_price:,.2f} + {atr_stop_dist:.2f})")
                        close_position(client, state, symbol, pos, current_price, "ATR_STOP")
                        return

            # ── 6. SIGNAL FLIP ──
            should_flip = (
                (pos["side"] == "LONG" and signal == "SHORT") or
                (pos["side"] == "SHORT" and signal == "LONG")
            )

            if should_flip:
                # ── MTF filter on flip ──
                mtf_blocked = False
                if CONFIG.get("mtf_enabled", False) and htf_trend != 0:
                    if signal == "LONG" and htf_trend == -1:
                        mtf_blocked = True
                    elif signal == "SHORT" and htf_trend == 1:
                        mtf_blocked = True

                if mtf_blocked:
                    # OPTION B: Position is aligned with 4H trend, opposite signal is noise
                    # HOLD the position — don't close on counter-trend signals
                    log.info(f"  ⟳ FLIP BLOCKED: {signal} signal blocked by 4H trend — HOLDING {pos['side']} (trusting 4H trend)")
                else:
                    log.info(f"  ⟳ FLIP! {pos['side']} → {signal} ({reason})")
                    # Close current position and open opposite (both aligned with 4H)
                    if close_position(client, state, symbol, pos, current_price, f"FLIP_{pos['side']}_to_{signal}"):
                        # Open opposite
                        open_position(client, state, symbol, signal, current_price, reason)
                return

            # ── Holding ──
            log.info(f"  Holding {pos['side']} | Waiting for TP/Flip/Exit")

        else:
            # ═══════ NO POSITION — Check for entry ═══════
            if signal in ("LONG", "SHORT"):
                # ── Cooldown check: skip entry for N bars after a stop loss ──
                cooldown_bars = CONFIG.get("cooldown_bars", 0)
                if cooldown_bars > 0 and "last_sl_time" in state:
                    last_sl = datetime.fromisoformat(state["last_sl_time"])
                    # Calculate bar duration from interval config
                    interval_mins = {"1m":1,"3m":3,"5m":5,"15m":15,"30m":30,"1h":60,"2h":120,"4h":240}.get(CONFIG["interval"], 15)
                    cooldown_mins = cooldown_bars * interval_mins
                    elapsed = (datetime.now(timezone.utc) - last_sl).total_seconds() / 60
                    if elapsed < cooldown_mins:
                        remaining = cooldown_mins - elapsed
                        log.info(f"  ⏸ COOLDOWN: Skipping {signal} signal — {remaining:.0f}m left after SL")
                        return

                # ── Volume filter: skip entry when volume too low (avoid fake/thin moves) ──
                if CONFIG.get("vol_filter_enabled", False):
                    curr_vol = ind.get("volume", 0)
                    vol_sma = ind.get("vol_sma", 0)
                    min_ratio = CONFIG.get("vol_min_ratio", 0.8)
                    if vol_sma > 0 and curr_vol < vol_sma * min_ratio:
                        vol_pct = (curr_vol / vol_sma * 100) if vol_sma > 0 else 0
                        log.info(f"  ⏸ LOW VOLUME: Skipping {signal} — vol={curr_vol:.0f} ({vol_pct:.0f}% of SMA, need {min_ratio*100:.0f}%)")
                        return

                # ── MTF filter on new entry ──
                if CONFIG.get("mtf_enabled", False) and htf_trend != 0:
                    if signal == "LONG" and htf_trend == -1:
                        log.info(f"  ⏸ MTF FILTER: {signal} blocked — 4H trend is BEARISH")
                        return
                    elif signal == "SHORT" and htf_trend == 1:
                        log.info(f"  ⏸ MTF FILTER: {signal} blocked — 4H trend is BULLISH")
                        return

                log.info(f"  ★ NEW {signal} SIGNAL! ({reason})")
                open_position(client, state, symbol, signal, current_price, reason)
            else:
                log.info(f"  No position, no signal. Waiting...")

    except Exception as e:
        log.error(f"  Module 1 error: {e}")
        import traceback
        traceback.print_exc()


# ═══════════════════════════════════════════════════════════════
# MODULE 2: TOP GAINER SHORT SCANNER
# (same as v1 with hard SL + max hold fixes)
# ═══════════════════════════════════════════════════════════════

def scan_top_gainers(client):
    """Scan for coins pumped >15% in 24h."""
    gainers = []
    try:
        tickers = client._request("GET", "/fapi/v1/ticker/24hr")
        for t in tickers:
            sym = t["symbol"]
            if sym not in GAINER_CONFIG["scan_pairs"]:
                continue
            change_pct = float(t.get("priceChangePercent", 0))
            if change_pct >= GAINER_CONFIG["min_24h_gain"] * 100:
                gainers.append({
                    "symbol": sym,
                    "change_24h": change_pct,
                    "price": float(t["lastPrice"]),
                    "volume": float(t.get("quoteVolume", 0)),
                })
    except Exception as e:
        log.error(f"Gainer scan failed: {e}")
    gainers.sort(key=lambda x: x["change_24h"], reverse=True)
    return gainers


def check_exhaustion_signal(client, symbol):
    """Check if a pumped coin shows exhaustion signals for shorting."""
    try:
        df = get_candles(client, symbol, "1h", 60)
        rsi_vals = compute_rsi(df, 14)
        bb_upper, bb_mid, bb_lower = compute_bb(df, 20, 2.0)

        curr_price = float(df["close"].iloc[-1])
        curr_rsi = float(rsi_vals.iloc[-1])
        curr_bb_u = float(bb_upper.iloc[-1])

        if pd.isna(curr_rsi) or pd.isna(curr_bb_u):
            return False, ""

        rsi_was_high = False
        for j in range(-12, 0):
            if not pd.isna(rsi_vals.iloc[j]) and float(rsi_vals.iloc[j]) > GAINER_CONFIG["rsi_was_high_threshold"]:
                rsi_was_high = True
                break

        if not rsi_was_high:
            return False, ""
        if curr_rsi >= GAINER_CONFIG["rsi_entry_below"]:
            return False, ""
        if curr_price >= curr_bb_u:
            return False, ""

        red_count = sum(1 for j in range(-3, 0)
                        if float(df["close"].iloc[j]) < float(df["open"].iloc[j]))

        if red_count < GAINER_CONFIG["min_red_candles"]:
            return False, ""

        reason = f"EXHAUSTION(RSI={curr_rsi:.0f},belowBB,{red_count}red)"
        return True, reason

    except Exception as e:
        log.error(f"  Exhaustion check failed for {symbol}: {e}")
        return False, ""


def run_module2(client, state):
    """Top Gainer Short Scanner."""
    log.info(f"\n{'═'*60}")
    log.info("  MODULE 2: TOP GAINER SHORT SCANNER")
    log.info(f"{'═'*60}")

    gainer_positions = {k: v for k, v in state["positions"].items()
                        if v.get("strategy") == "GAINER_SHORT"}

    # ── Manage existing gainer positions ──
    for symbol, pos in list(gainer_positions.items()):
        try:
            current_price = client.get_price(symbol)
            pnl_pct, pnl_usd = calc_pnl(pos, current_price)
            avg_price, total_qty, total_cost = calc_position(pos)

            if pnl_pct > pos.get("peak_pnl", 0):
                pos["peak_pnl"] = pnl_pct
            peak = pos.get("peak_pnl", 0)
            dca_filled = pos.get("dca_filled", 0)

            start_time = datetime.fromisoformat(pos.get("start_time", datetime.now(timezone.utc).isoformat()))
            hours_held = (datetime.now(timezone.utc) - start_time).total_seconds() / 3600

            log.info(f"  {symbol} SHORT | P&L: {pnl_pct*100:+.2f}% | Peak: {peak*100:.2f}% | Hold: {hours_held:.0f}h")

            # Hard SL
            if pnl_pct <= -GAINER_CONFIG["hard_sl_pct"]:
                log.info(f"  ✖ HARD SL at {pnl_pct*100:.2f}%")
                close_position(client, state, symbol, pos, current_price, "GAINER_HARD_SL")
                continue

            # Timeout
            if hours_held >= GAINER_CONFIG["max_hold_hours"]:
                log.info(f"  ⏱ TIMEOUT at {hours_held:.0f}h")
                close_position(client, state, symbol, pos, current_price, "GAINER_TIMEOUT")
                continue

            # Trailing TP
            if peak >= GAINER_CONFIG["trailing_tp_activate"]:
                if peak - pnl_pct >= GAINER_CONFIG["trailing_tp_trail"]:
                    log.info(f"  ★ GAINER TP! {pnl_pct*100:+.2f}%")
                    close_position(client, state, symbol, pos, current_price, "GAINER_TP")
                    continue

            # DCA
            if dca_filled < len(GAINER_CONFIG["dca_levels"]):
                dca_cfg = GAINER_CONFIG["dca_levels"][dca_filled]
                first_entry = pos["orders"][0]["price"]
                trigger = first_entry * (1 + dca_cfg["deviation"])
                if current_price >= trigger:
                    sym_info = client.get_exchange_info(symbol)
                    if sym_info:
                        dca_amount = GAINER_CONFIG["capital_per_trade"] * dca_cfg["multiplier"]
                        dca_qty = round_qty(dca_amount / current_price, sym_info["step_size"], sym_info["precision"])
                        result = client.place_order(symbol, "SELL", dca_qty)
                        if result:
                            fill_p = float(result.get("avgPrice", current_price))
                            fill_q = float(result.get("executedQty", dca_qty))
                            pos["orders"].append({"price": fill_p, "qty": fill_q, "type": f"DCA{dca_filled+1}",
                                                  "time": datetime.now(timezone.utc).isoformat()})
                            pos["dca_filled"] = dca_filled + 1
                            log.info(f"  ▼ Gainer DCA #{dca_filled+1}")

            # Emergency SL after DCA exhausted
            if dca_filled >= len(GAINER_CONFIG["dca_levels"]) and pnl_pct <= -GAINER_CONFIG["emergency_sl_pct"]:
                log.info(f"  ✖ GAINER SL at {pnl_pct*100:.2f}%")
                close_position(client, state, symbol, pos, current_price, "GAINER_SL")

        except Exception as e:
            log.error(f"  Gainer error {symbol}: {e}")

    # ── Scan for new gainers ──
    if len(gainer_positions) >= GAINER_CONFIG["max_gainer_positions"]:
        log.info(f"  Max gainer positions ({GAINER_CONFIG['max_gainer_positions']}). Skip scan.")
        return

    gainers = scan_top_gainers(client)
    if not gainers:
        log.info("  No coins up >15% in 24h.")
        return

    log.info(f"  Found {len(gainers)} gainers:")
    for g in gainers[:10]:
        log.info(f"    {g['symbol']}: +{g['change_24h']:.1f}% | ${g['price']:.4f}")

    slots = GAINER_CONFIG["max_gainer_positions"] - len(gainer_positions)
    for g in gainers[:5]:
        if slots <= 0:
            break
        symbol = g["symbol"]
        if symbol in state["positions"]:
            continue

        exhausted, reason = check_exhaustion_signal(client, symbol)
        if exhausted:
            log.info(f"  ★ SHORTING {symbol} — {reason}")
            try:
                client.set_leverage(symbol, GAINER_CONFIG["leverage"])
                client.set_margin_type(symbol, "ISOLATED")
                sym_info = client.get_exchange_info(symbol)
                if not sym_info:
                    continue

                qty = round_qty(GAINER_CONFIG["capital_per_trade"] / g["price"],
                               sym_info["step_size"], sym_info["precision"])
                result = client.place_order(symbol, "SELL", qty)
                if result:
                    fill_p = float(result.get("avgPrice", g["price"]))
                    fill_q = float(result.get("executedQty", qty))
                    state["positions"][symbol] = {
                        "side": "SHORT",
                        "strategy": "GAINER_SHORT",
                        "orders": [{"price": fill_p, "qty": fill_q, "type": "BASE",
                                    "time": datetime.now(timezone.utc).isoformat()}],
                        "peak_pnl": 0,
                        "dca_filled": 0,
                        "start_time": datetime.now(timezone.utc).isoformat(),
                        "entry_reason": reason,
                        "gain_24h": g["change_24h"],
                    }
                    slots -= 1
                    log.info(f"  Opened SHORT {fill_q} {symbol} @ ${fill_p:.4f}")
            except Exception as e:
                log.error(f"  Failed to short {symbol}: {e}")
        else:
            log.info(f"  {symbol}: +{g['change_24h']:.1f}% — Not exhausted yet")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def run_bot():
    log.info("=" * 60)
    log.info(f"FUTURES BOT v3: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    log.info(f"Module 1: BTC 15m MACD+RSI Momentum (2x) — NO DCA")
    log.info(f"Module 2: Top Gainer Short Scanner (2x)")
    log.info("=" * 60)

    state = load_state()
    client = FuturesClient(CONFIG["api_key"], CONFIG["api_secret"], CONFIG["base_url"])

    # Check account
    try:
        account = client.get_account()
        total_balance = float(account.get("totalWalletBalance", 0))
        available = float(account.get("availableBalance", 0))
        log.info(f"Balance: ${total_balance:.2f} | Available: ${available:.2f}")
        # Save balance to state so dashboard can display it
        state["balance"] = round(total_balance, 2)
        state["available_balance"] = round(available, 2)
    except Exception as e:
        log.warning(f"Account check failed: {e} — continuing with last known state")
        # Don't exit — still manage existing positions and check signals

    # ── Sync Check: compare state with actual Binance positions ──
    try:
        binance_positions = client.get_positions()
        binance_syms = {p["symbol"] for p in binance_positions}
        state_syms = set(state["positions"].keys())

        # Positions on Binance but not in state (orphaned from crash)
        orphaned = binance_syms - state_syms
        if orphaned:
            log.warning(f"  ⚠ Orphaned positions on Binance (not in state): {orphaned}")
            log.warning(f"  These may be from a crashed flip. Review manually or they'll be managed next run.")
            for bp in binance_positions:
                if bp["symbol"] in orphaned:
                    log.warning(f"    {bp['symbol']}: {bp['side']} {bp['qty']} @ ${bp['entry_price']:.4f} | uPnL: ${bp['unrealized_pnl']:.2f}")
                    # Auto-adopt orphaned position into state
                    state["positions"][bp["symbol"]] = {
                        "side": bp["side"],
                        "strategy": "RECOVERED",
                        "orders": [{"price": bp["entry_price"], "qty": bp["qty"], "type": "RECOVERED",
                                    "time": datetime.now(timezone.utc).isoformat()}],
                        "peak_pnl": 0,
                        "dca_filled": 0,
                        "start_time": datetime.now(timezone.utc).isoformat(),
                        "entry_reason": "Recovered from Binance (orphaned position)",
                    }
                    log.info(f"    Adopted {bp['symbol']} into state for management.")

        # Positions in state but not on Binance (already closed externally)
        ghost = state_syms - binance_syms
        for sym in ghost:
            if sym in state["positions"]:
                log.warning(f"  ⚠ Ghost position {sym} in state but not on Binance. Removing.")
                del state["positions"][sym]

    except Exception as e:
        log.warning(f"  Sync check failed: {e}. Continuing with state file.")

    # Run Module 1: BTC 15m Scalper
    run_module1(client, state)

    # Run Module 2: Gainer Short Scanner
    try:
        run_module2(client, state)
    except Exception as e:
        log.error(f"Module 2 error: {e}")

    # ─── Summary ───
    log.info(f"\n{'='*60}")
    log.info("BOT SUMMARY:")
    active = len(state["positions"])
    log.info(f"  Active positions: {active}")
    for sym, pos in state["positions"].items():
        avg, qty, cost = calc_position(pos)
        strategy = pos.get("strategy", "BB_RSI_FLIP")
        try:
            price = client.get_price(sym)
            pnl_pct, pnl_usd = calc_pnl(pos, price)
            log.info(f"    {sym} {pos['side']} ({strategy}) | Avg=${avg:,.2f} | P&L={pnl_pct*100:+.2f}% (${pnl_usd:+.2f})")
        except Exception:
            log.info(f"    {sym} {pos['side']} ({strategy}) | Avg=${avg:,.2f}")

    s = state["stats"]
    if s["total_trades"] > 0:
        wr = s["wins"] / s["total_trades"] * 100
        log.info(f"  Trades: {s['total_trades']} | WR: {wr:.1f}% | P&L: ${s['total_profit_usd']:.2f}")
        log.info(f"  Long W/L: {s['long_wins']}/{s['long_losses']} | Short W/L: {s['short_wins']}/{s['short_losses']}")
        log.info(f"  Exits — TP: {s.get('tp_trades',0)} | Flip: {s.get('flip_trades',0)} | SL: {s.get('sl_trades',0)}")
    else:
        log.info(f"  No completed trades yet.")

    log.info("=" * 60)
    save_state(state)
    log.info("State saved.\n")


if __name__ == "__main__":
    import sys

    if "--test" in sys.argv:
        log.info("TEST MODE — Checking connectivity")
        client = FuturesClient(CONFIG["api_key"], CONFIG["api_secret"], CONFIG["base_url"])
        try:
            acc = client.get_account()
            bal = float(acc.get("totalWalletBalance", 0))
            log.info(f"Connected! Balance: ${bal:.2f}")
            for sym in [CONFIG["pair"]] + GAINER_CONFIG["scan_pairs"][:3]:
                p = client.get_price(sym)
                log.info(f"  {sym}: ${p:,.4f}")
            log.info("Connectivity PASSED!")
        except Exception as e:
            log.error(f"Failed: {e}")
            log.info("Get Futures Testnet API keys: https://testnet.binancefuture.com")

    elif "--dry-run" in sys.argv:
        log.info("DRY RUN — Checking signals without trading")
        client = FuturesClient(CONFIG["api_key"], CONFIG["api_secret"], CONFIG["base_url"])
        try:
            df = get_candles(client, CONFIG["pair"], CONFIG["interval"], CONFIG["candles_needed"])
            signal, ind, reason = get_15m_signal(df)
            log.info(f"BTC 15m Signal: {signal}")
            log.info(f"  Reason: {reason}")
            log.info(f"  Price: ${ind['price']:,.2f}")
            log.info(f"  RSI: {ind['rsi']:.1f}")
            log.info(f"  MACD: {ind.get('macd_line',0):.2f} | Signal: {ind.get('signal_line',0):.2f} | Hist: {ind['macd_hist']:.2f}")
            log.info(f"  ATR: {ind.get('atr',0):.2f}")
            log.info(f"  BB: [{ind['bb_lower']:,.0f} — {ind['bb_mid']:,.0f} — {ind['bb_upper']:,.0f}]")
        except Exception as e:
            log.error(f"Dry run failed: {e}")

    else:
        run_bot()
