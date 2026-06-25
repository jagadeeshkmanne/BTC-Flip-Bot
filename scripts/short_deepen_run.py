#!/usr/bin/env python3
"""STEP 2 — short-side deepening variants vs CONFIG C. Honest sweep."""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_btclong_ethshort import load4h
from short_side_engine import run_ec2
from short_deepen_engine import run_ec3
from backtest_v2_eth_conviction import yr, grn
from backtest_myv3_shorts import m

def build_llev():
    btc,_=load4h("BTCUSDT"); eth,_=load4h("ETHUSDT")
    common=pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    btc=btc[btc["timestamp"].isin(common)].reset_index(drop=True); c=btc["close"]
    adx=bt.adx(btc,14).shift(1).fillna(0).values
    egap=((bt.ema(c,50)-bt.ema(c,200))/bt.ema(c,200)).shift(1).fillna(0).values
    conv=np.clip(adx/35.0,0,1)*0.5+np.clip(egap/0.12,0,1)*0.5
    return 1.0+1.5*conv

LLEV=build_llev()
BASE=dict(short_inst="eth", long_lev=LLEV, size_lo=0.5, size_mid=1.0, size_hi=1.0,
          drop_look=35, s_atr=6.0, s_cap=0.20)

def stats(s):
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])[2]
    return cg,dd,rr,o
def line(name,s):
    cg,dd,rr,o=stats(s)
    g='GRN' if grn(s) else 'RED'
    print(f"{name:<34} CAGR{cg*100:>4.0f}% DD{dd*100:>4.0f}% r/DD{rr:>5.2f} OOS{o:>5.2f} "
          f"18:{yr(s,2018):>+4.0f} 22:{yr(s,2022):>+3.0f} {g}")
    return cg,dd,rr,o,g

def wins(s, ref_rr=4.90):
    cg,dd,rr,o=stats(s)
    return (rr>ref_rr) and grn(s) and (o>=4.2) and (dd>=-0.38)

if __name__=="__main__":
    print("="*92)
    # reproduce via run_ec2 and via run_ec3-with-knobs-off (must be identical)
    s2=run_ec2(**BASE); line("C via run_ec2", s2)
    s3=run_ec3(**BASE); line("C via run_ec3 (knobs off)", s3)
    print("  identical:", bool(np.allclose(s2.values,s3.values)))
    print("-"*92)
    print("SHORT PYRAMID (spyr_k, +0.5x, stop->entry):")
    for k in (1.0,1.5,2.0):
        line(f"  spyr_k={k} frac0.5", run_ec3(**BASE, spyr_k=k, spyr_frac=0.5))
    for k in (1.0,1.5,2.0):
        line(f"  spyr_k={k} frac1.0", run_ec3(**BASE, spyr_k=k, spyr_frac=1.0))
    print("-"*92)
    print("TRAILING SHORT EXIT (lowS + k*ATR, armed after R):")
    for k in (1.5,2.5,3.5):
        for arm in (0.0,1.0,2.0):
            line(f"  strail_k={k} arm={arm}", run_ec3(**BASE, strail_k=k, strail_arm_R=arm))
    print("-"*92)
    print("SHORT RE-ENTRY (re-short while still bear):")
    line("  s_reentry", run_ec3(**BASE, s_reentry=True))
    print("-"*92)
    print("DEEPEST-TIER BUMP (size_hi):")
    for sh in (1.25,1.5):
        b=dict(BASE); b["size_hi"]=sh
        line(f"  size_hi={sh}", run_ec3(**b))
