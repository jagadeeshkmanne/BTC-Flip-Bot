#!/usr/bin/env python3
"""pump_fade_confirm.py — user refinement of the pump-fade short (FINDINGS #10):
don't short the pump blindly; wait for MOMENTUM TO STALL + candle confirmation.

Entry variants (after a >= +30% day, watching the next 3 daily candles):
  BLIND      short at pump-day close (baseline — portfolio-ruinous, see
             pump_fade_portfolio.py)
  RED        short at close of the first RED day (close < open)
  BREAKDOWN  short at close of the first day that CLOSES BELOW the prior
             day's LOW (momentum actually broken)
Exit variants from entry:
  1d / 3d    fixed close-to-close
  MOMO       exit at close of first GREEN day after entry (bounce = momentum
             back), max 5 days
Universe: LIQUID only (30d median turnover >= $10M — the only universe that
was even close to survivable). Costs 0.15%/round-trip. Funding NOT in sim
(measured separately: mean -1.0%/d of notional AGAINST the short).

Per-event stats + full portfolio sim (same rules as pump_fade_portfolio.py:
$5k start, 10% equity margin/position, max 10 concurrent, isolated liq at
high >= entry*(1+1/L-0.5%)).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pump_fade_portfolio import (fetch_all_daily, build_events, FEE, START,
                                 MARGIN_FRAC, MAX_POS, MMR)

COST = 0.0015
WAIT = 3            # days to wait for confirmation after the pump day
MOMO_MAX = 5        # max hold for momentum exit


def trigger_day(df: pd.DataFrame, j: int, mode: str) -> int | None:
    """Index of the confirmation day after pump day j, or None."""
    o, c, lo = df["open"].values, df["close"].values, df["low"].values
    for k in range(j + 1, min(j + 1 + WAIT, len(df))):
        if mode == "blind":
            return j
        if mode == "red" and c[k] < o[k]:
            return k
        if mode == "breakdown" and c[k] < lo[k - 1]:
            return k
    return j if mode == "blind" else None


def exit_day(df: pd.DataFrame, k: int, mode: str) -> int | None:
    o, c = df["open"].values, df["close"].values
    if mode in ("1d", "3d"):
        h = int(mode[0])
        return k + h if k + h < len(df) else None
    for m in range(k + 1, min(k + 1 + MOMO_MAX, len(df))):   # momo exit
        if c[m] > o[m]:
            return m
    return k + MOMO_MAX if k + MOMO_MAX < len(df) else None


def confirmed_entries(data, events, entry_mode):
    """{entry_date: [(sym, turnover)]} — events re-keyed to confirmation day."""
    out, n = {}, 0
    for d, lst in events.items():
        for s, to in lst:
            df = data[s]
            j = df.index.get_loc(d)
            k = trigger_day(df, j, entry_mode)
            if k is not None:
                out.setdefault(df.index[k], []).append((s, to))
                n += 1
    for d in out:
        out[d].sort(key=lambda x: -x[1])
    return out, n


def per_event(data, events, entry_mode, ex_mode, lev=2.0):
    rets, liq_hits = [], 0
    for d, lst in events.items():
        for s, _ in lst:
            df = data[s]
            j = df.index.get_loc(d)
            k = trigger_day(df, j, entry_mode)
            if k is None:
                continue
            m = exit_day(df, k, ex_mode)
            if m is None:
                continue
            c = df["close"].values
            entry = c[k]
            rets.append(entry / c[m] - 1 - COST)
            if df["high"].values[k + 1:m + 1].size and \
               df["high"].values[k + 1:m + 1].max() >= entry * (1 + 1 / lev - MMR):
                liq_hits += 1
    a = np.array(rets)
    if not len(a):
        return "  (no events)"
    return (f"N={len(a):>4}  mean {a.mean()*100:+6.2f}%  med {np.median(a)*100:+6.2f}%  "
            f"win {(a>0).mean()*100:4.1f}%  p5 {np.percentile(a,5)*100:+7.2f}%  "
            f"worst {a.min()*100:+7.2f}%  2x-liq-touch {liq_hits/len(a)*100:4.1f}%")


def simulate(data, entries, lev, ex_mode):
    dates = sorted(set(d for df in data.values() for d in df.index))
    cash, positions = START, {}
    eq, n_liq, n_tr, n_w = [], 0, 0, 0
    for d in dates:
        for s in list(positions):
            pos = positions[s]
            if d not in data[s].index:
                continue
            df = data[s]
            i = df.index.get_loc(d)
            bar = df.loc[d]
            if bar["high"] >= pos["entry"] * (1 + 1 / lev - MMR):
                positions.pop(s); n_liq += 1; n_tr += 1
                continue
            pos["held"] += 1
            done = (pos["held"] >= int(ex_mode[0]) if ex_mode in ("1d", "3d")
                    else (bar["close"] > bar["open"] or pos["held"] >= MOMO_MAX))
            if done:
                fill = bar["close"] * 1.0002
                pnl = (pos["entry"] - fill) * pos["qty"] - fill * pos["qty"] * FEE
                cash += pos["margin"] + pnl
                n_tr += 1; n_w += pnl > 0
                positions.pop(s)
        upnl = sum(p["margin"] + (p["entry"] - (data[s].loc[d, "close"]
                   if d in data[s].index else p["entry"])) * p["qty"]
                   for s, p in positions.items())
        equity = cash + upnl
        for s, _ in entries.get(d, []):
            if len(positions) >= MAX_POS or s in positions or equity <= 0:
                continue
            margin = equity * MARGIN_FRAC
            if margin > cash:
                continue
            entry = data[s].loc[d, "close"] * (1 - 0.0002)
            notional = margin * lev
            cash -= margin + notional * FEE
            positions[s] = {"entry": entry, "qty": notional / entry,
                            "margin": margin, "held": 0}
        eq.append(equity)
    eq = np.array(eq)
    dd = (eq / np.maximum.accumulate(eq) - 1).min()
    return (f"final ${eq[-1]:>8,.0f}  total {(eq[-1]/START-1)*100:+6.0f}%  "
            f"maxDD {dd*100:+6.1f}%  LIQS {n_liq:>3}  trades {n_tr:>4}  "
            f"win {n_w/max(n_tr-n_liq,1)*100:3.0f}%")


def main():
    data = fetch_all_daily()
    events = build_events(data, liquid_only=True)
    n_ev = sum(len(v) for v in events.values())
    print(f"LIQUID universe, {n_ev} pump events (>= +30% day, 30d med turnover >= $10M)\n")
    print("A) PER-EVENT (close-to-close from confirmed entry, costs in, funding NOT):")
    for em in ("blind", "red", "breakdown"):
        print(f"  entry={em}:")
        for xm in ("1d", "3d", "momo"):
            print(f"    exit {xm:>4}: {per_event(data, events, em, xm)}")
    print("\nB) PORTFOLIO ($5k, 10%/pos, max 10, isolated liq, funding NOT in sim):")
    for em in ("red", "breakdown"):
        entries, n = confirmed_entries(data, events, em)
        print(f"  entry={em} ({n} confirmed of {n_ev}):")
        for xm in ("1d", "momo"):
            for lev in (1, 2):
                print(f"    {lev}x exit {xm:>4}: {simulate(data, entries, lev, xm)}")


if __name__ == "__main__":
    main()
