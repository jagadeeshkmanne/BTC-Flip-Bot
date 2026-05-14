"""Divergence-Flip V2 backtest — full new config.

Spec:
  - Entry: fresh bull div (RSI at pivot ≤ 30) → LONG
           fresh bear div (RSI at pivot ≥ 70) → SHORT
  - DCA: 3 fixed levels at 0.6% (L1, L2 at -0.6%, L3 at -1.2% from L1)
  - SL: 2% from L1 (anchored, doesn't widen with DCA)
  - BE arms at +0.5% favorable from L1
  - Trailing SL after BE: peak ± 0.2%
  - BE buffer (initial floor): firstEntry ± 0.2%
  - Flip on opposite divergence (with RSI filter)
  - No hard TP, no EOD, multi-cycle
  - Leverage 2×, $5K start, 0.04% fee/side

Tests various DIV_FRESH_BARS values.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent / "strategies" / "day"))
sys.path.insert(0, str(Path(__file__).parent))

import v22_backtest as bt
from core import build_features

INITIAL = 5000.0
SL_PCT = 0.02            # SL anchored to L1
DCA_LEVELS = 3
DCA_SPACING = 0.006
BE_TRIGGER = 0.005
BE_BUFFER = 0.002
TRAIL_DIST = 0.002
RSI_LONG_MAX = 30
RSI_SHORT_MIN = 70
LEV = 2.0
COMM = 0.0004
DIV_PIVOT_R = 5


def detect_div_with_rsi(df: pd.DataFrame):
    """Compute bull/bear div fired arrays + RSI at pivot for each."""
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    rsi_arr = bt.rsi(closes, bt.RSI_PERIOD)
    n = len(df)
    bear_fired = np.zeros(n, dtype=bool)
    bull_fired = np.zeros(n, dtype=bool)
    bear_pivot_rsi = np.full(n, np.nan)
    bull_pivot_rsi = np.full(n, np.nan)
    last_PH = prev_PH = np.nan
    last_RatH = prev_RatH = np.nan
    last_PL = prev_PL = np.nan
    last_RatL = prev_RatL = np.nan
    L, R = bt.DIV_PIVOT_L, bt.DIV_PIVOT_R
    for j in range(n):
        i = j - R
        if i - L >= 0:
            wh = highs[i - L:j + 1]
            wl = lows[i - L:j + 1]
            if highs[i] == wh.max() and (wh == highs[i]).sum() == 1:
                prev_PH = last_PH
                prev_RatH = last_RatH
                last_PH = highs[i]
                last_RatH = rsi_arr[i]
                if not np.isnan(prev_PH) and last_PH > prev_PH and last_RatH < prev_RatH:
                    bear_fired[j] = True
                    bear_pivot_rsi[j] = rsi_arr[i]
            if lows[i] == wl.min() and (wl == lows[i]).sum() == 1:
                prev_PL = last_PL
                prev_RatL = last_RatL
                last_PL = lows[i]
                last_RatL = rsi_arr[i]
                if not np.isnan(prev_PL) and last_PL < prev_PL and last_RatL > prev_RatL:
                    bull_fired[j] = True
                    bull_pivot_rsi[j] = rsi_arr[i]
    return bull_fired, bear_fired, bull_pivot_rsi, bear_pivot_rsi, rsi_arr


def run(start: str, end: str, div_fresh_bars: int = 10, use_rsi_filter: bool = True):
    df = bt.load_5m(start, end)
    if df.empty:
        return [], INITIAL, 0.0
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values

    bull_fired, bear_fired, bull_pivot_rsi, bear_pivot_rsi, rsi_arr = detect_div_with_rsi(df)

    # bars_since trackers with RSI-filter built in
    n = len(df)
    bars_since_bull = np.full(n, 9999, dtype=int)
    bars_since_bear = np.full(n, 9999, dtype=int)
    last_bull_rsi = np.full(n, np.nan)
    last_bear_rsi = np.full(n, np.nan)
    cb, cu = 9999, 9999
    last_bull_pivot_rsi = np.nan
    last_bear_pivot_rsi = np.nan
    for j in range(n):
        if bull_fired[j]:
            cu = 0
            last_bull_pivot_rsi = bull_pivot_rsi[j]
        else:
            cu = min(cu + 1, 9999)
        if bear_fired[j]:
            cb = 0
            last_bear_pivot_rsi = bear_pivot_rsi[j]
        else:
            cb = min(cb + 1, 9999)
        bars_since_bull[j] = cu
        bars_since_bear[j] = cb
        last_bull_rsi[j] = last_bull_pivot_rsi
        last_bear_rsi[j] = last_bear_pivot_rsi

    equity = INITIAL
    peak_equity = INITIAL
    max_dd = 0.0
    trades = []

    in_pos = False
    side = ""
    first_entry = 0.0
    worst_entry = 0.0
    peak_price = 0.0
    entries = []   # list of (px, qty)
    be_activated = False
    entry_time = ""

    def avg_entry():
        total_q = sum(q for _, q in entries)
        if total_q <= 0:
            return first_entry
        return sum(p * q for p, q in entries) / total_q

    def per_leg_qty(eq, px):
        if px <= 0:
            return 0.0
        return (eq * 0.95 * LEV) / px / DCA_LEVELS

    def close_full(j, fill, reason):
        nonlocal equity, in_pos, peak_equity, max_dd
        total_q = sum(q for _, q in entries)
        avg = avg_entry()
        if side == "LONG":
            pnl = (fill - avg) * total_q
        else:
            pnl = (avg - fill) * total_q
        pnl -= fill * total_q * COMM
        equity += pnl
        trades.append({
            "side": side, "first": first_entry, "avg": avg, "exit": fill,
            "qty": total_q, "legs": len(entries),
            "pnl_usd": pnl, "reason": reason,
            "entry_time": entry_time,
            "exit_time": df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M"),
        })
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity * 100
        if dd > max_dd:
            max_dd = dd
        in_pos = False

    for j in range(n):
        C = closes[j]
        H = highs[j]
        L_ = lows[j]

        # Check entry signal (fresh div within window + RSI-at-pivot filter)
        bull_ok = bars_since_bull[j] <= div_fresh_bars
        bear_ok = bars_since_bear[j] <= div_fresh_bars
        if use_rsi_filter:
            if bull_ok and (np.isnan(last_bull_rsi[j]) or last_bull_rsi[j] > RSI_LONG_MAX):
                bull_ok = False
            if bear_ok and (np.isnan(last_bear_rsi[j]) or last_bear_rsi[j] < RSI_SHORT_MIN):
                bear_ok = False

        if in_pos:
            # Update peak
            if side == "LONG":
                peak_price = max(peak_price, H)
            else:
                peak_price = min(peak_price, L_)

            # DCA — fixed distance from worst entry
            if len(entries) < DCA_LEVELS:
                dca_trig = worst_entry * (1 - DCA_SPACING) if side == "LONG" else worst_entry * (1 + DCA_SPACING)
                crossed = (side == "LONG" and L_ <= dca_trig) or (side == "SHORT" and H >= dca_trig)
                if crossed:
                    qty = round(per_leg_qty(equity, dca_trig), 3)
                    # Leverage cap
                    cur_total = sum(q for _, q in entries)
                    max_tot = (equity * 0.95 * LEV) / dca_trig
                    if cur_total + qty > max_tot:
                        qty = round(max(0, max_tot - cur_total), 3)
                    if qty > 0:
                        equity -= dca_trig * qty * COMM
                        entries.append((dca_trig, qty))
                        if side == "LONG":
                            worst_entry = min(worst_entry, dca_trig)
                        else:
                            worst_entry = max(worst_entry, dca_trig)

            # BE arm check (uses first_entry, sticky)
            if not be_activated:
                fav = (C - first_entry) / first_entry if side == "LONG" else (first_entry - C) / first_entry
                if fav >= BE_TRIGGER:
                    be_activated = True

            # Composite SL — anchored to L1 (first_entry × 0.98 for LONG)
            raw_sl = first_entry * (1 - SL_PCT) if side == "LONG" else first_entry * (1 + SL_PCT)
            if be_activated:
                be_sl = first_entry * (1 + BE_BUFFER) if side == "LONG" else first_entry * (1 - BE_BUFFER)
                trail_sl = peak_price * (1 - TRAIL_DIST) if side == "LONG" else peak_price * (1 + TRAIL_DIST)
                sl_px = max(raw_sl, be_sl, trail_sl) if side == "LONG" else min(raw_sl, be_sl, trail_sl)
            else:
                sl_px = raw_sl

            # Exit checks
            exit_reason = None
            exit_px = None
            if side == "LONG" and L_ <= sl_px:
                # Classify: TRAIL / BE / SL
                if be_activated and sl_px > first_entry * (1 + BE_BUFFER) + 0.01:
                    exit_reason = "TRAIL"
                elif be_activated:
                    exit_reason = "BE"
                else:
                    exit_reason = "SL"
                exit_px = sl_px
            elif side == "SHORT" and H >= sl_px:
                if be_activated and sl_px < first_entry * (1 - BE_BUFFER) - 0.01:
                    exit_reason = "TRAIL"
                elif be_activated:
                    exit_reason = "BE"
                else:
                    exit_reason = "SL"
                exit_px = sl_px

            # Flip on opposite signal (filter-gated)
            if exit_px is None:
                if side == "LONG" and bear_ok:
                    exit_reason = "FLIP"
                    exit_px = C
                elif side == "SHORT" and bull_ok:
                    exit_reason = "FLIP"
                    exit_px = C

            if exit_px is not None:
                close_full(j, exit_px, exit_reason)
                # Open reverse if flip
                if exit_reason == "FLIP":
                    new_side = "SHORT" if side == "LONG" else "LONG"
                    qty = round(per_leg_qty(equity, C), 3)
                    if qty > 0:
                        equity -= C * qty * COMM
                        in_pos = True
                        side = new_side
                        first_entry = C
                        worst_entry = C
                        peak_price = C
                        entries = [(C, qty)]
                        be_activated = False
                        entry_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")

        # Open new position if flat and signal fires
        if not in_pos and (bull_ok or bear_ok):
            new_side = "LONG" if bull_ok and (not bear_ok or bars_since_bull[j] <= bars_since_bear[j]) else "SHORT"
            qty = round(per_leg_qty(equity, C), 3)
            if qty > 0:
                equity -= C * qty * COMM
                in_pos = True
                side = new_side
                first_entry = C
                worst_entry = C
                peak_price = C
                entries = [(C, qty)]
                be_activated = False
                entry_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")

    return trades, equity, max_dd


def summarize(label, trades, equity, dd):
    wins = sum(1 for t in trades if t["pnl_usd"] > 0)
    n = len(trades)
    pct = (equity / INITIAL - 1) * 100
    if n == 0:
        print(f"{label:30s}  trades=0  no signals")
        return
    gw = sum(t["pnl_usd"] for t in trades if t["pnl_usd"] > 0)
    gl = -sum(t["pnl_usd"] for t in trades if t["pnl_usd"] < 0)
    pf = gw / gl if gl > 0 else float("inf")
    # Avg by reason
    by_reason = {}
    for t in trades:
        r = t["reason"]
        by_reason.setdefault(r, []).append(t["pnl_usd"])
    reason_str = "  ".join(f"{r}={len(v)}(${sum(v):+.0f})" for r, v in sorted(by_reason.items()))
    print(f"{label:30s}  trades={n:3d}  wins={wins:2d}  WR={wins/n*100:>4.0f}%  PnL={pct:+6.2f}%  PF={pf:>4.2f}  DD={dd:>4.1f}%  | {reason_str}")


def main():
    import datetime as dt
    start = (dt.datetime.utcnow() - dt.timedelta(days=30)).strftime("%Y-%m-%d")
    end = dt.datetime.utcnow().strftime("%Y-%m-%d")
    print(f"Backtest window: {start} → {end}\n")
    print(f"Config: 3×0.6% DCA / 2% SL from L1 / BE@0.5%, buffer 0.2%, trail 0.2% / RSI filter <30 / >70\n")
    print(f"{'Variant':30s}  {'stats':100s}")
    print("-" * 145)
    for bars in [5, 10, 20, 50, 100]:
        trades, eq, dd = run(start, end, div_fresh_bars=bars, use_rsi_filter=True)
        summarize(f"RSI filter ON, fresh={bars}b", trades, eq, dd)
    print()
    print("Reference (no RSI filter):")
    print("-" * 145)
    for bars in [5, 10, 20]:
        trades, eq, dd = run(start, end, div_fresh_bars=bars, use_rsi_filter=False)
        summarize(f"RSI filter OFF, fresh={bars}b", trades, eq, dd)


if __name__ == "__main__":
    main()
