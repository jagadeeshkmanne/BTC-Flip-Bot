"""core_engine.py — Shared paper-trading engine for the regime/pullback bots.

Used by bot_gemini.py, bot_chatgpt.py, bot_claude.py. Unlike the rsiscalp DCA
engine, this models SINGLE-entry positions with:
  - a hard stop (fixed %, swing-based, or ATR-based — the bot decides)
  - a list of scaled take-profit targets (partial exits, e.g. 1R / 2R)
  - a trailing stop the bot ratchets each tick (e.g. behind EMA20 or by ATR)

The engine is deliberately "dumb": it owns state I/O, fills, fees, stats, the
circuit breaker, equity/DD tracking and status.json. The STRATEGY (each bot)
computes all signals/levels and calls book.open / book.partial / book.close /
book.update_sl. This keeps the risky math in one tested place while each bot
stays a readable strategy file.

PAPER-ONLY — public Binance futures endpoints, virtual balance, no API keys.
"""
from __future__ import annotations
import os, json, logging, sys
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import numpy as np

BINANCE_BASE = "https://fapi.binance.com"
COMMISSION_PCT = 0.0004  # 0.04% taker per side (paper assumption)


# ═════════════════════ indicators ═════════════════════
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    gain = d.clip(lower=0); loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    al = loss.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff(); dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    a = tr.ewm(alpha=1.0/n, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1.0/n, adjust=False).mean() / a
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1.0/n, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0/n, adjust=False).mean()


def bollinger(s: pd.Series, n: int = 20, k: float = 2.0):
    mid = s.rolling(n).mean()
    sd = s.rolling(n).std()
    return mid - k * sd, mid, mid + k * sd


# ═════════════════════ data fetch ═════════════════════
def fetch_klines(pair: str, interval: str, limit: int = 500, log=None) -> "pd.DataFrame | None":
    try:
        r = requests.get(f"{BINANCE_BASE}/fapi/v1/klines",
                         params={"symbol": pair, "interval": interval, "limit": limit}, timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        return pd.DataFrame([{
            "timestamp": pd.to_datetime(k[0], unit="ms"),
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]),
        } for k in data])
    except Exception as e:
        if log: log.error(f"klines fetch failed ({interval}): {e}")
        return None


def fetch_live_price(pair: str, log=None) -> "float | None":
    try:
        r = requests.get(f"{BINANCE_BASE}/fapi/v1/ticker/price", params={"symbol": pair}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as e:
        if log: log.error(f"live price fetch failed: {e}")
        return None


# ═════════════════════ candle helpers ═════════════════════
def is_bullish(bar, prev) -> bool:
    """Green momentum candle: closes up and through the prior bar's high."""
    return bar["close"] > bar["open"] and bar["close"] >= prev["high"]


def is_bearish(bar, prev) -> bool:
    return bar["close"] < bar["open"] and bar["close"] <= prev["low"]


def swing_low(df: pd.DataFrame, lookback: int = 6) -> float:
    return float(df["low"].iloc[-lookback:].min())


def swing_high(df: pd.DataFrame, lookback: int = 6) -> float:
    return float(df["high"].iloc[-lookback:].max())


# ═════════════════════ logger ═════════════════════
def make_logger(name: str, log_file: str):
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    log.handlers.clear()
    fh = logging.FileHandler(log_file)
    fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(sh)
    return log


# ═════════════════════ paper book ═════════════════════
class PaperBook:
    """Single-entry paper position manager with scaled TPs + trailing stop."""

    def __init__(self, data_dir: str, strategy_name: str, log,
                 initial: float = 5000.0, leverage: float = 3.0,
                 breaker_losses: int = 1, breaker_pause_hours: float = 0.25):
        # cooldown default (user 2026-06-04): 15-min pause after EVERY loss
        self.dir = data_dir
        self.name = strategy_name
        self.log = log
        self.initial = initial
        self.leverage = leverage
        self.breaker_losses = breaker_losses
        self.breaker_pause_hours = breaker_pause_hours
        os.makedirs(data_dir, exist_ok=True)
        self.state_file = os.path.join(data_dir, "state.json")
        self.status_file = os.path.join(data_dir, "status.json")
        self.state = self._load()

    # ── state I/O ──
    def _load(self):
        if not os.path.exists(self.state_file):
            return {"balance": self.initial, "peak_equity": self.initial,
                    "position": None, "stats": {"total": 0, "wins": 0, "pnl": 0.0},
                    "trade_log": [], "consec_losses": 0}
        with open(self.state_file) as f:
            return json.load(f)

    def save(self):
        with open(self.state_file, "w") as f:
            json.dump(self.state, f, default=str, indent=2)

    @property
    def balance(self):
        return self.state["balance"]

    @property
    def position(self):
        return self.state.get("position")

    # ── sizing ──
    def qty_for_risk(self, entry_px: float, sl_px: float, risk_pct: float) -> float:
        """Size so a stop-out loses risk_pct of balance, capped at leverage notional."""
        risk_per_unit = abs(entry_px - sl_px)
        if risk_per_unit <= 0:
            return 0.0
        qty_risk = (self.balance * risk_pct) / risk_per_unit
        qty_cap = (self.balance * 0.95 * self.leverage) / entry_px
        return round(min(qty_risk, qty_cap), 3)

    def qty_for_notional(self, entry_px: float) -> float:
        return round((self.balance * 0.95 * self.leverage) / entry_px, 3)

    # ── breaker ──
    def paused(self) -> bool:
        pu = self.state.get("pause_until")
        if not pu:
            return False
        try:
            return datetime.now(timezone.utc) < datetime.fromisoformat(pu)
        except Exception:
            return False

    # ── open / partial / close ──
    def open(self, side: str, entry_px: float, qty: float, sl_px: float,
             tp_targets: list, meta: dict):
        if qty <= 0:
            self.log.warning(f"  qty {qty} too small — skip open")
            return
        self.state["balance"] -= entry_px * qty * COMMISSION_PCT
        self.state["position"] = {
            "side": side, "entry": entry_px, "qty": qty, "qty_total": qty,
            "sl": sl_px, "init_sl": sl_px, "R": abs(entry_px - sl_px),
            "tp_targets": [{"px": t["px"], "frac": t["frac"], "done": False} for t in tp_targets],
            "trail_active": False, "best": entry_px,
            "entry_time": datetime.now(timezone.utc).isoformat(), "meta": meta,
            # compat fields for server.py _query_paper_position (single-entry bot)
            "first_entry": entry_px, "worst_entry": entry_px, "filled": 1,
            "entries": [{"px": entry_px, "qty": qty}],
        }
        self.log.warning(f"  OPEN {side} {qty}@${entry_px:.2f} SL ${sl_px:.2f} "
                         f"({meta.get('regime','?')}/{meta.get('reason','?')}) | bal ${self.balance:.2f}")

    def update_sl(self, new_sl: float):
        pos = self.position
        if not pos:
            return
        side = pos["side"]
        # only ever tighten (ratchet) in the favorable direction
        if side == "LONG" and new_sl > pos["sl"]:
            pos["sl"] = new_sl; pos["trail_active"] = True
        elif side == "SHORT" and new_sl < pos["sl"]:
            pos["sl"] = new_sl; pos["trail_active"] = True

    def _book_pnl(self, pos, exit_px, qty):
        side = pos["side"]
        gross = (exit_px - pos["entry"]) * qty if side == "LONG" else (pos["entry"] - exit_px) * qty
        fees = exit_px * qty * COMMISSION_PCT
        return gross - fees

    def _record(self, pos, exit_px, qty, reason, net):
        bal_before = self.balance
        self.state["balance"] += net
        side = pos["side"]
        move = (exit_px / pos["entry"] - 1) * 100 * (1 if side == "LONG" else -1)
        self.state.setdefault("trade_log", []).append({
            "side": side, "entry": pos["entry"], "exit": exit_px, "qty": qty,
            "reason": reason, "pnl_usd": net, "pnl_pct": net / bal_before * 100 if bal_before else 0,
            "price_move_pct": move, "regime": pos["meta"].get("regime"),
            "entry_time": pos.get("entry_time"),
            "exit_time": datetime.now(timezone.utc).isoformat(),
        })
        self.state["trade_log"] = self.state["trade_log"][-200:]

    def partial(self, exit_px: float, fraction: float, reason: str):
        pos = self.position
        if not pos:
            return
        qty = round(pos["qty"] * fraction, 3)
        if qty <= 0:
            return
        net = self._book_pnl(pos, exit_px, qty)
        self._record(pos, exit_px, qty, reason, net)
        pos["qty"] = round(pos["qty"] - qty, 3)
        pos["qty_total"] = pos["qty"]
        pos["entries"] = [{"px": pos["entry"], "qty": pos["qty"]}]
        # partials count as their own (winning/losing) booked trade for stats
        self.state["stats"]["total"] += 1
        self.state["stats"]["pnl"] += net / self.initial * 100
        if net > 0:
            self.state["stats"]["wins"] += 1
        self.log.warning(f"  PARTIAL {reason}: {fraction*100:.0f}% ({qty}) @${exit_px:.2f} "
                         f"net ${net:+.2f} | remaining {pos['qty']}")

    def close(self, exit_px: float, reason: str):
        pos = self.position
        if not pos:
            return
        qty = pos["qty"]
        net = self._book_pnl(pos, exit_px, qty) if qty > 0 else 0.0
        if qty > 0:
            self._record(pos, exit_px, qty, reason, net)
            self.state["stats"]["total"] += 1
            self.state["stats"]["pnl"] += net / self.initial * 100
            if net > 0:
                self.state["stats"]["wins"] += 1
        # circuit breaker on the FINAL leg's result
        if net <= 0:
            self.state["consec_losses"] = self.state.get("consec_losses", 0) + 1
            if self.state["consec_losses"] >= self.breaker_losses:
                until = datetime.now(timezone.utc) + timedelta(hours=self.breaker_pause_hours)
                self.state["pause_until"] = until.isoformat()
                self.state["consec_losses"] = 0
                self.log.warning(f"  CIRCUIT BREAKER: {self.breaker_losses} losses — pause until {until.isoformat()[:16]}")
        else:
            self.state["consec_losses"] = 0
        self.log.warning(f"  CLOSE {pos['side']} via {reason} @${exit_px:.2f} net ${net:+.2f} | bal ${self.balance:.2f}")
        self.state["position"] = None

    # ── equity / status ──
    def mark_equity(self):
        if self.balance > self.state.get("peak_equity", 0):
            self.state["peak_equity"] = self.balance
        peak = self.state.get("peak_equity", self.balance)
        return (self.balance / peak - 1) if peak > 0 else 0.0

    def write_status(self, pair, close_px, live_px, signal, indicators, regime,
                     strategy_desc, block_reason=None):
        pos = self.position
        peak = self.state.get("peak_equity", self.balance)
        pos_status = None
        if pos:
            fav = ((live_px - pos["entry"]) / pos["entry"] * 100) * (1 if pos["side"] == "LONG" else -1)
            nxt = next((t for t in pos["tp_targets"] if not t["done"]), None)
            pos_status = {
                "side": pos["side"], "first_entry": pos["entry"], "avg_entry": pos["entry"],
                "worst_entry": pos["entry"], "qty_total": pos["qty"], "filled": 1,
                "sl_px": pos["sl"], "tp_px": nxt["px"] if nxt else None,
                "fav_pct": fav, "entry_time": pos.get("entry_time"),
                "regime": pos["meta"].get("regime"),
            }
        with open(self.status_file, "w") as f:
            json.dump({
                "env": os.path.basename(self.dir), "pair": pair,
                "price": close_px, "live_price": live_px,
                "balance": self.balance, "peak_equity": peak,
                "drawdown_pct": (self.balance / peak - 1) if peak > 0 else 0.0,
                "position": pos_status, "signal": signal, "regime": regime,
                "indicators": indicators, "block_reason": block_reason,
                "stats": self.state["stats"], "strategy": strategy_desc,
                "paper_mode": True, "state": "IN_POSITION" if pos else "FLAT",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }, f, default=str, indent=2)

    def stats_line(self):
        s = self.state["stats"]
        wr = (s["wins"] / s["total"] * 100) if s["total"] else 0.0
        tot = (self.balance / self.initial - 1) * 100
        return f"  Stats: {s['total']} legs | WR {wr:.0f}% | PnL {tot:+.2f}%"
