#!/usr/bin/env python3
"""STEP 3 — verify the winning trailing-short candidate: full year-by-year +
perturbation (nudge each knob +/-1 step) + report worst neighbor."""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_btclong_ethshort import load4h
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
    cg,dd,rr=m(s); o=m(s[s.index>=s.index[int(len(s)*0.6)]])[2]; return cg,dd,rr,o
def full(name,**kw):
    s=run_ec3(**BASE,**kw); cg,dd,rr,o=stats(s)
    g='GRN' if grn(s) else 'RED'
    yrs=" ".join(f"{y}:{yr(s,y):+.0f}" for y in range(2018,2027))
    print(f"{name}\n  CAGR{cg*100:.0f}% DD{dd*100:.0f}% r/DD{rr:.2f} OOS{o:.2f} {g}\n  {yrs}")
    return cg,dd,rr,o,g

if __name__=="__main__":
    print("CANDIDATE: strail_k=3.5 arm_R=1.0")
    full("candidate", strail_k=3.5, strail_arm_R=1.0)
    print("="*70)
    print("PERTURBATION (+/-1 step each knob):")
    rows=[]
    grid=[("strail_k", 2.5,3.5,4.5),("strail_arm_R",0.5,1.0,1.5)]
    # neighbors: vary one knob at a time around (k=3.5, arm=1.0)
    neighbors=[
        ("k=2.5,arm=1.0", dict(strail_k=2.5,strail_arm_R=1.0)),
        ("k=4.5,arm=1.0", dict(strail_k=4.5,strail_arm_R=1.0)),
        ("k=3.5,arm=0.5", dict(strail_k=3.5,strail_arm_R=0.5)),
        ("k=3.5,arm=1.5", dict(strail_k=3.5,strail_arm_R=1.5)),
        # also nudge a base short knob to check robustness
        ("k=3.5,arm=1.0,s_atr=5.5", dict(strail_k=3.5,strail_arm_R=1.0,s_atr=5.5)),
        ("k=3.5,arm=1.0,s_atr=6.5", dict(strail_k=3.5,strail_arm_R=1.0,s_atr=6.5)),
    ]
    worst=None
    for name,kw in neighbors:
        cfg=dict(BASE); cfg.update(kw)
        s=run_ec3(**cfg); cg,dd,rr,o=stats(s); g='GRN' if grn(s) else 'RED'
        print(f"  {name:<28} CAGR{cg*100:>4.0f}% DD{dd*100:>4.0f}% r/DD{rr:>5.2f} OOS{o:>5.2f} 22:{yr(s,2022):>+3.0f} {g}")
        if worst is None or rr<worst[1]: worst=(name,rr,o,g)
    print(f"  WORST NEIGHBOR: {worst[0]}  r/DD={worst[1]:.2f} OOS={worst[2]:.2f} {worst[3]}")
