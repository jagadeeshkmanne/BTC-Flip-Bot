#!/usr/bin/env python3
"""bot_trend_4h.py — Dynamic-Leverage Trend (4h BTC perp, long/flat) PAPER bot.

THE RULE (best strategy, 2026-06-16; backtest/version_compare.py, REVIEW_validation.py):
  ENTRY (long): EMA13 > EMA20 AND close > EMA200  (on the last CLOSED 4h bar)
  EXIT  (flat): when that flips. NO take-profit (full ride — fixed TP hurts).
  DIRECTION: long / flat only. No shorts (15+ short tests all net-negative).

  DYNAMIC LEVERAGE (the validated edge this round):
    base = min(0.02 / ATR%_4h, cap)
    cap  = 5x when conviction high (4h ADX(14) > 20 AND weekly close > weekly EMA50)
           else 2.5x
    overlay: base *= 0.5 if (30-bar realized-vol rank > 0.78) OR (daily funding > 0.0015)
    overlay: base *= 0.5 if (prior daily close < daily EMA200)   # slow-bear cut
    -> avg effective leverage ~1.1x, max ~5x only in calm high-conviction uptrends.

HONEST PAPER ACCOUNTING (lessons from the rsiscalp post-mortem):
  - every fill at the LIVE price (+/- slippage), never a target price
  - taker fee 0.055%/side on notional; funding charged on held notional (long pays when +)
  - decisions ONLY on CLOSED 4h bars; at most one decision per 4h bar
  - leveraged: qty = balance*lev/entry; equity = balance + qty*(price-entry)

Cron: every 15 min (status freshness); trades only when a new 4h bar closes.
State/log/status: data/trend_btc/
"""
from __future__ import annotations
import os, sys, json, logging
from datetime import datetime, timezone

import pandas as pd
import requests

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(STRATEGY_DIR)
sys.path.insert(0, STRATEGY_DIR)
from bybit_data import fetch_klines, fetch_live_price

# ─── Parameters (FIXED — validated as a set; do not tune live) ───
PAIR = "BTCUSDT"
EMA_F, EMA_S, EMA_G = 13, 20, 200
ADX_LEN = 14
ATR_LEN = 14
VOL_TARGET = 0.02
CAP_HIGH, CAP_LOW = 5.0, 2.5
RV_LEN, RV_RANK_HI = 30, 0.78
FUND_HI = 0.0015          # daily funding threshold for the de-lever overlay
INITIAL_BALANCE = 5000.0
FEE_PCT = 0.00055         # perp taker per side
SLIP_PCT = 0.0005

DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("TREND_DATA_DIR", "trend_btc"))
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

log = logging.getLogger("bot_trend_4h")
log.setLevel(logging.INFO); log.handlers.clear()
for h in (logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)):
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")); log.addHandler(h)


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1)
    return ema(tr, n)

def adx(df: pd.DataFrame, n: int) -> pd.Series:
    up = df["high"].diff(); dn = -df["low"].diff()
    pdm = ((up > dn) & (up > 0)) * up.clip(lower=0)
    ndm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    a = atr(df, n)
    pdi = 100 * ema(pdm, n) / a
    ndi = 100 * ema(ndm, n) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, 1e-9)
    return ema(dx, n)

def fetch_funding() -> float:
    """Current 8h funding rate (fraction). Returns 0.0 on failure (neutral)."""
    try:
        r = requests.get("https://api.bybit.com/v5/market/tickers",
                         params={"category": "linear", "symbol": PAIR}, timeout=10)
        return float(r.json()["result"]["list"][0].get("fundingRate") or 0.0)
    except Exception as e:
        log.error(f"funding fetch failed: {e}"); return 0.0

def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"balance": INITIAL_BALANCE, "peak_equity": INITIAL_BALANCE, "position": None,
                "last_decision_bar": None, "last_funding_bar": None,
                "stats": {"total": 0, "wins": 0, "losses": 0, "pnl": 0.0}, "trade_log": []}
    with open(STATE_FILE) as f:
        return json.load(f)

def atomic_write(path: str, payload: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, default=str, indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def main() -> None:
    state = load_state()
    df = fetch_klines("4h", 1000, PAIR, log)
    if df is None or len(df) < EMA_G + RV_LEN + 5:
        log.error("insufficient 4h klines"); return
    live_px = fetch_live_price(PAIR, log)
    if live_px is None:
        log.error("live price unavailable"); return

    closed = df.iloc[:-1].copy()              # decisions on CLOSED bars only
    closed["ema_f"] = ema(closed["close"], EMA_F)
    closed["ema_s"] = ema(closed["close"], EMA_S)
    closed["ema_g"] = ema(closed["close"], EMA_G)
    closed["atr"] = atr(closed, ATR_LEN)
    closed["adx"] = adx(closed, ADX_LEN)
    closed["rv"] = closed["close"].pct_change().rolling(RV_LEN).std()
    last = closed.iloc[-1]
    bar_id = str(pd.Timestamp(last["timestamp"]))

    # higher-timeframe conviction
    wdf = fetch_klines("1w", 200, PAIR, log)
    weekly_bull = True
    if wdf is not None and len(wdf) > 51:
        wc = wdf.iloc[:-1]["close"]; weekly_bull = bool(wc.iloc[-1] > ema(wc, 50).iloc[-1])
    ddf = fetch_klines("1d", 400, PAIR, log)
    daily_bear = False
    if ddf is not None and len(ddf) > 201:
        dc = ddf.iloc[:-1]["close"]; daily_bear = bool(dc.iloc[-1] < ema(dc, 200).iloc[-1])
    fr8 = fetch_funding(); fr_daily = fr8 * 3.0

    # realized-vol percentile rank over available window (live approx of 1yr rank)
    rv_now = float(last["rv"]); rv_hist = closed["rv"].dropna()
    rv_rank = float((rv_hist < rv_now).mean()) if len(rv_hist) > 50 else 0.0

    target_long = bool(last["ema_f"] > last["ema_s"] and last["close"] > last["ema_g"])
    adx_ok = bool(last["adx"] > 20)
    atr_pct = float(last["atr"]) / float(last["close"]) if last["close"] else 0.02
    cap = CAP_HIGH if (adx_ok and weekly_bull) else CAP_LOW
    lev = min(VOL_TARGET / atr_pct, cap) if atr_pct > 0 else cap
    if rv_rank > RV_RANK_HI or fr_daily > FUND_HI: lev *= 0.5
    if daily_bear: lev *= 0.5

    pos = state.get("position")
    log.info(f"  bar {bar_id}: close ${last['close']:,.0f} EMA{EMA_F}/{EMA_S}/{EMA_G} "
             f"{last['ema_f']:,.0f}/{last['ema_s']:,.0f}/{last['ema_g']:,.0f} | ADX {last['adx']:.0f} "
             f"| wk {'bull' if weekly_bull else 'bear'} | dly {'bear' if daily_bear else 'bull'} "
             f"| fund8h {fr8*100:+.3f}% | rvRank {rv_rank:.2f} | lev {lev:.2f}x "
             f"| target {'LONG' if target_long else 'FLAT'} | pos {'LONG' if pos else 'FLAT'} | live ${live_px:,.0f}")

    # funding accrual on held position (long pays when funding>0), once per closed 4h bar
    if pos and state.get("last_funding_bar") != bar_id:
        state["last_funding_bar"] = bar_id
        notional = pos["qty"] * live_px * pos["lev"]
        state["balance"] -= fr8 * 0.5 * notional   # ~half an 8h period per 4h bar
    # one decision per closed 4h bar
    if state.get("last_decision_bar") != bar_id:
        state["last_decision_bar"] = bar_id
        if target_long and pos is None:
            fill = live_px * (1 + SLIP_PCT); bal = state["balance"]
            qty = bal * lev / fill; fee = FEE_PCT * qty * fill
            state["balance"] = bal - fee
            state["position"] = {"qty": qty, "entry": fill, "lev": lev, "cost_basis": bal,
                                 "entry_time": datetime.now(timezone.utc).isoformat()}
            log.warning(f"  LONG {qty:.4f} BTC @ ${fill:,.2f} lev {lev:.2f}x (fee ${fee:.2f})")
        elif not target_long and pos is not None:
            fill = live_px * (1 - SLIP_PCT)
            pnl = pos["qty"] * (fill - pos["entry"]) * pos["lev"]; fee = FEE_PCT * pos["qty"] * fill * pos["lev"]
            net = pnl - fee
            state["balance"] += net
            st = state["stats"]; st["total"] += 1; st["pnl"] += net
            st["wins" if net > 0 else "losses"] += 1
            state.setdefault("trade_log", []).append({
                "entry": pos["entry"], "exit": fill, "qty": pos["qty"], "lev": pos["lev"],
                "net_usd": net, "net_pct": net / pos["cost_basis"] * 100,
                "entry_time": pos["entry_time"], "exit_time": datetime.now(timezone.utc).isoformat(),
                "reason": "TREND_FLIP"})
            state["trade_log"] = state["trade_log"][-200:]
            state["position"] = None
            log.warning(f"  CLOSE @ ${fill:,.2f} net ${net:+,.2f} ({net/pos['cost_basis']*100:+.2f}%)")

    pos = state.get("position")
    if pos:
        equity = state["balance"] + pos["qty"] * (live_px - pos["entry"]) * pos["lev"]
        unreal = pos["qty"] * (live_px - pos["entry"]) * pos["lev"]
    else:
        equity = state["balance"]; unreal = 0.0
    state["peak_equity"] = max(state.get("peak_equity", equity), equity)
    peak = state["peak_equity"]; dd = (equity / peak - 1) if peak > 0 else 0.0

    atomic_write(STATE_FILE, state)
    atomic_write(STATUS_FILE, {
        "env": os.environ.get("TREND_DATA_DIR", "trend_btc"), "pair": PAIR,
        "price": float(last["close"]), "live_price": live_px,
        "balance": equity, "peak_equity": peak, "drawdown_pct": dd,
        "position": ({"side": "LONG", "qty_total": pos["qty"], "avg_entry": pos["entry"],
                      "first_entry": pos["entry"], "leverage": pos["lev"], "entry_time": pos["entry_time"],
                      "unrealized_usd": unreal, "filled": 1} if pos else None),
        "signal": "LONG" if target_long else "FLAT",
        "indicators": {"ema_f": float(last["ema_f"]), "ema_s": float(last["ema_s"]), "ema_g": float(last["ema_g"]),
                       "adx": float(last["adx"]), "weekly_bull": weekly_bull, "daily_bear": daily_bear,
                       "funding_8h_pct": fr8*100, "rv_rank": rv_rank, "leverage": lev,
                       "cap": cap, "closed_bar": bar_id},
        "stats": state["stats"],
        "strategy": (f"Dynamic-Lev Trend — 4h BTC perp, long/flat. LONG when EMA{EMA_F}>EMA{EMA_S} AND "
                     f"close>EMA{EMA_G}; exit on flip, no TP. Dyn leverage {CAP_LOW}x<->{CAP_HIGH}x by "
                     f"ADX+weekly conviction, vol-targeted, de-levered in high-vol/funding/bear. "
                     f"Backtest OOS ~50-65% CAGR / ~28% DD [PAPER]"),
        "paper_mode": True, "state": "IN_POSITION" if pos else "FLAT",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    log.info(f"  equity ${equity:,.2f} (peak ${peak:,.2f}, DD {dd*100:+.2f}%) | trades {state['stats']['total']} "
             f"| realized P&L ${state['stats']['pnl']:+,.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}"); sys.exit(1)
