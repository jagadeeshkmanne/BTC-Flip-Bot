#!/usr/bin/env python3
"""backtest_myv3_shortgate.py — short ONLY after a long-horizon rally rolls over (user's idea).

Keeps my-V3 LONG side unchanged. Replaces the (whipsawy) daily-EMA short gate with SLOW,
multi-month signals so shorts only fire when a major rally tops out and breaks down:
  A) price < SMA(N months)         -> confirmed major downtrend (N=1,3,6,12 mo)
  B) drawdown > D% from N-mo high  -> short the decline off a peak
  C) rally-then-break: was up >R% over N mo AND now < its 50-day MA
The 4h ATR stop + pyramid + break-even machinery is identical. Question: does ANY slow short
gate make shorts ADD risk-adjusted return (beat long-only ret/DD 2.38) — esp. survive 2022?
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_shorts import regimes, run, m

BPD = 6  # 4h bars per day


def yr_ret(s, y):
    seg = s[s.index.year == y]
    return (seg.iloc[-1]/seg.iloc[0]-1)*100 if len(seg) > 20 else 0.0


def main():
    df, bull, bear_daily = regimes()
    c = df["close"]
    n = len(df)
    print("="*94)
    print("SHORTS GATED ON LONG-HORIZON RALLY/REVERSAL (BTC 4h) — keep my-V3 long side, vary short gate")
    print("="*94)
    base = run(df, bull, bear_daily, allow_long=True, allow_short=False)
    cb, db, rb = m(base)
    print(f"  {'short gate':<40}{'CAGR':>6}{'DD':>6}{'r/DD':>6}{'OOSrDD':>8}{'2022':>7}{'2026':>7}")
    print(f"  {'LONG-ONLY (baseline, no shorts)':<40}{cb*100:>5.0f}%{db*100:>5.0f}%{rb:>6.2f}{m(base[base.index>=base.index[int(n*0.6)]])[2]:>8.2f}{yr_ret(base,2022):>6.0f}%{yr_ret(base,2026):>6.0f}%")
    print("  " + "-"*92)

    gates = []
    # A) price below SMA(N months), causal
    for mo in (1, 3, 6, 12):
        sma = bt.sma(c, mo*30*BPD).shift(1)
        gates.append((f"A) price < SMA({mo}mo)", (c < sma).fillna(False).values))
    # B) drawdown > D% from trailing N-month high
    for mo, d in ((3, 0.10), (6, 0.10), (6, 0.20), (12, 0.20)):
        hh = c.rolling(mo*30*BPD).max().shift(1)
        dd = (c/hh - 1)
        gates.append((f"B) >{int(d*100)}% off {mo}mo-high", (dd < -d).fillna(False).values))
    # C) rally-then-break: up >R% over N mo AND now below 50-day MA
    ma50 = bt.sma(c, 50*BPD).shift(1)
    for mo, r in ((6, 0.50), (12, 1.0)):
        ralln = (c/c.shift(mo*30*BPD) - 1)
        cond = ((ralln > r) & (c < ma50)).fillna(False).values
        gates.append((f"C) +{int(r*100)}%/{mo}mo & <50dMA", cond))

    for name, bear in gates:
        s = run(df, bull, bear, allow_long=True, allow_short=True)
        cg, dd2, rr = m(s); orr = m(s[s.index >= s.index[int(n*0.6)]])[2]
        flag = "  <== beats base" if rr > rb else ""
        print(f"  {name:<40}{cg*100:>5.0f}%{dd2*100:>5.0f}%{rr:>6.2f}{orr:>8.2f}{yr_ret(s,2022):>6.0f}%{yr_ret(s,2026):>6.0f}%{flag}")


if __name__ == "__main__":
    main()
