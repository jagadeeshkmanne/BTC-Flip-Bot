#!/usr/bin/env python3
"""short_side_sweep2.py — sizing tiers, short leverage, short-earlier, and best combos."""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_shorts import m
from backtest_v2_eth_conviction import yr
from backtest_btclong_ethshort import load4h
from short_side_engine import run_ec2

def conv_lev():
    btc,_=load4h("BTCUSDT"); eth,_=load4h("ETHUSDT")
    common=pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    b=btc[btc["timestamp"].isin(common)].reset_index(drop=True); c=b["close"]
    adx=bt.adx(b,14).shift(1).fillna(0).values
    egap=((bt.ema(c,50)-bt.ema(c,200))/bt.ema(c,200)).shift(1).fillna(0).values
    conv=np.clip(adx/35.0,0,1)*0.5+np.clip(egap/0.12,0,1)*0.5
    return 1.0+1.5*conv
LLEV=conv_lev()

def oos(s): return m(s[s.index>=s.index[int(len(s)*0.6)]])[2]
def allyears(s): return [yr(s,y) for y in range(2018,2027)]

def report(name,s,base=None):
    cg,dd,rr=m(s); o=oos(s); y18=yr(s,2018); y22=yr(s,2022)
    ys=allyears(s); mn=min(ys); flag="grn" if mn>=-1.0 else "RED"; tag=""
    if base is not None:
        bcg,bdd,brr,bo,bmn=base
        if rr>brr and mn>=-1.0 and dd>=-0.40-1e-9 and o>=bo-0.05: tag=" <-- WIN"
        elif rr>brr and mn>=-1.0 and dd>=-0.40-1e-9: tag=" <-- cand(OOSdn)"
    print(f"  {name:<34}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o:>6.2f}  18:{y18:>+4.0f} 22:{y22:>+4.0f}  min:{mn:>+4.0f} {flag}{tag}")
    return cg,dd,rr,o,mn

def R(name,base=None,**kw):
    return report(name,run_ec2("eth",long_lev=LLEV,**kw),base)

if __name__=="__main__":
    print("="*100); print("SANITY: run_ec2 defaults must == base (150/-36/4.18/4.01/+21)"); print("="*100)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  years")
    base=R("run_ec2 default")

    print("\n"+"="*100); print("F) short SIZING tiers (base .25/.5/1.0)"); print("="*100)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  years")
    R("more aggr .5/.75/1.0",base,size_lo=0.50,size_mid=0.75,size_hi=1.0)
    R("aggr .5/1.0/1.0",base,size_lo=0.50,size_mid=1.0,size_hi=1.0)
    R("flat 1.0/1.0/1.0",base,size_lo=1.0,size_mid=1.0,size_hi=1.0)
    R("less aggr .15/.35/.75",base,size_lo=0.15,size_mid=0.35,size_hi=0.75)
    R(".35/.6/1.0",base,size_lo=0.35,size_mid=0.60,size_hi=1.0)

    print("\n"+"="*100); print("G) SHORT LEVERAGE (skeptical — watch 2022 DD)"); print("="*100)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  years")
    for lv in [1.2,1.3,1.5]:
        R(f"s_lev={lv}",base,s_lev=lv)
    # leverage with tighter stops to control DD
    R("s_lev1.3 s_cap0.12",base,s_lev=1.3,s_cap=0.12)
    R("s_lev1.5 s_cap0.12 s_atr4",base,s_lev=1.5,s_cap=0.12,s_atr=4.0)

    print("\n"+"="*100); print("H) SHORT EARLIER (smaller drop gate) vs LATER"); print("="*100)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  years")
    R("earlier drop0.05 macd",base,drop_pct=0.05)
    R("earlier drop0.03 macd",base,drop_pct=0.03)
    R("earliest drop0.0 macd",base,drop_pct=0.0)   # short on MACD cross alone
    R("later drop0.13 macd",base,drop_pct=0.13)

    print("\n"+"="*100); print("I) BEST COMBOS from single-axis winners"); print("="*100)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  years")
    R("dl35",base,drop_look=35)
    R("dl35 s_cap0.20",base,drop_look=35,s_cap=0.20)
    R("dl35 s_atr6 s_cap0.20",base,drop_look=35,s_atr=6.0,s_cap=0.20)
    R("dl30 s_cap0.20",base,drop_look=30,s_cap=0.20)
    R("s_atr7 s_cap0.25",base,s_atr=7.0,s_cap=0.25)
    R("dl35 s_atr7 s_cap0.25",base,drop_look=35,s_atr=7.0,s_cap=0.25)
    R("dl35 s_cap0.20 size.35/.6/1",base,drop_look=35,s_cap=0.20,size_lo=0.35,size_mid=0.60,size_hi=1.0)
    R("dl30 s_atr6 s_cap0.20",base,drop_look=30,s_atr=6.0,s_cap=0.20)
