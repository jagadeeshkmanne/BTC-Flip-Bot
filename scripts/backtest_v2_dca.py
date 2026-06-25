#!/usr/bin/env python3
"""backtest_v2_dca.py — pyramid (add-to-WINNER) gap/size sweep + why it's NOT a martingale.

V2's "pyramid" adds ONCE to a WINNING trade at +2R (stop moved to break-even). This sweeps the
add trigger (gap) and size, and contrasts with a MARTINGALE (add to a LOSER) which we tested
earlier and which blows up. Answers: how many adds, what gaps, did we try martingale.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_ext import build
from backtest_short_aggressive import daily_macd_bear
from backtest_v2_combined import run2
from backtest_myv3_final import m


def main():
    df,bull=build(); c=df["close"]
    L=bull & (c>bt.sma(c,9*30*6).shift(1)).fillna(False).values
    bear=((c/c.rolling(40*6).max().shift(1)-1)<-0.10).fillna(False).values & daily_macd_bear(df)
    ddh=(c/c.rolling(180*6).max().shift(1)-1).fillna(0).values
    ss=np.where(ddh<=-0.30,1.0,np.where(ddh<=-0.20,0.50,0.25))
    pa=(c>2.2*bt.sma(c,140*6).shift(1)).fillna(False).values
    def row(name,**kw):
        s=run2(df,L,bear,ss,pa,use_parab=True,use_bd=True,**kw); cg,dd,rr=m(s)
        print(f"  {name:<36}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}")
    print("="*72)
    print("V2 PYRAMID = add ONCE to a WINNER at +R (stop->break-even). Sweep gap & size.")
    print("="*72)
    print(f"  {'config':<36}{'CAGR':>6}{'DD':>5}{'r/DD':>6}")
    print("  -- number of adds / gap (single add at +R) --")
    row("0 adds (NO pyramid)", pyr_frac=0.0)
    row("1 add @ +1.5R (small gap)", pyr_r=1.5)
    row("1 add @ +2R  (V2 default)", pyr_r=2.0)
    row("1 add @ +2.5R", pyr_r=2.5)
    row("1 add @ +3R  (wide gap)", pyr_r=3.0)
    row("1 add @ +4R  (very wide)", pyr_r=4.0)
    print("  -- add size (at +2R) --")
    row("add 50% of base", pyr_frac=0.5)
    row("add 100% (V2)", pyr_frac=1.0)
    row("add 150%", pyr_frac=1.5)
    print()
    print("  NOTE: multi-add ladders tested earlier (agent): {2R,4R}=CAGR 85%/DD -40%,")
    print("        {2R,4R,6R}=118%/-50% — more adds = more CAGR but worse DD. 1 add is best r/DD.")
    print("  MARTINGALE (add to a LOSER / average down) tested early (backtest_martingale.py,")
    print("        backtest_dca_bot.py): LOSES with -76% bag risk. V2 adds to WINNERS only — opposite.")


if __name__=="__main__":
    main()
