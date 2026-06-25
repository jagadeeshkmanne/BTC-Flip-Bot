#!/usr/bin/env python3
"""short_retune_sweep.py — joint re-tune of SHORT params around config D, trailing stop ACTIVE."""
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from short_deepen_engine import run_ec3
from backtest_v2_eth_conviction import yr, grn
from backtest_myv3_final import m
from backtest_btclong_ethshort import load4h


def build_llev():
    btc, _ = load4h("BTCUSDT"); eth, _ = load4h("ETHUSDT")
    common = pd.Index(btc["timestamp"]).intersection(pd.Index(eth["timestamp"]))
    b = btc[btc["timestamp"].isin(common)].reset_index(drop=True); c = b["close"]
    adx = bt.adx(b, 14).shift(1).fillna(0).values
    egap = ((bt.ema(c, 50) - bt.ema(c, 200)) / bt.ema(c, 200)).shift(1).fillna(0).values
    conv = np.clip(adx / 35.0, 0, 1) * 0.5 + np.clip(egap / 0.12, 0, 1) * 0.5
    return 1.0 + 1.5 * conv

LLEV = build_llev()

# config D defaults
D = dict(short_inst="eth", long_lev=LLEV, size_lo=0.5, size_mid=1.0, size_hi=1.0,
         drop_look=35, s_atr=6.0, s_cap=0.20, strail_k=3.5, strail_arm_R=1.0)


def stats(**kw):
    args = dict(D); args.update(kw)
    s = run_ec3(**args)
    cg, dd, rr = m(s)
    oos = m(s[s.index >= s.index[int(len(s) * 0.6)]])[2]
    ys = {y: yr(s, y) for y in range(2018, 2027)}
    green = all(v >= -1.0 for v in ys.values())
    return dict(cg=cg, dd=dd, rr=rr, oos=oos, ys=ys, green=green)


def fmt(label, r, base_rr=None):
    flag = "grn" if r["green"] else "RED"
    mark = ""
    if base_rr is not None and r["rr"] > base_rr + 0.2 and r["green"] and r["oos"] >= 4.3 and r["dd"] >= -0.35:
        mark = "  <== BEATS D"
    print(f"  {label:<26}CAGR {r['cg']*100:>5.0f}%  DD {r['dd']*100:>5.0f}%  r/DD {r['rr']:>5.2f}  "
          f"OOS {r['oos']:>5.2f}  2018 {r['ys'][2018]:>+5.0f}  2022 {r['ys'][2022]:>+4.0f}  {flag}{mark}")


def main():
    print("=" * 100)
    print("STEP 1 — REPRODUCE CONFIG D")
    print("=" * 100)
    d = stats()
    fmt("CONFIG D", d)
    print(f"\n  Full year-by-year: " + "  ".join(f"{y}:{d['ys'][y]:+.0f}%" for y in range(2018, 2027)))
    base = d["rr"]

    print("\n" + "=" * 100)
    print("STEP 2 — ONE-AT-A-TIME SWEEPS (trailing stop active)")
    print("=" * 100)

    print("\n[drop_pct]  (D=0.10)")
    for v in [0.06, 0.08, 0.10, 0.12, 0.15]:
        fmt(f"drop_pct={v}", stats(drop_pct=v), base)

    print("\n[drop_look]  (D=35)")
    for v in [25, 30, 35, 40, 50]:
        fmt(f"drop_look={v}", stats(drop_look=v), base)

    print("\n[s_atr]  (D=6)")
    for v in [4, 5, 6, 7, 8]:
        fmt(f"s_atr={v}", stats(s_atr=float(v)), base)

    print("\n[s_cap]  (D=0.20)")
    for v in [0.12, 0.15, 0.20, 0.25, 0.30]:
        fmt(f"s_cap={v}", stats(s_cap=v), base)

    print("\n[strail_k]  (D=3.5)")
    for v in [2.5, 3.0, 3.5, 4.0, 4.5]:
        fmt(f"strail_k={v}", stats(strail_k=v), base)

    print("\n[strail_arm_R]  (D=1.0)")
    for v in [0.5, 1.0, 1.5, 2.0]:
        fmt(f"strail_arm_R={v}", stats(strail_arm_R=v), base)

    print("\n[bear-depth sizing lo/mid/hi]  (D=0.5/1.0/1.0)")
    for lo, mid, hi in [(0.5, 1.0, 1.0), (0.25, 0.5, 1.0), (0.5, 0.75, 1.0),
                        (0.75, 1.0, 1.0), (0.5, 1.0, 1.25), (1.0, 1.0, 1.0)]:
        fmt(f"size {lo}/{mid}/{hi}", stats(size_lo=lo, size_mid=mid, size_hi=hi), base)


if __name__ == "__main__":
    main()
