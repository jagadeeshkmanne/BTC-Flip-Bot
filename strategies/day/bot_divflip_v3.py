#!/usr/bin/env python3
"""bot_divflip_v3.py — Divflip v3 paper bot (LOCKED v4.2 config).

New since v1/v1f:
  - 3 DCAs at 0.2%/0.4% spacing, sizing 1:2:4 progressive
  - FLIP exit (close on opposite divergence + min +0.2% profit)
  - L3 lock at +0.2% from avg
  - 5 filters: BB squeeze + today_change + ADX + min ATR + HTF bias
  - Oversold override (bypass HTF in extreme reversal setups)
  - 1h cooldown after SL, no time stop

State / log / status: data/paper_divflip_v3/
Dashboard URL: ?strategy=divflip_v3
"""
from __future__ import annotations
import os, sys, json, time, logging
from datetime import datetime, timezone, timedelta
import requests
import pandas as pd
import numpy as np

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(os.path.dirname(STRATEGY_DIR))
sys.path.insert(0, STRATEGY_DIR)

from core import build_features, detect_divergence, rsi_series
from core_divflip_v3 import (
    LEVERAGE, RISK_PCT, DCA_LEVELS, DCA_SPACING_L2_PCT, DCA_SPACING_L3_PCT,
    DCA_RATIOS, SL_FROM_L1, FLIP_MIN_PROFIT, L3_LOCK_PCT, SL_COOLDOWN_HOURS,
    DIV_PIVOT_L, DIV_PIVOT_R, DIV_FRESH_BARS, RSI_PERIOD,
    RSI_LONG_MAX, RSI_SHORT_MIN,
    USE_BB_SQUEEZE, BB_SQUEEZE_RANK,
    USE_TODAY_CHANGE, TODAY_LIMIT_PCT,
    USE_ADX_CAP, ADX_MAX,
    USE_MIN_ATR, MIN_ATR_PCT,
    USE_HTF_BIAS, HTF_SLOPE_THR,
    USE_OVERSOLD_OVERRIDE, OVERRIDE_RSI, OVERRIDE_RP,
    evaluate_signal_v3, dca_price, sl_price_v3, l3_lock_price, per_level_qty,
)

# ─── Paths ───
DATA_DIR = os.path.join(BOT_DIR, "data", "paper_divflip_v3")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE  = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE    = os.path.join(DATA_DIR, "bot.log")

# ─── Config ───
PAIR = "BTCUSDT"
INITIAL_BALANCE = 5000.0
COMMISSION_PCT = 0.0004
# v3 uses 15m bars to match backtest calibration.
# All thresholds (MIN_ATR_PCT=0.30%, BB_SQUEEZE_RANK=0.20, etc.) were calibrated
# on 15m data — 5m would have ATR 3× smaller and never satisfy MIN_ATR_PCT.
TIMEFRAME = "15m"
RP_LOOKBACK_BARS = 96    # 1-day on 15m bars (24h × 4 bars/h)
BINANCE_BASE = "https://fapi.binance.com"

# ─── Logging ───
log = logging.getLogger("bot_divflip_v3")
log.setLevel(logging.INFO)
log.handlers.clear()
fh = logging.FileHandler(LOG_FILE)
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(fh)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(sh)


# ─── Public Binance fetchers ───
def fetch_klines(interval: str, limit: int = 500) -> pd.DataFrame | None:
    try:
        r = requests.get(f"{BINANCE_BASE}/fapi/v1/klines",
                         params={"symbol": PAIR, "interval": interval, "limit": limit},
                         timeout=10)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        rows = [{
            "timestamp": pd.to_datetime(k[0], unit="ms"),
            "open": float(k[1]), "high": float(k[2]), "low": float(k[3]),
            "close": float(k[4]), "volume": float(k[5]),
        } for k in data]
        return pd.DataFrame(rows)
    except Exception as e:
        log.error(f"fetch_klines({interval}) failed: {e}")
        return None


def fetch_live_price() -> float | None:
    try:
        r = requests.get(f"{BINANCE_BASE}/fapi/v1/ticker/price",
                         params={"symbol": PAIR}, timeout=5)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as e:
        log.error(f"fetch_live_price failed: {e}")
        return None


# ─── State I/O ───
def load_state():
    if not os.path.exists(STATE_FILE):
        return {
            "balance": INITIAL_BALANCE, "peak_equity": INITIAL_BALANCE,
            "position": None, "stats": {"total": 0, "wins": 0, "pnl": 0.0},
            "trades": [], "last_sl_time": None,
        }
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def write_status(status):
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2, default=str)


# ─── Indicator computation ───
def compute_indicators(df_15m, df_1d):
    """Add all v3 indicators to df_15m (15m bars per v3 calibration)."""
    df_5m = df_15m  # alias kept to minimize diff — variable named df_5m below but holds 15m data
    h, l, c, v = df_5m["high"], df_5m["low"], df_5m["close"], df_5m["volume"]
    pc = c.shift(1)

    # Recompute RSI with v3 period (7)
    delta = c.diff()
    up = delta.clip(lower=0).rolling(RSI_PERIOD).mean()
    dn = (-delta.clip(upper=0)).rolling(RSI_PERIOD).mean()
    df_5m["rsi"] = 100 - (100 / (1 + up / dn.replace(0, np.nan)))

    # ATR(14)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    df_5m["atr_14"] = tr.rolling(14).mean()
    df_5m["atr_pct"] = df_5m["atr_14"] / c * 100

    # ADX(14)
    up_m = h.diff(); dn_m = -l.diff()
    plus_dm = np.where((up_m > dn_m) & (up_m > 0), up_m, 0.0)
    minus_dm = np.where((dn_m > up_m) & (dn_m > 0), dn_m, 0.0)
    atrd = tr.rolling(14).mean().replace(0, np.nan)
    plus_di  = 100 * pd.Series(plus_dm,  index=df_5m.index).rolling(14).mean() / atrd
    minus_di = 100 * pd.Series(minus_dm, index=df_5m.index).rolling(14).mean() / atrd
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df_5m["adx_14"] = dx.rolling(14).mean()

    # BB width pct rank (rolling 100 bars)
    bbm = c.rolling(20).mean()
    bbs = c.rolling(20).std()
    df_5m["bb_width_pct"] = (4 * bbs) / bbm * 100
    df_5m["bb_width_rank_100"] = df_5m["bb_width_pct"].rolling(100).rank(pct=True)

    # range_pos 1-day
    rh = h.rolling(RP_LOOKBACK_BARS).max()
    rl = l.rolling(RP_LOOKBACK_BARS).min()
    df_5m["range_pos"] = (c - rl) / (rh - rl) * 100
    df_5m["rp_high"] = rh
    df_5m["rp_low"] = rl

    # Today change from UTC open
    df_5m["date"] = df_5m["timestamp"].dt.date
    df_5m["today_open"] = df_5m.groupby("date")["open"].transform("first")
    df_5m["today_change_pct"] = (c - df_5m["today_open"]) / df_5m["today_open"] * 100

    # Daily EMA50 slope (5-day)
    df_1d = df_1d.copy()
    df_1d["ema50"] = df_1d["close"].ewm(span=50, adjust=False).mean()
    df_1d["ema50_slope_5d"] = (df_1d["ema50"] / df_1d["ema50"].shift(5) - 1) * 100
    df_1d["date"] = df_1d["timestamp"].dt.date
    slope_map = dict(zip(df_1d["date"], df_1d["ema50_slope_5d"]))
    df_5m["daily_ema50_slope"] = df_5m["date"].map(slope_map)

    return df_5m


# ─── Position open / close ───
def open_position(state, side, l1_px, balance):
    qty_l1 = per_level_qty(balance, l1_px, leg_idx=0)
    fees = qty_l1 * l1_px * COMMISSION_PCT
    state["balance"] -= fees
    state["position"] = {
        "side": side, "first_entry": l1_px, "worst_entry": l1_px,
        "avg_entry": l1_px, "peak_price": l1_px,
        "entries": [{"px": l1_px, "qty": qty_l1, "leg": 1}],
        "qty_total": qty_l1, "filled": 1,
        "balance_at_entry": balance,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "leverage": LEVERAGE,
        "sl_px": sl_price_v3(side, l1_px),
        "l3_lock_px": None,  # only set when L3 fills
    }
    log.warning(f"  OPENED {side} L1: {qty_l1:.4f}@${l1_px:.2f} | fees ${fees:.2f} | balance ${state['balance']:.2f}")


def fill_dca(state, leg_idx, fill_px, balance):
    """Fill L2 (leg_idx=1) or L3 (leg_idx=2)."""
    pos = state["position"]
    qty = per_level_qty(balance, fill_px, leg_idx=leg_idx)
    fees = qty * fill_px * COMMISSION_PCT
    state["balance"] -= fees
    pos["entries"].append({"px": fill_px, "qty": qty, "leg": leg_idx + 1})
    pos["qty_total"] += qty
    # recompute avg
    notional = sum(e["px"] * e["qty"] for e in pos["entries"])
    pos["avg_entry"] = notional / pos["qty_total"]
    pos["worst_entry"] = fill_px
    pos["filled"] = leg_idx + 1
    # set L3 lock if applicable
    if pos["filled"] == 3:
        pos["l3_lock_px"] = l3_lock_price(pos["side"], pos["avg_entry"])
    log.warning(f"  L{leg_idx+1} FILL: {qty:.4f}@${fill_px:.2f} | new avg ${pos['avg_entry']:.2f} | "
                f"fees ${fees:.2f} | balance ${state['balance']:.2f}")


def close_position(state, exit_px, reason):
    pos = state["position"]
    qty = pos["qty_total"]
    side = pos["side"]
    avg = pos["avg_entry"]
    gross = (exit_px - avg) * qty if side == "LONG" else (avg - exit_px) * qty
    fees = qty * exit_px * COMMISSION_PCT
    net = gross - fees
    pct_balance = net / pos["balance_at_entry"] * 100
    pct_price = (exit_px - avg) / avg * 100 if side == "LONG" else (avg - exit_px) / avg * 100
    state["balance"] += net + (qty * avg)  # return capital + pnl
    state["balance"] -= (qty * avg)  # cancel out — net already includes the move
    state["balance"] += net  # actual pnl
    # Correct accounting: balance only changes by net pnl
    # (the above is wrong, let me redo)
    # Actually: balance was decremented by fees on entries. Now we add gross - exit_fees.
    # Net change to balance = gross (entry pnl) - exit_fees - already-deducted entry fees
    # Simpler: just track pnl as net - total_fees, balance starts at INITIAL, all P&L flows here.
    # For now, the simpler approach: net pnl includes all fees so just add it.
    # But above I double-added. Let me fix:

    # RESET — clean accounting
    # Restore balance to pre-this-close state (undo the wrong adds above)
    state["balance"] -= net + (qty * avg) - (qty * avg) + net
    # Now add only the correct net pnl
    state["balance"] += net

    state["stats"]["total"] += 1
    if net > 0:
        state["stats"]["wins"] += 1
    state["stats"]["pnl"] += pct_balance

    log.warning(f"  EXIT {side} via {reason} @${exit_px:.2f} | avg_entry ${avg:.2f} | "
                f"gross ${gross:+.2f} − fees ${fees:.2f} = net ${net:+.2f} "
                f"(price {pct_price:+.2f}% / balance {pct_balance:+.2f}%) | balance ${state['balance']:.2f}")

    # Trade log
    if "trades" not in state:
        state["trades"] = []
    state["trades"].append({
        "entry_time": pos["entry_time"],
        "exit_time": datetime.now(timezone.utc).isoformat(),
        "side": side, "avg_entry": round(avg, 2),
        "exit": round(exit_px, 2), "entries": pos["filled"],
        "reason": reason, "pnl_usd": round(net, 2),
        "pnl_pct": round(pct_balance, 3),
    })
    # Keep only last 100 trades in state
    state["trades"] = state["trades"][-100:]

    if reason == "SL":
        state["last_sl_time"] = datetime.now(timezone.utc).isoformat()

    state["position"] = None
    return reason


# ─── Main tick ───
def main():
    state = load_state()

    # Fetch market data (v3 uses 15m to match backtest calibration)
    df_15m = fetch_klines(TIMEFRAME, 500)
    df_1d = fetch_klines("1d", 200)
    if df_15m is None or len(df_15m) < 100 or df_1d is None or len(df_1d) < 60:
        log.error("insufficient klines")
        return

    live_px = fetch_live_price()
    if live_px is None:
        live_px = float(df_15m.iloc[-1]["close"])

    # Compute base features + divergence (V2.2 core does this)
    df = build_features(df_15m, df_1d)
    df = detect_divergence(df, DIV_PIVOT_L, DIV_PIVOT_R)
    # Override RSI with v3 period + recompute all the v3 indicators
    df = compute_indicators(df, df_1d)

    last_idx = len(df) - 1
    row = df.iloc[last_idx]

    # Pull the v3 inputs
    range_pos = float(row["range_pos"]) if not pd.isna(row["range_pos"]) else None
    bb_rank = float(row["bb_width_rank_100"]) if not pd.isna(row["bb_width_rank_100"]) else None
    today_chg = float(row["today_change_pct"]) if not pd.isna(row["today_change_pct"]) else None
    adx = float(row["adx_14"]) if not pd.isna(row["adx_14"]) else None
    atr_pct = float(row["atr_pct"]) if not pd.isna(row["atr_pct"]) else None
    htf_slope = float(row["daily_ema50_slope"]) if not pd.isna(row["daily_ema50_slope"]) else None
    rp_high = float(row["rp_high"]) if not pd.isna(row["rp_high"]) else None
    rp_low = float(row["rp_low"]) if not pd.isna(row["rp_low"]) else None

    current_side = state["position"]["side"] if state["position"] else None

    sig = evaluate_signal_v3(
        df, last_idx,
        range_pos=range_pos, bb_width_rank=bb_rank,
        today_change_pct=today_chg, adx=adx, atr_pct=atr_pct,
        daily_ema50_slope_pct=htf_slope, current_side=current_side,
    )

    log.info("=" * 64)
    log.info(f"Divflip v3 — RSI(7) {DIV_PIVOT_L}L/{DIV_PIVOT_R}R, 3 DCA @0.2%/0.4% (1:2:4), "
             f"FLIP exit | SL {SL_FROM_L1*100:.1f}% L1 | L3 lock {L3_LOCK_PCT*100:.1f}%")
    log.info(f"  Balance: ${state['balance']:.2f} | BTC: ${live_px:.2f}")

    # ─── DCA fill check (if in position) ───
    pos = state["position"]
    exit_reason = None
    if pos is not None:
        side = pos["side"]
        # Check L2 / L3 fills based on current bar high/low (use live_px as proxy)
        # In production we'd track bar-level; here we use live_px conservatively
        if pos["filled"] < DCA_LEVELS:
            l2_trigger = dca_price(side, pos["first_entry"], leg_idx=1)
            l3_trigger = dca_price(side, pos["first_entry"], leg_idx=2)
            # L2 fill
            if pos["filled"] == 1:
                if (side == "LONG" and live_px <= l2_trigger) or (side == "SHORT" and live_px >= l2_trigger):
                    fill_dca(state, 1, l2_trigger, pos["balance_at_entry"])
            # L3 fill (may have skipped L2 if price gapped)
            if pos["filled"] == 2:
                if (side == "LONG" and live_px <= l3_trigger) or (side == "SHORT" and live_px >= l3_trigger):
                    fill_dca(state, 2, l3_trigger, pos["balance_at_entry"])

        # Re-load pos after potential fills
        pos = state["position"]
        avg = pos["avg_entry"]

        # ─── EXIT CHECKS — order: SL → L3 lock → FLIP ───
        # 1. Hard SL
        sl_px = pos["sl_px"]
        if (side == "LONG" and live_px <= sl_px) or (side == "SHORT" and live_px >= sl_px):
            close_position(state, sl_px, "SL")
            exit_reason = "SL"

        # 2. L3 lock (only if L3 filled)
        if not exit_reason and pos["filled"] == 3 and pos["l3_lock_px"] is not None:
            lock_px = pos["l3_lock_px"]
            if (side == "LONG" and live_px >= lock_px) or (side == "SHORT" and live_px <= lock_px):
                close_position(state, lock_px, "L3_LOCK")
                exit_reason = "L3_LOCK"

        # 3. FLIP exit (opposite divergence + min profit)
        if not exit_reason and sig.flip_opposite:
            upnl_pct = ((live_px - avg) / avg if side == "LONG" else (avg - live_px) / avg) * LEVERAGE
            if upnl_pct >= FLIP_MIN_PROFIT:
                close_position(state, live_px, "FLIP")
                exit_reason = "FLIP"

    # ─── Cooldown check ───
    cooldown_active = False
    cooldown_msg = ""
    last_sl = state.get("last_sl_time")
    if last_sl:
        last_sl_dt = datetime.fromisoformat(last_sl.replace("Z", "+00:00")) if isinstance(last_sl, str) else last_sl
        elapsed = datetime.now(timezone.utc) - last_sl_dt
        if elapsed < timedelta(hours=SL_COOLDOWN_HOURS):
            cooldown_active = True
            hrs = (timedelta(hours=SL_COOLDOWN_HOURS) - elapsed).total_seconds() / 3600
            cooldown_msg = f"COOLDOWN active ({hrs:.1f}h remaining)"

    # ─── ENTRY CHECK ───
    if state["position"] is None and not exit_reason:
        if cooldown_active and sig.side:
            log.info(f"  Signal {sig.side} SKIPPED — {cooldown_msg}")
        elif sig.side:
            open_position(state, sig.side, live_px, state["balance"])

    # ─── In-position log line ───
    pos = state.get("position")
    if pos:
        side = pos["side"]
        avg = pos["avg_entry"]
        fav = ((live_px - avg) / avg if side == "LONG" else (avg - live_px) / avg) * 100
        fav_pos = fav * LEVERAGE
        log.info(f"  IN {side} L{pos['filled']}/{DCA_LEVELS} avg=${avg:.2f} live=${live_px:.2f} "
                 f"fav={fav:+.2f}% (pos {fav_pos:+.2f}%) SL=${pos['sl_px']:.2f}"
                 + (f" L3lock=${pos['l3_lock_px']:.2f}" if pos.get('l3_lock_px') else ""))

    # ─── Stats line ───
    stats = state["stats"]
    wr = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0.0
    total_pnl_pct = (state["balance"] / INITIAL_BALANCE - 1) * 100
    log.info(f"  Stats: {stats['total']} trades | WR {wr:.0f}% | PnL {total_pnl_pct:+.2f}%")

    save_state(state)

    # ─── Build STATUS with ACTUAL vs REQUIRED for each filter ───
    pos_status = None
    if pos:
        pos_status = {
            "side": pos["side"],
            "first_entry": pos["first_entry"],
            "avg_entry": pos["avg_entry"],
            "qty_total": pos["qty_total"],
            "filled": pos["filled"],
            "sl_px": pos["sl_px"],
            "l3_lock_px": pos.get("l3_lock_px"),
            "entry_time": pos["entry_time"],
            "leverage": pos["leverage"],
        }

    status = {
        "env": "paper_divflip_v3",
        "pair": PAIR,
        "price": live_px,
        "live_price": live_px,
        "balance": state["balance"],
        "peak_equity": state.get("peak_equity", state["balance"]),
        "position": pos_status,
        "signal": sig.side,
        "indicators": sig.raw,
        "stats": stats,
        "cooldown_active": cooldown_active,
        "cooldown_msg": cooldown_msg,
        "last_sl_time": state.get("last_sl_time"),

        # ── FILTERS: actual vs required ──
        "filters": {
            "bb_squeeze": {
                "actual": round(bb_rank, 3) if bb_rank is not None else None,
                "required": f">= {BB_SQUEEZE_RANK}",
                "pass": (bb_rank is None) or (bb_rank >= BB_SQUEEZE_RANK),
                "enabled": USE_BB_SQUEEZE,
            },
            "today_change": {
                "actual": round(today_chg, 2) if today_chg is not None else None,
                "required": f"between -{TODAY_LIMIT_PCT}% and +{TODAY_LIMIT_PCT}% (side-dependent)",
                "pass": (today_chg is None) or (abs(today_chg) <= TODAY_LIMIT_PCT),
                "enabled": USE_TODAY_CHANGE,
            },
            "adx_cap": {
                "actual": round(adx, 1) if adx is not None else None,
                "required": f"<= {ADX_MAX}",
                "pass": (adx is None) or (adx <= ADX_MAX),
                "enabled": USE_ADX_CAP,
            },
            "min_atr": {
                "actual": round(atr_pct, 3) if atr_pct is not None else None,
                "required": f">= {MIN_ATR_PCT}%",
                "pass": (atr_pct is None) or (atr_pct >= MIN_ATR_PCT),
                "enabled": USE_MIN_ATR,
            },
            "htf_bias": {
                "actual": round(htf_slope, 2) if htf_slope is not None else None,
                "required": f"daily slope between -{HTF_SLOPE_THR}% and +{HTF_SLOPE_THR}% (or override)",
                "pass": (htf_slope is None) or (abs(htf_slope) <= HTF_SLOPE_THR),
                "enabled": USE_HTF_BIAS,
            },
        },

        # ── range_pos display (informational only — not a filter in v3) ──
        "range_pos_1d": round(range_pos, 2) if range_pos is not None else None,
        "rp_1d_high": round(rp_high, 2) if rp_high is not None else None,
        "rp_1d_low": round(rp_low, 2) if rp_low is not None else None,

        "strategy": (f"Divflip v3 [PAPER] (LOCKED v4.2): RSI{RSI_PERIOD} {DIV_PIVOT_L}L/{DIV_PIVOT_R}R / "
                     f"3 DCA @ 0.2%/0.4% (1:2:4) / SL {SL_FROM_L1*100:.1f}% L1 / "
                     f"L3 lock {L3_LOCK_PCT*100:.1f}% / FLIP exit / "
                     f"{SL_COOLDOWN_HOURS}h cooldown / no time stop"),
        "paper_mode": True,
        "state": "IN_POSITION" if pos else "FLAT",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_status(status)
    log.info("=" * 64 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}")
        sys.exit(1)
