#!/usr/bin/env python3
"""short_side_final.py — finalists: full year-by-year + perturbation robustness (overfit check)."""
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

FIN={
 "BASE (.25/.5/1.0)":dict(),
 "A sizing .5/1.0/1.0":dict(size_lo=0.50,size_mid=1.0,size_hi=1.0),
 "B dl35 s_cap0.20 .35/.6/1":dict(drop_look=35,s_cap=0.20,size_lo=0.35,size_mid=0.60,size_hi=1.0),
 "C dl35 s_atr6 s_cap0.20 .5/1/1":dict(drop_look=35,s_atr=6.0,s_cap=0.20,size_lo=0.50,size_mid=1.0,size_hi=1.0),
}

print("="*108)
print("FINALISTS — full year-by-year (yr% 2018..2026)")
print("="*108)
print(f"  {'config':<32}{'CAGR':>5}{'DD':>5}{'rDD':>5}{'OOS':>5}  "+" ".join(f"{y}" for y in range(2018,2027)))
ser={}
for name,kw in FIN.items():
    s=run_ec2("eth",long_lev=LLEV,**kw); ser[name]=s
    cg,dd,rr=m(s); o=oos(s)
    ys=" ".join(f"{yr(s,y):>+4.0f}" for y in range(2018,2027))
    print(f"  {name:<32}{cg*100:>4.0f}%{dd*100:>5.0f}%{rr:>5.2f}{o:>5.2f}  {ys}")

print("\n"+"="*108)
print("OVERFIT CHECK — perturb finalist B & A params +/- and confirm no cliff")
print("="*108)
print(f"  {'config':<40}{'CAGR':>5}{'DD':>5}{'rDD':>5}{'OOS':>5} min")
def line(name,**kw):
    s=run_ec2("eth",long_lev=LLEV,**kw); cg,dd,rr=m(s); o=oos(s)
    mn=min(yr(s,y) for y in range(2018,2027))
    print(f"  {name:<40}{cg*100:>4.0f}%{dd*100:>5.0f}%{rr:>5.2f}{o:>5.2f} {mn:>+4.0f}")
# perturb A (sizing only — most robust candidate)
line("A .5/1.0/1.0",size_lo=0.50,size_mid=1.0,size_hi=1.0)
line("A- .45/0.9/1.0",size_lo=0.45,size_mid=0.90,size_hi=1.0)
line("A+ .55/1.0/1.0",size_lo=0.55,size_mid=1.0,size_hi=1.0)
line("A .4/0.8/1.0",size_lo=0.40,size_mid=0.80,size_hi=1.0)
line("A .6/1.0/1.0",size_lo=0.60,size_mid=1.0,size_hi=1.0)
# perturb B around dl & s_cap
line("B dl35 cap.20 .35/.6/1",drop_look=35,s_cap=0.20,size_lo=0.35,size_mid=0.60,size_hi=1.0)
line("B dl32 cap.20",drop_look=32,s_cap=0.20,size_lo=0.35,size_mid=0.60,size_hi=1.0)
line("B dl38 cap.20",drop_look=38,s_cap=0.20,size_lo=0.35,size_mid=0.60,size_hi=1.0)
line("B dl35 cap.18",drop_look=35,s_cap=0.18,size_lo=0.35,size_mid=0.60,size_hi=1.0)
line("B dl35 cap.22",drop_look=35,s_cap=0.22,size_lo=0.35,size_mid=0.60,size_hi=1.0)

print("\n"+"="*108)
print("OVERFIT CHECK C (most-stacked) — perturb every knob")
print("="*108)
print(f"  {'config':<40}{'CAGR':>5}{'DD':>5}{'rDD':>5}{'OOS':>5} min")
Cbase=dict(drop_look=35,s_atr=6.0,s_cap=0.20,size_lo=0.50,size_mid=1.0,size_hi=1.0)
line("C base",**Cbase)
line("C dl30",**{**Cbase,"drop_look":30})
line("C dl40",**{**Cbase,"drop_look":40})
line("C s_atr5",**{**Cbase,"s_atr":5.0})
line("C s_atr7",**{**Cbase,"s_atr":7.0})
line("C s_cap0.15",**{**Cbase,"s_cap":0.15})
line("C s_cap0.25",**{**Cbase,"s_cap":0.25})
line("C size .4/.8/1",**{**Cbase,"size_lo":0.40,"size_mid":0.80})
line("C size .6/1/1",**{**Cbase,"size_lo":0.60})
