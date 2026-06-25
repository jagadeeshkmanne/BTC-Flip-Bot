#!/usr/bin/env python3
"""STEP 1 — reproduce CONFIG C exactly using run_ec2 + m()/yr()/grn()."""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_btclong_ethshort import load4h
from short_side_engine import run_ec2
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

def report(name,s):
    cg,dd,rr=m(s)
    o=m(s[s.index>=s.index[int(len(s)*0.6)]])[2]
    yrs={y:yr(s,y) for y in range(2018,2027)}
    print(f"{name}: CAGR {cg*100:.0f}% DD {dd*100:.0f}% r/DD {rr:.2f} OOS {o:.2f} {'GRN' if grn(s) else 'RED'}")
    print("   yrs: "+" ".join(f"{y}:{v:+.0f}" for y,v in yrs.items()))
    return cg,dd,rr,o,yrs

if __name__=="__main__":
    llev=build_llev()
    sC=run_ec2("eth", long_lev=llev, size_lo=0.5, size_mid=1.0, size_hi=1.0,
               drop_look=35, s_atr=6.0, s_cap=0.20)
    report("CONFIG C", sC)
