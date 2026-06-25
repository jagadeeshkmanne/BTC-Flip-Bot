#!/usr/bin/env python3
"""backtest_demand_zone_wf.py — validate the 4h demand-zone survivor: rolling windows,
parameter robustness, and multi-coin. Settles whether the +43% OOS was real or a fluke.

Reuses the honest engine in backtest_demand_zone.py (BoS + zone retest, lagged pivots, intrabar
stops). Uses cached 4h history for BTC/ETH/BNB/SOL. Long-only (the survivor variant).
"""
from __future__ import annotations
import os, sys
import pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "scripts"))
from backtest_demand_zone import backtest  # honest engine

CACHE = os.path.join(HERE, "data/cache")
COINS = {"BTCUSDT": "BTCUSDT_4h_2019_binance.csv", "ETHUSDT": "ETHUSDT_4h_2019_binance.csv",
         "BNBUSDT": "BNBUSDT_4h_2019_binance.csv", "SOLUSDT": "SOLUSDT_4h_2019_binance.csv"}
CFG = dict(left=3, right=3, zone_frac=0.6, stop_buf_atr=0.5, min_rr=1.5, allow_long=True, allow_short=False)


def load(sym):
    return pd.read_csv(os.path.join(CACHE, COINS[sym]), parse_dates=["timestamp"]).reset_index(drop=True)


def main():
    btc = load("BTCUSDT")
    span = f"{btc.timestamp.iloc[0].date()}->{btc.timestamp.iloc[-1].date()}"
    print("=" * 88)
    print(f"DEMAND-ZONE (4h, long-only) VALIDATION — BTC {span}, {len(btc)} bars")
    print("=" * 88)

    # ---- 1) rolling non-overlapping windows (fixed params) ----
    print("\n[1] Rolling ~6-month windows (fixed params) — is it consistently positive?")
    win = 1080  # ~6 months of 4h bars
    print(f"  {'window':<26}{'net%':>8}{'dd%':>7}{'trades':>8}{'win%':>6}")
    pos_cnt = tot = 0
    for s in range(0, len(btc) - win, win):
        seg = btc.iloc[s:s + win].reset_index(drop=True)
        r = backtest(seg, **CFG)
        lbl = f"{seg.timestamp.iloc[0].date()}->{seg.timestamp.iloc[-1].date()}"
        print(f"  {lbl:<26}{r['net']:>8.1f}{r['dd']:>7.1f}{r['trades']:>8d}{r['wr']:>6.0f}")
        tot += 1; pos_cnt += int(r['net'] > 0)
    print(f"  -> positive in {pos_cnt}/{tot} windows")

    # ---- 2) parameter robustness (full history) ----
    print("\n[2] Parameter robustness (full BTC) — net% across the grid:")
    print(f"  {'zone_frac':>9}{'min_rr':>7}{'stop_atr':>9}{'net%':>9}{'dd%':>7}{'trades':>8}")
    for zf in (0.5, 0.6, 0.75):
        for rr in (1.0, 1.5, 2.0):
            cfg = {**CFG, "zone_frac": zf, "min_rr": rr}
            r = backtest(btc, **cfg)
            print(f"  {zf:>9.2f}{rr:>7.1f}{CFG['stop_buf_atr']:>9.1f}{r['net']:>9.1f}{r['dd']:>7.1f}{r['trades']:>8d}")

    # ---- 3) multi-coin (full + OOS 60/40) ----
    print("\n[3] Multi-coin (4h long-only, fixed params):")
    print(f"  {'coin':<8}{'FULL net%':>10}{'dd%':>7}{'tr':>5}{'wr%':>6}   {'OOS net%':>9}{'dd%':>7}{'tr':>5}")
    for sym in COINS:
        df = load(sym)
        oos = df.iloc[int(len(df) * 0.6):].reset_index(drop=True)
        rf, ro = backtest(df, **CFG), backtest(oos, **CFG)
        print(f"  {sym[:-4]:<8}{rf['net']:>10.1f}{rf['dd']:>7.1f}{rf['trades']:>5d}{rf['wr']:>6.0f}   "
              f"{ro['net']:>9.1f}{ro['dd']:>7.1f}{ro['trades']:>5d}")


if __name__ == "__main__":
    main()
