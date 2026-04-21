#!/usr/bin/env python3
"""
backtest_1h.py — Run V2b (Structure Break + SL-Flip) on 1h bars across multiple pairs.

Fetches full 1h history from Binance futures, simulates V2b logic identically
to the Pine script, outputs metrics per pair.

Usage:
    python3 backtest_1h.py BTCUSDT BNBUSDT SOLUSDT

Key V2b rules replicated:
  - Daily EMA50 bias (resampled from 1h, prior-day close, no lookahead)
  - Daily RSI(14) -> but we use 1h RSI on the entry TF
  - Pivot structure (HH+HL / LH+LL + break of latest pivot)
  - SL: 5-bar swing ± 0.1% buffer, capped at 2.5%
  - TP ladder: TP1 50%@3R (SL->BE+0.75%), TP2 25%@4R, Runner 25% ATR-trail
  - DD-adaptive risk: max(0.5, 1 + drawdownPct)
  - Hard halt 15% -> 168 hours (7 days on 1h)
  - SL-flip bias-gated (Long SL before TP1 + bias bear -> flip short)
  - Cooldown: 72 hours (3 days on 1h)
"""
import os, sys, time, json
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import numpy as np
import requests

# ═════ Strategy Params (match V2b canonical) ═════
LEVERAGE          = 2.0
RISK_PCT          = 0.03
PIVOT_LEN         = 3
SL_SWING_LEN      = 5
SL_BUFFER_PCT     = 0.001
SL_MAX_PCT        = 0.025
TP1_R             = 3.0
TP1_FRAC          = 0.50
TP2_R             = 4.0
TP2_FRAC          = 0.25
BE_BUF_PCT        = 0.0075
TRAIL_ATR_MULT    = 2.5
DD_HALT_PCT       = 0.15
# Halt/cooldown bars scale with TF. Set by main() based on --interval.
DD_HALT_BARS      = 7   # default 1D: 7 bars = 7 days
GEN_CD_BARS       = 3   # default 1D: 3 bars = 3 days
RSI_PERIOD        = 14
ATR_PERIOD        = 14
EMA_BIAS_LEN      = 50
COMMISSION_PCT    = 0.0004  # 0.04% per side (Binance futures taker)
# TV Pine V2b uses slippage=3 ticks. Tick size varies per pair:
#   BTC: $0.10 -> 3 ticks ~ 0.0004% at $80K
#   BNB: $0.01 -> 3 ticks ~ 0.005% at $600
#   SOL: $0.001 -> 3 ticks ~ 0.002% at $150
# Using 0.0005% as a reasonable global proxy (was 0.03% — 75x too aggressive).
SLIPPAGE_PCT      = 0.000005  # 0.0005% per fill (was 0.0003 -> way too high)

INITIAL_EQUITY    = 5000.0
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


# ═════ Data fetch ═════
def fetch_klines(symbol: str, interval: str = "1h", start_ms: int = None) -> pd.DataFrame:
    """Paginate through Binance futures klines for full history."""
    cache_file = os.path.join(CACHE_DIR, f"{symbol}_{interval}.csv")
    if os.path.exists(cache_file):
        print(f"  {symbol}: loading cached {cache_file}")
        df = pd.read_csv(cache_file, parse_dates=["timestamp"])
        return df

    print(f"  {symbol}: fetching from Binance (full history)...")
    BASE = "https://fapi.binance.com/fapi/v1/klines"
    all_bars = []
    LIMIT = 1500
    # Default start: 2019-09-01 (BTC futures launched ~Sep 2019)
    cursor = start_ms if start_ms else int(datetime(2019, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
    while True:
        params = {"symbol": symbol, "interval": interval, "limit": LIMIT}
        if cursor: params["startTime"] = cursor
        for attempt in range(3):
            try:
                r = requests.get(BASE, params=params, timeout=15)
                if r.status_code == 200:
                    data = r.json()
                    break
                time.sleep(2)
            except Exception as e:
                print(f"    retry {attempt+1}: {e}")
                time.sleep(2)
        else:
            print(f"    FAILED after 3 retries, stopping")
            break
        if not data: break
        all_bars.extend(data)
        print(f"    fetched {len(all_bars)} bars, last: {pd.to_datetime(data[-1][0], unit='ms')}")
        if len(data) < LIMIT: break
        cursor = data[-1][0] + 1
        time.sleep(0.15)  # rate limit friendly

    if not all_bars:
        print(f"  {symbol}: no data!")
        return pd.DataFrame()

    df = pd.DataFrame(all_bars, columns=[
        "ot","open","high","low","close","volume","ct","qav","trades","tbbav","tbqav","ig"
    ])
    df = df.astype({"open":float,"high":float,"low":float,"close":float,"volume":float})
    df["timestamp"] = pd.to_datetime(df["ot"], unit="ms")
    df = df[["timestamp","open","high","low","close","volume"]]
    df.to_csv(cache_file, index=False)
    print(f"  {symbol}: saved {len(df)} bars to cache")
    return df


# ═════ Indicators ═════
def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    gain = d.clip(lower=0); loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    al = loss.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()

def compute_pivots(df: pd.DataFrame, pivot_len: int = PIVOT_LEN) -> pd.DataFrame:
    """For each bar, fill ph_last, ph_prev, pl_last, pl_prev using strict comparison.
    Pivot confirmed `pivot_len` bars after its formation (no repaint)."""
    highs = df["high"].values
    lows = df["low"].values
    n = len(df)

    ph_series = [np.nan] * n
    pl_series = [np.nan] * n

    for i in range(pivot_len, n - pivot_len):
        is_ph = True; is_pl = True
        for j in range(i - pivot_len, i + pivot_len + 1):
            if j == i: continue
            if highs[j] >= highs[i]: is_ph = False
            if lows[j]  <= lows[i]:  is_pl = False
            if not is_ph and not is_pl: break
        # Pivot confirms at bar (i + pivot_len), not at i
        confirm_bar = i + pivot_len
        if confirm_bar < n:
            if is_ph: ph_series[confirm_bar] = float(highs[i])
            if is_pl: pl_series[confirm_bar] = float(lows[i])

    # Forward fill the last two values per row
    df["ph_last"] = np.nan
    df["ph_prev"] = np.nan
    df["pl_last"] = np.nan
    df["pl_prev"] = np.nan

    last_ph = np.nan; prev_ph = np.nan
    last_pl = np.nan; prev_pl = np.nan
    for i in range(n):
        if not np.isnan(ph_series[i]):
            prev_ph = last_ph
            last_ph = ph_series[i]
        if not np.isnan(pl_series[i]):
            prev_pl = last_pl
            last_pl = pl_series[i]
        df.iat[i, df.columns.get_loc("ph_last")] = last_ph
        df.iat[i, df.columns.get_loc("ph_prev")] = prev_ph
        df.iat[i, df.columns.get_loc("pl_last")] = last_pl
        df.iat[i, df.columns.get_loc("pl_prev")] = prev_pl
    return df


# ═════ Build features ═════
def build_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().reset_index(drop=True)
    df["rsi"] = rsi(df["close"], RSI_PERIOD)
    df["atr"] = atr(df, ATR_PERIOD)
    df["swing_low"]  = df["low"].rolling(SL_SWING_LEN).min()
    df["swing_high"] = df["high"].rolling(SL_SWING_LEN).max()
    df = compute_pivots(df, PIVOT_LEN)

    # Daily bias: resample 1h to 1D, compute EMA50, shift by 1 day, map back
    d_idx = df.set_index("timestamp")
    daily = d_idx.resample("1D").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()
    daily["ema50"] = daily["close"].ewm(span=EMA_BIAS_LEN, adjust=False).mean()
    daily["bias"] = np.where(daily["close"] > daily["ema50"], 1,
                     np.where(daily["close"] < daily["ema50"], -1, 0))
    # Prior day's bias applies to today's hourly bars
    daily["bias_prior"] = daily["bias"].shift(1).fillna(0).astype(int)

    # Map each hourly bar to its prior day's bias
    df["date_day"] = df["timestamp"].dt.normalize()
    bias_by_day = daily["bias_prior"].to_dict()
    # daily index is the day's start (00:00). We want bar's date -> prior day's bias_prior
    # daily.index entries are day starts; bias_prior at index D = EMA bias from day D-1's close
    df["bias_d"] = df["date_day"].map(bias_by_day).fillna(0).astype(int)
    return df


# ═════ Backtest loop ═════
def backtest(df: pd.DataFrame, label: str = "") -> dict:
    equity = INITIAL_EQUITY
    peak_equity = INITIAL_EQUITY
    halt_until = -1
    last_exit_bar = -10**9

    position = None  # dict with side, entry_price, qty, orig_qty, sl, init_sl_dist, tp1_done, tp2_done, entry_bar, is_flip, entry_type
    trades = []
    equity_curve = []

    for i in range(len(df)):
        bar = df.iloc[i]
        px = bar["close"]
        rsi_v = bar["rsi"]
        atr_v = bar["atr"]
        bias_d = int(bar["bias_d"]) if not pd.isna(bar["bias_d"]) else 0
        ph_last = bar["ph_last"]; ph_prev = bar["ph_prev"]
        pl_last = bar["pl_last"]; pl_prev = bar["pl_prev"]
        sw_lo = bar["swing_low"]; sw_hi = bar["swing_high"]

        # Skip until indicators warmed up
        if pd.isna(rsi_v) or pd.isna(atr_v) or pd.isna(sw_lo) or pd.isna(sw_hi):
            equity_curve.append(equity); continue

        # Halt check (only when flat)
        halted = i < halt_until

        # DD tracking
        peak_equity = max(peak_equity, equity)
        dd_pct = (equity / peak_equity - 1) if peak_equity > 0 else 0.0
        if dd_pct <= -DD_HALT_PCT and position is None:
            halt_until = i + DD_HALT_BARS
            peak_equity = equity
            halted = True

        dd_factor = max(0.5, 1 + dd_pct)
        eff_risk = RISK_PCT * dd_factor

        # Swing SL prices (independent of entry for now; we compute at entry time)
        def calc_sl(side, entry_px):
            if side == "LONG":
                raw = sw_lo * (1 - SL_BUFFER_PCT)
                cap = entry_px * (1 - SL_MAX_PCT)
                return max(raw, cap)
            else:
                raw = sw_hi * (1 + SL_BUFFER_PCT)
                cap = entry_px * (1 + SL_MAX_PCT)
                return min(raw, cap)

        def calc_qty(side, entry_px, sl_px):
            dist = abs(entry_px - sl_px)
            if dist <= 0: return 0.0
            risk_amt = equity * 0.95 * eff_risk
            qty_risk = risk_amt / dist
            qty_cap = (equity * 0.95 * LEVERAGE) / entry_px
            return min(qty_risk, qty_cap)

        # ── Position management ──
        if position is not None:
            side = position["side"]
            entry = position["entry_price"]
            qty = position["qty"]
            orig = position["orig_qty"]
            sl = position["sl"]
            init_d = position["init_sl_dist"]
            tp1_done = position["tp1_done"]
            tp2_done = position["tp2_done"]

            # Check TP1
            tp1_px = entry + TP1_R * init_d if side == "LONG" else entry - TP1_R * init_d
            tp2_px = entry + TP2_R * init_d if side == "LONG" else entry - TP2_R * init_d

            high_h = bar["high"]; low_h = bar["low"]

            tp1_hit = (high_h >= tp1_px) if side == "LONG" else (low_h <= tp1_px)
            if not tp1_done and tp1_hit:
                # Close TP1_FRAC of orig_qty at tp1_px (with slippage + commission)
                close_qty = orig * TP1_FRAC
                close_qty = min(close_qty, qty)
                fill = tp1_px * (1 - SLIPPAGE_PCT) if side == "LONG" else tp1_px * (1 + SLIPPAGE_PCT)
                pnl = ((fill - entry) if side == "LONG" else (entry - fill)) * close_qty
                pnl -= fill * close_qty * COMMISSION_PCT
                equity += pnl
                qty -= close_qty
                # Move SL to BE+buffer
                be_sl = entry * (1 + BE_BUF_PCT) if side == "LONG" else entry * (1 - BE_BUF_PCT)
                sl = max(sl, be_sl) if side == "LONG" else min(sl, be_sl)
                tp1_done = True
                position.update({"qty": qty, "sl": sl, "tp1_done": tp1_done})

            # Check TP2 (only after TP1)
            tp2_hit = (high_h >= tp2_px) if side == "LONG" else (low_h <= tp2_px)
            if tp1_done and not tp2_done and tp2_hit:
                close_qty = orig * TP2_FRAC
                close_qty = min(close_qty, qty)
                fill = tp2_px * (1 - SLIPPAGE_PCT) if side == "LONG" else tp2_px * (1 + SLIPPAGE_PCT)
                pnl = ((fill - entry) if side == "LONG" else (entry - fill)) * close_qty
                pnl -= fill * close_qty * COMMISSION_PCT
                equity += pnl
                qty -= close_qty
                tp2_done = True
                position.update({"qty": qty, "tp2_done": tp2_done})

            # Trail after TP2
            if tp2_done:
                new_sl = (px - TRAIL_ATR_MULT * atr_v) if side == "LONG" else (px + TRAIL_ATR_MULT * atr_v)
                sl = max(sl, new_sl) if side == "LONG" else min(sl, new_sl)
                position["sl"] = sl

            # Check SL
            sl_hit = (low_h <= sl) if side == "LONG" else (high_h >= sl)
            if sl_hit:
                fill = sl * (1 - SLIPPAGE_PCT) if side == "LONG" else sl * (1 + SLIPPAGE_PCT)
                pnl = ((fill - entry) if side == "LONG" else (entry - fill)) * qty
                pnl -= fill * qty * COMMISSION_PCT
                equity += pnl
                # Cumulative trade PnL including partial exits already booked
                trade_pnl_total = equity - position.get("equity_at_entry", INITIAL_EQUITY)
                trades.append({
                    "bar": i,
                    "time": str(bar["timestamp"]),
                    "side": side,
                    "entry": entry,
                    "exit": fill,
                    "qty": orig,
                    "reason": "SL",
                    "tp1_done": tp1_done,
                    "tp2_done": tp2_done,
                    "pnl": trade_pnl_total,
                    "is_flip": position.get("is_flip", False),
                    "entry_type": position.get("entry_type", "breakout"),
                })
                prev_side = side
                prev_tp1 = tp1_done
                position = None
                last_exit_bar = i

                # SL-FLIP: bias-gated, only if TP1 never hit
                if not prev_tp1 and not halted:
                    want_flip = False
                    if prev_side == "LONG" and bias_d == -1:
                        flip_side = "SHORT"; want_flip = True
                    elif prev_side == "SHORT" and bias_d == 1:
                        flip_side = "LONG"; want_flip = True
                    if want_flip:
                        flip_sl = calc_sl(flip_side, fill)
                        flip_qty = calc_qty(flip_side, fill, flip_sl)
                        if flip_qty > 0:
                            flip_fill = fill * (1 + SLIPPAGE_PCT) if flip_side == "LONG" else fill * (1 - SLIPPAGE_PCT)
                            flip_sl = calc_sl(flip_side, flip_fill)
                            # Entry commission
                            equity -= flip_fill * flip_qty * COMMISSION_PCT
                            position = {
                                "side": flip_side,
                                "entry_price": flip_fill,
                                "qty": flip_qty,
                                "orig_qty": flip_qty,
                                "sl": flip_sl,
                                "init_sl_dist": abs(flip_fill - flip_sl),
                                "tp1_done": False,
                                "tp2_done": False,
                                "entry_bar": i,
                                "is_flip": True,
                                "entry_type": "flip",
                                "equity_at_entry": equity,
                            }
                            last_exit_bar = -10**9  # bypass cooldown for flip

        # ── Entry (only if flat) ──
        if position is None and not halted:
            # Cooldown check
            cd_blocked = (i - last_exit_bar) < GEN_CD_BARS

            # Compute signals
            hh_ok = not pd.isna(ph_last) and not pd.isna(ph_prev) and ph_last > ph_prev
            hl_ok = not pd.isna(pl_last) and not pd.isna(pl_prev) and pl_last > pl_prev
            lh_ok = not pd.isna(ph_last) and not pd.isna(ph_prev) and ph_last < ph_prev
            ll_ok = not pd.isna(pl_last) and not pd.isna(pl_prev) and pl_last < pl_prev

            bull_struct = hh_ok and hl_ok
            bear_struct = lh_ok and ll_ok
            break_up = (not pd.isna(ph_last)) and px > ph_last
            break_dn = (not pd.isna(pl_last)) and px < pl_last

            long_sig  = bias_d == 1  and rsi_v > 50 and bull_struct and break_up
            short_sig = bias_d == -1 and rsi_v < 50 and bear_struct and break_dn

            if not cd_blocked and (long_sig or short_sig):
                side = "LONG" if long_sig else "SHORT"
                entry_px = px * (1 + SLIPPAGE_PCT) if side == "LONG" else px * (1 - SLIPPAGE_PCT)
                sl = calc_sl(side, entry_px)
                qty = calc_qty(side, entry_px, sl)
                if qty > 0:
                    equity -= entry_px * qty * COMMISSION_PCT
                    position = {
                        "side": side, "entry_price": entry_px,
                        "qty": qty, "orig_qty": qty,
                        "sl": sl, "init_sl_dist": abs(entry_px - sl),
                        "tp1_done": False, "tp2_done": False,
                        "entry_bar": i,
                        "is_flip": False, "entry_type": "breakout",
                        "equity_at_entry": equity,
                    }

        equity_curve.append(equity)

    # ── Force-close final open position at last bar ──
    if position is not None:
        bar = df.iloc[-1]
        fill = bar["close"]
        pnl = ((fill - position["entry_price"]) if position["side"] == "LONG"
               else (position["entry_price"] - fill)) * position["qty"]
        pnl -= fill * position["qty"] * COMMISSION_PCT
        equity += pnl
        trades.append({
            "bar": len(df)-1, "time": str(bar["timestamp"]),
            "side": position["side"], "entry": position["entry_price"],
            "exit": fill, "qty": position["orig_qty"], "reason": "EOD_close",
            "tp1_done": position["tp1_done"], "tp2_done": position["tp2_done"],
            "pnl": equity - position.get("equity_at_entry", INITIAL_EQUITY),
            "is_flip": position.get("is_flip", False),
            "entry_type": position.get("entry_type", "breakout"),
        })

    # ── Metrics ──
    eq = pd.Series(equity_curve)
    running_peak = eq.cummax()
    dd_series = (eq - running_peak) / running_peak
    max_dd = abs(dd_series.min()) if len(dd_series) else 0
    net_pct = (equity / INITIAL_EQUITY - 1) * 100

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gross_win = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    wr = len(wins) / len(trades) * 100 if trades else 0

    # CAGR over the actual period
    if len(df) >= 2:
        span_years = (df.iloc[-1]["timestamp"] - df.iloc[0]["timestamp"]).total_seconds() / (365.25 * 86400)
        cagr = ((equity / INITIAL_EQUITY) ** (1 / span_years) - 1) * 100 if span_years > 0 and equity > 0 else 0
    else:
        cagr = 0; span_years = 0
    calmar = cagr / (max_dd * 100) if max_dd > 0 else float("inf")

    flip_trades = [t for t in trades if t.get("is_flip")]

    return {
        "label": label,
        "bars": len(df),
        "span_years": span_years,
        "net_pct": net_pct,
        "cagr": cagr,
        "max_dd": max_dd * 100,
        "calmar": calmar,
        "pf": pf,
        "trades": len(trades),
        "wr": wr,
        "flip_trades": len(flip_trades),
        "final_equity": equity,
    }


# ═════ Main ═════
def main():
    global DD_HALT_BARS, GEN_CD_BARS

    # Parse args: [interval] [pairs...] [--from YYYY-MM-DD] [--to YYYY-MM-DD]
    args = sys.argv[1:]
    interval = "1d"
    pairs = []
    start_date = None
    end_date = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("1d", "4h", "1h", "15m"):
            interval = a
        elif a == "--from" and i + 1 < len(args):
            start_date = pd.Timestamp(args[i+1]); i += 1
        elif a == "--to" and i + 1 < len(args):
            end_date = pd.Timestamp(args[i+1]); i += 1
        else:
            pairs.append(a)
        i += 1
    if not pairs:
        pairs = ["BTCUSDT", "BNBUSDT", "SOLUSDT"]

    # Use Pine's DEFAULTS (bar counts, not day counts) — same for every TF.
    # This matches how TV backtests V2b when you just swap the chart TF.
    DD_HALT_BARS = 7
    GEN_CD_BARS  = 3

    print(f"\n{'='*80}\nV2b {interval} Backtest — pairs: {pairs}"
          f"\n  halt: {DD_HALT_BARS} bars | cooldown: {GEN_CD_BARS} bars"
          f"\n{'='*80}")

    results = []
    for symbol in pairs:
        print(f"\n─── {symbol} {interval} ───")
        df = fetch_klines(symbol, interval)
        if df.empty:
            print(f"  skipped: no data"); continue
        print(f"  bars: {len(df)} | range: {df.iloc[0]['timestamp']} → {df.iloc[-1]['timestamp']}")
        df = build_features(df)
        # Apply date filter AFTER features built (so EMA/RSI have warmup data)
        if start_date is not None:
            df = df[df["timestamp"] >= start_date].reset_index(drop=True)
        if end_date is not None:
            df = df[df["timestamp"] <= end_date].reset_index(drop=True)
        if start_date or end_date:
            print(f"  filtered to: {df.iloc[0]['timestamp']} → {df.iloc[-1]['timestamp']} ({len(df)} bars)")
        print(f"  running backtest...")
        res = backtest(df, label=symbol)
        results.append(res)

    # Summary table
    print(f"\n{'='*80}\nSUMMARY ({interval} V2b)\n{'='*80}")
    print(f"{'Pair':<10} {'Span':>6} {'Net%':>10} {'CAGR%':>8} {'DD%':>7} {'Calmar':>8} {'PF':>6} {'Trades':>7} {'WR%':>6} {'Flips':>6}")
    print("─" * 80)
    for r in results:
        print(f"{r['label']:<10} {r['span_years']:>5.1f}y "
              f"{r['net_pct']:>+9.1f}% {r['cagr']:>+7.1f}% "
              f"{r['max_dd']:>6.1f}% {r['calmar']:>7.2f} "
              f"{r['pf']:>5.2f} {r['trades']:>7} {r['wr']:>5.1f}% {r['flip_trades']:>6}")
    print("═" * 80)
    if interval == "1d":
        print(f"\nTV BTC 1D V2b baseline: +1579% net, 30.7% DD, PF 3.31, 39 trades, 54% WR (6.6y)")
        print(f"Our BTC number above should be within ~10-20% of this if the Python port is faithful.")


if __name__ == "__main__":
    main()
