"""Divergence-flip strategy backtest.

Spec (user-requested 2026-05-13):
  - Entry: fresh bull div -> LONG, fresh bear div -> SHORT
  - No S/R touch required, no volume filter
  - TPs: 50% qty at +0.5%, 50% qty at +1%
  - SL: 2% hard from entry (added for safety)
  - Opposite divergence -> exit remaining qty, flip
  - No EOD flatten, no cycle/day cap
  - Multi-cycle, always in market between trades

Compare against V2.2 baseline on same window.
"""
from __future__ import annotations
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import v22_backtest as bt

INITIAL = 5000.0
SL_PCT = 0.02
TP1_PCT = 0.005
TP2_PCT = 0.01
LEV = 2.0
COMM = 0.0004


def detect_divergences(df: pd.DataFrame):
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    rsi_arr = bt.rsi(closes, bt.RSI_PERIOD)

    n = len(df)
    bear_fired = np.zeros(n, dtype=bool)
    bull_fired = np.zeros(n, dtype=bool)
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
            if lows[i] == wl.min() and (wl == lows[i]).sum() == 1:
                prev_PL = last_PL
                prev_RatL = last_RatL
                last_PL = lows[i]
                last_RatL = rsi_arr[i]
                if not np.isnan(prev_PL) and last_PL < prev_PL and last_RatL > prev_RatL:
                    bull_fired[j] = True
    return bull_fired, bear_fired


def run(start: str, end: str):
    df = bt.load_5m(start, end)
    if df.empty:
        print(f"No data in {start}..{end}")
        return [], INITIAL, 0.0

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
        if side == "LONG":
            pnl = (fill - entry_px) * qty_remaining
        else:
            pnl = (entry_px - fill) * qty_remaining
        pnl -= fill * qty_remaining * COMM
        equity += pnl
        trades.append({
            "side": side,
            "entry": entry_px,
            "exit": fill,
            "qty": qty_remaining,
            "pnl_usd": pnl,
            "reason": reason,
            "entry_time": entry_time,
            "exit_time": df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M"),
        })
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity * 100
        if dd > max_dd:
            max_dd = dd
        in_pos = False

    def partial_take(j, fill, qty_part, reason):
        nonlocal equity, qty_remaining, half_taken, peak_equity, max_dd
        if side == "LONG":
            pnl = (fill - entry_px) * qty_part
        else:
            pnl = (entry_px - fill) * qty_part
        pnl -= fill * qty_part * COMM
        equity += pnl
        qty_remaining -= qty_part
        half_taken = True
        trades.append({
            "side": side,
            "entry": entry_px,
            "exit": fill,
            "qty": qty_part,
            "pnl_usd": pnl,
            "reason": reason,
            "entry_time": entry_time,
            "exit_time": df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M"),
        })
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity * 100
        if dd > max_dd:
            max_dd = dd

    for j in range(len(df)):
        C = df.at[j, "close"]
        H = df.at[j, "high"]
        L_ = df.at[j, "low"]

        # ── Manage open position ──
        if in_pos:
            # SL first (worst case)
            if side == "LONG" and L_ <= entry_px * (1 - SL_PCT):
                close_remaining(j, entry_px * (1 - SL_PCT), "SL")
                continue
            if side == "SHORT" and H >= entry_px * (1 + SL_PCT):
                close_remaining(j, entry_px * (1 + SL_PCT), "SL")
                continue

            # Partial TP1 (50%)
            if not half_taken:
                if side == "LONG" and H >= entry_px * (1 + TP1_PCT):
                    partial_take(j, entry_px * (1 + TP1_PCT), qty_full * 0.5, "TP1")
                elif side == "SHORT" and L_ <= entry_px * (1 - TP1_PCT):
                    partial_take(j, entry_px * (1 - TP1_PCT), qty_full * 0.5, "TP1")

            # Final TP2 (remaining 50%)
            if half_taken:
                if side == "LONG" and H >= entry_px * (1 + TP2_PCT):
                    close_remaining(j, entry_px * (1 + TP2_PCT), "TP2")
                    continue
                if side == "SHORT" and L_ <= entry_px * (1 - TP2_PCT):
                    close_remaining(j, entry_px * (1 - TP2_PCT), "TP2")
                    continue

            # Flip on opposite signal
            if side == "LONG" and bear_fired[j]:
                close_remaining(j, C, "FLIP")
                # Immediately open SHORT
                qty_full = (equity * 0.95 * LEV) / C
                if qty_full > 0:
                    equity -= C * qty_full * COMM
                    in_pos = True
                    side = "SHORT"
                    entry_px = C
                    qty_remaining = qty_full
                    half_taken = False
                    entry_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")
                continue
            elif side == "SHORT" and bull_fired[j]:
                close_remaining(j, C, "FLIP")
                qty_full = (equity * 0.95 * LEV) / C
                if qty_full > 0:
                    equity -= C * qty_full * COMM
                    in_pos = True
                    side = "LONG"
                    entry_px = C
                    qty_remaining = qty_full
                    half_taken = False
                    entry_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")
                continue

        # ── Open fresh position ──
        if not in_pos:
            if bull_fired[j]:
                qty_full = (equity * 0.95 * LEV) / C
                if qty_full > 0:
                    equity -= C * qty_full * COMM
                    in_pos = True
                    side = "LONG"
                    entry_px = C
                    qty_remaining = qty_full
                    half_taken = False
                    entry_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")
            elif bear_fired[j]:
                qty_full = (equity * 0.95 * LEV) / C
                if qty_full > 0:
                    equity -= C * qty_full * COMM
                    in_pos = True
                    side = "SHORT"
                    entry_px = C
                    qty_remaining = qty_full
                    half_taken = False
                    entry_time = df.at[j, "timestamp"].strftime("%Y-%m-%d %H:%M")

    return trades, equity, max_dd


def main():
    import datetime as dt
    end = dt.datetime.utcnow().strftime("%Y-%m-%d")
    start = (dt.datetime.utcnow() - dt.timedelta(days=365)).strftime("%Y-%m-%d")
    if len(sys.argv) >= 2:
        start = sys.argv[1]
    if len(sys.argv) >= 3:
        end = sys.argv[2]

    print(f"Div-Flip backtest: {start} -> {end}")
    print(f"Entry: every fresh div. TP1 +0.5% (50%) / TP2 +1% (50%) / SL 2% / Flip on opposite.")
    print(f"No EOD, no cycle cap. Lev {LEV}x, comm {COMM*100}%/side, $5000 start.\n")

    trades, equity, dd = run(start, end)
    if not trades:
        print("No trades.")
        return

    # Aggregate by entry cycle (group consecutive TP1/TP2 with same entry_time)
    df_t = pd.DataFrame(trades)
    by_cycle = df_t.groupby(["entry_time", "side"], sort=False).agg(
        cycle_pnl=("pnl_usd", "sum"),
        legs=("pnl_usd", "size"),
        last_reason=("reason", "last"),
    ).reset_index()

    wins = (by_cycle["cycle_pnl"] > 0).sum()
    losses = (by_cycle["cycle_pnl"] < 0).sum()
    n = len(by_cycle)
    total_pnl = equity - INITIAL
    pct = (equity / INITIAL - 1) * 100
    gw = by_cycle.loc[by_cycle["cycle_pnl"] > 0, "cycle_pnl"].sum()
    gl = -by_cycle.loc[by_cycle["cycle_pnl"] < 0, "cycle_pnl"].sum()
    pf = gw / gl if gl > 0 else float("inf")

    print(f"=== DIV-FLIP RESULT ===")
    print(f"Final equity:    ${equity:,.2f}  ({pct:+.2f}%)")
    print(f"Cycles (trades): {n}  (wins {wins} / losses {losses})")
    print(f"Win rate:        {wins/n*100:.1f}%")
    print(f"Profit factor:   {pf:.2f}")
    print(f"Max drawdown:    {dd:.2f}%")
    print(f"Avg cycle:       ${total_pnl/n:+,.2f}")
    print()

    # Exit-reason breakdown
    reason_counts = df_t["reason"].value_counts().to_dict()
    reason_pnl = df_t.groupby("reason")["pnl_usd"].sum().to_dict()
    print("Exit reasons (per fill):")
    for r in sorted(reason_counts):
        print(f"  {r:5s} count={reason_counts[r]:5d}  pnl=${reason_pnl[r]:+,.2f}")

    # Run V2.2 baseline on the same window for comparison
    print()
    print(f"=== V2.2 BASELINE (same window) ===")
    bt_trades, bt_eq, bt_dd = bt.run_backtest(start, end)
    if bt_trades:
        bt_wins = sum(1 for t in bt_trades if t.pnl_usd > 0)
        bt_gw = sum(t.pnl_usd for t in bt_trades if t.pnl_usd > 0)
        bt_gl = -sum(t.pnl_usd for t in bt_trades if t.pnl_usd < 0)
        bt_pf = bt_gw / bt_gl if bt_gl > 0 else float("inf")
        bt_pct = (bt_eq / INITIAL - 1) * 100
        print(f"Final equity:    ${bt_eq:,.2f}  ({bt_pct:+.2f}%)")
        print(f"Trades:          {len(bt_trades)}  (wins {bt_wins})")
        print(f"Win rate:        {bt_wins/len(bt_trades)*100:.1f}%")
        print(f"Profit factor:   {bt_pf:.2f}")
        print(f"Max drawdown:    {bt_dd:.2f}%")

    # Save div-flip trades
    out = Path(__file__).parent / "results" / "div_flip_trades.csv"
    df_t.to_csv(out, index=False)
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
