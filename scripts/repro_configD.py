#!/usr/bin/env python3
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from short_deepen_engine import run_ec3
from backtest_btclong_ethshort import load4h
from backtest_v2_eth_conviction import yr, grn
from backtest_myv3_shorts import m

def build_llev():
    btc,_=load4h("BTCUSDT"); eth,_=load4h("ETHUSDT")
    common=pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    b=btc[btc["timestamp"].isin(common)].reset_index(drop=True); c=b["close"]
    adx=bt.adx(b,14).shift(1).fillna(0).values
    egap=((bt.ema(c,50)-bt.ema(c,200))/bt.ema(c,200)).shift(1).fillna(0).values
    conv=np.clip(adx/35.0,0,1)*0.5+np.clip(egap/0.12,0,1)*0.5
    return 1.0+1.5*conv

llev=build_llev()
s=run_ec3("eth", long_lev=llev, size_lo=0.5, size_mid=1.0, size_hi=1.0,
          drop_look=35, s_atr=6.0, s_cap=0.20, strail_k=3.5, strail_arm_R=1.0)
cg,dd,rr=m(s)
oos=m(s[s.index>=s.index[int(len(s)*0.6)]])
print("CONFIG D reproduction:")
print(f"  CAGR {cg*100:.1f}%  DD {dd*100:.1f}%  ret/DD {rr:.2f}  OOS {oos[2]:.2f}")
print("  per-year:", {y:round(yr(s,y),0) for y in range(2017,2027)})
print("  green:", grn(s))
