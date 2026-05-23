"""Divergence-Flip backtest with optional 15m / 1h S/R filter.

Tests whether gating divflip entries on short-term S/R touch (prev-15m or
prev-1h bar's high/low) rescues it from its documented OOS −100% — which
had NO S/R filter (it fired on every fresh divergence regardless of where
price was). The S/R touch is exactly the structural difference between
divflip and the sr_dca bot — sr_dca's losses stay small because it only
enters near a prev-day H/L.

Variants run on the same window:
  - none  : baseline (= original div_flip_backtest)
  - 15m   : enter only if the firing bar touches the prev 15-minute H/L
  - 1h    : enter only if the firing bar touches the prev 1-hour H/L
"""
from __future__ import annotations
import sys
from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import v22_backtest as bt
from div_flip_backtest import (INITIAL, SL_PCT, TP1_PCT, TP2_PCT, LEV, COMM,
                               detect_divergences)

SUPPORT_ZONE = 0.0005   # 0.05% touch zone — matches sr_dca


def attach_period_sr(df_5m: pd.DataFrame, period_min: int) -> pd.DataFrame:
    """For each 5m bar, attach its prev period's H/L."""
    df = df_5m.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    period = f"{period_min}min"
    s = df.set_index("timestamp")
    pH = s["high"].resample(period, label="left").max().shift(1)
    pL = s["low"].resample(period, label="left").min().shift(1)
    floor = df["timestamp"].dt.floor(period)
    df["prev_pH"] = floor.map(pH).values
    df["prev_pL"] = floor.map(pL).values
    return df


def _touch_long(L: float, prev_pL) -> bool:
    if prev_pL is None or pd.isna(prev_pL):
        return False
    return L <= prev_pL * (1 + SUPPORT_ZONE) and L > prev_pL * (1 - 0.01)


def _touch_short(H: float, prev_pH) -> bool:
    if prev_pH is None or pd.isna(prev_pH):
        return False
    return H >= prev_pH * (1 - SUPPORT_ZONE) and H < prev_pH * (1 + 0.01)


def run(start: str, end: str, sr_tf_min: int):
    """sr_tf_min: 0 → no filter (baseline). 15 → 15m S/R. 60 → 1h S/R."""
    df = bt.load_5m(start, end)
    if df.empty:
        return [], INITIAL, 0.0
    if sr_tf_min:
        df = attach_period_sr(df, sr_tf_min)

    bull_fired, bear_fired = detect_divergences(df)

    equity = INITIAL
    peak_equity = INITIAL
    max_dd = 0.0
    trades = []

    in_pos = False
    side = ""
    entry_px = 0.0
    qty_full = 0.0
    qty_remaining = 0.0
    half_taken = False
    entry_time = ""

    def close_remaining(j, fill, reason):
        nonlocal equity, in_pos, side, entry_px, qty_full, qty_remaining, half_taken, peak_equity, max_dd
        pnl = (fill - entry_px) * qty_remaining if side == "LONG" else (entry_px - fill) * qty_remaining
        pnl -= fill * qty_remaining * COMM
        equity += pnl
        trades.append({"side": side, "entry": entry_px, "exit": fill,
                       "qty": qty_remaining, "pnl_usd": pnl, "reason": reason,
                       "entry_time": entry_time,
                       "exit_time": df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")})
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity * 100
        if dd > max_dd:
            max_dd = dd
        in_pos = False

    def partial_take(j, fill, qty_part, reason):
        nonlocal equity, qty_remaining, half_taken, peak_equity, max_dd
        pnl = (fill - entry_px) * qty_part if side == "LONG" else (entry_px - fill) * qty_part
        pnl -= fill * qty_part * COMM
        equity += pnl
        qty_remaining -= qty_part
        half_taken = True
        trades.append({"side": side, "entry": entry_px, "exit": fill,
                       "qty": qty_part, "pnl_usd": pnl, "reason": reason,
                       "entry_time": entry_time,
                       "exit_time": df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")})
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity * 100
        if dd > max_dd:
            max_dd = dd

    def can_open(j: int, direction: str) -> bool:
        if not sr_tf_min:
            return True
        if direction == "long":
            return _touch_long(df.at[j, "low"], df.at[j, "prev_pL"])
        return _touch_short(df.at[j, "high"], df.at[j, "prev_pH"])

    for j in range(len(df)):
        C = df.at[j, "close"]
        H = df.at[j, "high"]
        L_ = df.at[j, "low"]

        if in_pos:
            # SL
            if side == "LONG" and L_ <= entry_px * (1 - SL_PCT):
                close_remaining(j, entry_px * (1 - SL_PCT), "SL"); continue
            if side == "SHORT" and H >= entry_px * (1 + SL_PCT):
                close_remaining(j, entry_px * (1 + SL_PCT), "SL"); continue
            # TP1 (50%)
            if not half_taken:
                if side == "LONG" and H >= entry_px * (1 + TP1_PCT):
                    partial_take(j, entry_px * (1 + TP1_PCT), qty_full * 0.5, "TP1")
                elif side == "SHORT" and L_ <= entry_px * (1 - TP1_PCT):
                    partial_take(j, entry_px * (1 - TP1_PCT), qty_full * 0.5, "TP1")
            # TP2 (remaining 50%)
            if half_taken:
                if side == "LONG" and H >= entry_px * (1 + TP2_PCT):
                    close_remaining(j, entry_px * (1 + TP2_PCT), "TP2"); continue
                if side == "SHORT" and L_ <= entry_px * (1 - TP2_PCT):
                    close_remaining(j, entry_px * (1 - TP2_PCT), "TP2"); continue
            # Flip on opposite — also gated by SR filter
            if side == "LONG" and bear_fired[j]:
                close_remaining(j, C, "FLIP")
                if can_open(j, "short") and (equity * 0.95 * LEV) / C > 0:
                    qty_full = (equity * 0.95 * LEV) / C
                    equity -= C * qty_full * COMM
                    in_pos = True; side = "SHORT"; entry_px = C
                    qty_remaining = qty_full; half_taken = False
                    entry_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")
                continue
            elif side == "SHORT" and bull_fired[j]:
                close_remaining(j, C, "FLIP")
                if can_open(j, "long") and (equity * 0.95 * LEV) / C > 0:
                    qty_full = (equity * 0.95 * LEV) / C
                    equity -= C * qty_full * COMM
                    in_pos = True; side = "LONG"; entry_px = C
                    qty_remaining = qty_full; half_taken = False
                    entry_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")
                continue

        if not in_pos:
            if bull_fired[j] and can_open(j, "long"):
                qty_full = (equity * 0.95 * LEV) / C
                if qty_full > 0:
                    equity -= C * qty_full * COMM
                    in_pos = True; side = "LONG"; entry_px = C
                    qty_remaining = qty_full; half_taken = False
                    entry_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")
            elif bear_fired[j] and can_open(j, "short"):
                qty_full = (equity * 0.95 * LEV) / C
                if qty_full > 0:
                    equity -= C * qty_full * COMM
                    in_pos = True; side = "SHORT"; entry_px = C
                    qty_remaining = qty_full; half_taken = False
                    entry_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")

    return trades, equity, max_dd


def summarize(trades, equity, max_dd):
    if not trades:
        return {"trades": 0, "equity": equity, "pct": 0.0, "wr": 0.0, "pf": 0.0, "dd": 0.0}
    df_t = pd.DataFrame(trades)
    by_cycle = df_t.groupby(["entry_time", "side"], sort=False).agg(
        cycle_pnl=("pnl_usd", "sum")).reset_index()
    wins = (by_cycle["cycle_pnl"] > 0).sum()
    n = len(by_cycle)
    gw = by_cycle.loc[by_cycle["cycle_pnl"] > 0, "cycle_pnl"].sum()
    gl = -by_cycle.loc[by_cycle["cycle_pnl"] < 0, "cycle_pnl"].sum()
    pf = gw / gl if gl > 0 else float("inf")
    return {"trades": int(n), "equity": equity, "pct": (equity / INITIAL - 1) * 100,
            "wr": wins / n * 100 if n else 0.0, "pf": pf, "dd": max_dd}


def main():
    start = "2021-05-01"
    end = dt.datetime.utcnow().strftime("%Y-%m-%d")
    if len(sys.argv) >= 2: start = sys.argv[1]
    if len(sys.argv) >= 3: end = sys.argv[2]

    variants = [("none (baseline)", 0), ("15m S/R", 15), ("1h S/R", 60)]
    print(f"Divflip + S/R filter backtest — BTCUSDT 5m, {start} → {end}\n")

    results = []
    for label, tf in variants:
        print(f"  running {label}...", flush=True)
        trades, equity, dd = run(start, end, tf)
        s = summarize(trades, equity, dd)
        results.append((label, s))

    print(f"\n=== FULL PERIOD: {start} → {end} ===")
    print(f"{'Variant':<18} {'Trades':>8} {'Return':>12} {'WR':>7} {'PF':>7} {'MaxDD':>8}")
    print("-" * 65)
    for label, s in results:
        ret = f"{s['pct']:+,.1f}%"
        wr = f"{s['wr']:.0f}%"
        pf = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
        dd = f"{s['dd']:.1f}%"
        print(f"{label:<18} {s['trades']:>8d} {ret:>12} {wr:>7} {pf:>7} {dd:>8}")


if __name__ == "__main__":
    main()
