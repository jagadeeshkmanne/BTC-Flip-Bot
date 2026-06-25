#!/usr/bin/env python3
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from short_deepen_engine import run_ec3
from long_swap_engine import run_swap
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
D = dict(short_inst="eth", long_lev=llev, size_lo=0.5, size_mid=1.0, size_hi=1.0,
         drop_look=35, s_atr=6.0, s_cap=0.20, strail_k=3.5, strail_arm_R=1.0)

# ---- Reference: config D from the deployed engine ----
ref = run_ec3(**D)
cgD,ddD,rrD = m(ref)
oosD = m(ref[ref.index>=ref.index[int(len(ref)*0.6)]])[2]

# ---- Validation: w_eth=0.0 from swap engine must match config D ----
s0 = run_swap(w_eth=0.0, **D)
# align indices (both .iloc[16:] from same data)
common_idx = ref.index.intersection(s0.index)
maxdiff = (ref.reindex(common_idx) - s0.reindex(common_idx)).abs().max()
reldiff = ((ref.reindex(common_idx) - s0.reindex(common_idx)).abs()/ref.reindex(common_idx).abs()).max()
print("="*92)
print("VALIDATION  w_eth=0.0 (pure BTC long) vs deployed config D")
print(f"  config D:  CAGR {cgD*100:.1f}%  DD {ddD*100:.1f}%  ret/DD {rrD:.2f}  OOS {oosD:.2f}")
cg0,dd0,rr0=m(s0); oos0=m(s0[s0.index>=s0.index[int(len(s0)*0.6)]])[2]
print(f"  swap w=0:  CAGR {cg0*100:.1f}%  DD {dd0*100:.1f}%  ret/DD {rr0:.2f}  OOS {oos0:.2f}")
print(f"  MAX equity abs diff = {maxdiff:.3e}   MAX rel diff = {reldiff:.3e}")
print("="*92)

YEARS=[2018,2021,2022,2024,2025]
def osr(s): return m(s[s.index>=s.index[int(len(s)*0.6)]])[2]
def minyr(s): return min(yr(s,y) for y in range(2018,2027))

print(f"\nLONG-WEIGHT SWEEP (short side unchanged = config D ETH trailing)")
print(f"{'w_eth':>6}{'CAGR':>7}{'DD':>7}{'r/DD':>6}{'OOS':>6}{'minYr':>7}{'grn':>5}   " + "".join(f"{y:>7}" for y in YEARS))
rows={}
for w in [0.0,0.25,0.5,0.75,1.0]:
    s=run_swap(w_eth=w, **D); rows[w]=s
    cg,dd,rr=m(s)
    g='Y' if grn(s) else 'RED'
    print(f"{w:>6.2f}{cg*100:>6.0f}%{dd*100:>6.0f}%{rr:>6.2f}{osr(s):>6.2f}{minyr(s):>+6.0f}%{g:>5}   " +
          "".join(f"{yr(s,y):>+6.0f}%" for y in YEARS))

print("\nFULL per-year (2017-2026):")
print(f"{'w_eth':>6}  " + "".join(f"{y:>7}" for y in range(2017,2027)))
for w,s in rows.items():
    print(f"{w:>6.2f}  " + "".join(f"{yr(s,y):>+6.0f}%" for y in range(2017,2027)))

# ---- WIN test vs config D under ALL strict rules ----
print("\nSTRICT-RULE evaluation (vs config D ret/DD 5.61, OOS>=4.3, DD>=-35%, all yrs>=-1%):")
for w,s in rows.items():
    if w==0.0: continue
    cg,dd,rr=m(s); oos=osr(s); my=minyr(s)
    rules=[("r/DD>5.61",rr>rrD),("allyr>=-1%",my>=-1.0),("OOS>=4.3",oos>=4.3),("DD>=-35%",dd>=-0.35)]
    ok=all(p for _,p in rules)
    print(f"  w={w:.2f}: " + "  ".join(f"{n}:{'OK' if p else 'X'}" for n,p in rules) + f"  => {'WIN' if ok else 'reject'}")
