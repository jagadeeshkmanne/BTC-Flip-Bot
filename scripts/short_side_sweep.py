#!/usr/bin/env python3
"""short_side_sweep.py — STEP1 reproduce base, STEP2 sweep short-side params.
Honesty: closed-bar signals already in run_ec (shift(1)), next-bar fills, FEE/SLIP on."""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_myv3_shorts import m
from backtest_v2_eth_conviction import run_ec, yr
from backtest_btclong_ethshort import load4h

# conviction leverage vector exactly as DEPLOYED (btcv2_eq)
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
def minyear(s): return min(allyears(s))
def green(s): return all(y>=-1.0 for y in range(2018,2027) for y in [yr(s,y)])

def report(name,s,base=None):
    cg,dd,rr=m(s); o=oos(s)
    y18=yr(s,2018); y22=yr(s,2022)
    ys=allyears(s); mn=min(ys)
    flag="grn" if mn>=-1.0 else "RED"
    tag=""
    if base is not None:
        bcg,bdd,brr=base
        if rr>brr and mn>=-1.0 and dd>=-0.40-1e-9:
            tag=" <-- candidate"
    print(f"  {name:<34}{cg*100:>5.0f}%{dd*100:>5.0f}%{rr:>6.2f}{o:>6.2f}  18:{y18:>+4.0f} 22:{y22:>+4.0f}  min:{mn:>+4.0f} {flag}{tag}")
    return cg,dd,rr,o,mn

if __name__=="__main__":
    print("="*100)
    print("STEP1 — REPRODUCE BASE (btcv2_eq = run_ec eth, long_lev=1+1.5*conv)")
    print("="*100)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  years")
    base_s=run_ec("eth",long_lev=LLEV)
    base=m(base_s)
    report("BASE (deployed)",base_s)
    print(f"\n  full year-by-year base: "+" ".join(f"{y}:{yr(base_s,y):+.0f}" for y in range(2018,2027)))

    def R(name,**kw):
        s=run_ec("eth",long_lev=LLEV,**kw)
        return report(name,s,base)

    print("\n"+"="*100); print("A) drop_pct sweep (base 0.10)"); print("="*100)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  years")
    for dp in [0.06,0.07,0.08,0.09,0.10,0.12,0.15]:
        R(f"drop_pct={dp}",drop_pct=dp)

    print("\n"+"="*100); print("B) drop_look sweep (base 40)"); print("="*100)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  years")
    for dl in [20,25,30,35,40,50,60]:
        R(f"drop_look={dl}",drop_look=dl)

    print("\n"+"="*100); print("C) confirm options (base macd)"); print("="*100)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  years")
    for cf in ["macd","ema","both","either","none"]:
        R(f"confirm={cf}",confirm=cf)

    print("\n"+"="*100); print("D) macd_tf 4h vs 1d (base 1d) + mf/ms/msig"); print("="*100)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  years")
    R("macd_tf=4h",macd_tf="4h")
    R("macd 8/21/5",mf=8,ms=21,msig=5)
    R("macd 8/21/5 4h",mf=8,ms=21,msig=5,macd_tf="4h")
    R("macd 12/26/9 (base)")

    print("\n"+"="*100); print("E) short stop: s_atr (base 5) x s_cap (base 0.15)"); print("="*100)
    print(f"  {'config':<34}{'CAGR':>6}{'DD':>5}{'r/DD':>6}{'OOS':>6}  years")
    for sa in [3,4,5,6,7]:
        R(f"s_atr={sa}",s_atr=float(sa))
    for sc in [0.10,0.12,0.15,0.20,0.25]:
        R(f"s_cap={sc}",s_cap=sc)
    # promising combos
    R("s_atr=6 s_cap=0.20",s_atr=6.0,s_cap=0.20)
    R("s_atr=7 s_cap=0.25",s_atr=7.0,s_cap=0.25)
    R("s_atr=4 s_cap=0.12",s_atr=4.0,s_cap=0.12)
