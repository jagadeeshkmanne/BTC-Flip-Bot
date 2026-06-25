#!/usr/bin/env python3
"""backtest_myv3_full.py — FULL metrics report for the my-V3 base config (BTC 4h).
MTF entry + ATR-3 structural stop + pyramid@2R + break-even. Comprehensive stats so we have the
complete picture: CAGR/DD/Calmar/Sharpe/Sortino/exposure/year-by-year/monthly/OOS.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backtest_myv3 import regime, run, m

BARS_YR = 6*365.25  # 4h bars per year


def full(s, label):
    r = s.pct_change().fillna(0.0)
    yrs = (s.index[-1]-s.index[0]).days/365.25
    cagr = (s.iloc[-1]/s.iloc[0])**(1/yrs)-1
    dd = (s/s.cummax()-1)
    maxdd = dd.min()
    # longest drawdown (bars underwater)
    uw = (dd < 0).astype(int); run_len=0; longest=0
    for x in uw:
        run_len = run_len+1 if x else 0; longest=max(longest,run_len)
    sharpe = r.mean()/r.std()*np.sqrt(BARS_YR) if r.std()>0 else 0
    downside = r[r<0].std()
    sortino = r.mean()/downside*np.sqrt(BARS_YR) if downside>0 else 0
    expo = (r != 0).mean()*100
    print(f"\n  === {label} ===")
    print(f"    total return    {(s.iloc[-1]/s.iloc[0]-1)*100:+.0f}%   (final {s.iloc[-1]/s.iloc[0]:.1f}x from start)")
    print(f"    CAGR            {cagr*100:.1f}%")
    print(f"    max drawdown    {maxdd*100:.1f}%")
    print(f"    Calmar (ret/DD) {cagr/abs(maxdd):.2f}")
    print(f"    Sharpe (ann)    {sharpe:.2f}")
    print(f"    Sortino (ann)   {sortino:.2f}")
    print(f"    longest underwater {longest} bars (~{longest/6:.0f} days)")
    print(f"    time in market  {expo:.0f}%")
    return cagr, maxdd


def main():
    df, bull = regime()
    print("="*70)
    print("MY-V3 FULL METRICS — BTC 4h | MTF entry + ATR-3 stop + pyramid@2R + BE")
    print("="*70)
    s = run(df, bull, atr_mult=3.0, sl_cap=0.08, be_r=1.0, pyr_r=2.0, pyr_frac=1.0)
    cg, dd = full(s, "BASE (recommended)")
    # OOS
    cut = s.index[int(len(s)*0.6)]
    oc, od, orr = m(s[s.index>=cut])
    print(f"    OOS (from {cut.date()}): CAGR {oc*100:.0f}%  DD {od*100:.0f}%  ret/DD {orr:.2f}")

    print("\n  YEAR-BY-YEAR:")
    yr = s.index.year
    print(f"    {'year':<6}{'return':>10}{'maxDD':>9}")
    for y in sorted(set(yr)):
        seg = s[yr==y]
        if len(seg) < 30: continue
        ret = (seg.iloc[-1]/seg.iloc[0]-1)*100
        d = (seg/seg.cummax()-1).min()*100
        tag = "  (flat — regime bear, in cash)" if abs(ret)<1 else ""
        print(f"    {y:<6}{ret:>+9.0f}%{d:>8.0f}%{tag}")

    # Dials the agents found
    print("\n  ALTERNATE DIALS (from agent sweep):")
    lo = run(df, bull, atr_mult=3.0, sl_cap=0.08, be_r=1.0, pyr_r=None)  # pyramid off ~ lower risk proxy
    clo, dlo, rlo = m(lo)
    print(f"    LOWER-RISK (pyramid OFF):   CAGR {clo*100:.0f}%  DD {dlo*100:.0f}%  ret/DD {rlo:.2f}")
    hi = run(df, bull, atr_mult=4.0, sl_cap=0.08, be_r=1.5, pyr_r=2.0, pyr_frac=1.0)
    chi, dhi, rhi = m(hi)
    print(f"    HIGHER-RETURN (atr4,be1.5): CAGR {chi*100:.0f}%  DD {dhi*100:.0f}%  ret/DD {rhi:.2f}")


if __name__ == "__main__":
    main()
