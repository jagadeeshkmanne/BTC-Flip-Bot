#!/usr/bin/env python3
"""backtest_carry.py — market-neutral CASH-AND-CARRY (funding harvest), the income engine.

Position: LONG spot + SHORT perp of the same coin = delta-neutral (price exposure ~0).
Income: the short-perp leg RECEIVES funding every 8h when the funding rate is positive
(longs pay shorts), pays when negative. Net return stream ≈ cumulative funding − costs.
This is direction-NEUTRAL, so it can be green in down-months too — the thing a directional
bot structurally cannot do.

Data: data/cache/{SYM}_funding_binance.csv  (8h funding rate history, fetched from Binance USD-M).

HONEST accounting:
  - funding applied on the SHORT notional each 8h (sign: +rate received, −rate paid)
  - one-time ENTRY + EXIT cost: 2 legs (spot + perp) × taker fee 0.055%/side
  - a periodic REBALANCE drag to keep delta-neutral as price drifts (configurable)
  - return measured on deployed capital (spot doubles as perp collateral => ~1× capital)
  - 60/40 in-sample/out-of-sample split; monthly stats = the headline (% positive months)

Variants:
  always   : hold the carry continuously (collect + and − funding). The honest baseline.
  positive : only hold when trailing-mean funding > thr; else sit in cash (0% / stable). Timing.
"""
from __future__ import annotations
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE = bt.FEE_PCT      # 0.055%/side taker
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_funding(sym):
    p = os.path.join(bt.CACHE, f"{sym}_funding_binance.csv")
    df = pd.read_csv(p)
    df["t"] = pd.to_datetime(df["fundingTime"], unit="ms")
    df["rate"] = df["fundingRate"].astype(float)
    return df[["t", "rate"]].sort_values("t").reset_index(drop=True)


def run(sym="BTCUSDT", mode="always", thr=0.0, look=9, rebal_bps_per_day=0.0):
    f = load_funding(sym)
    r = f["rate"].values.copy()
    n = len(r)
    # entry signal per 8h bar
    if mode == "positive":
        trail = pd.Series(r).rolling(look).mean().shift(1).fillna(0).values  # causal trailing funding
        hold = (trail > thr)
    else:
        hold = np.ones(n, bool)
    # per-8h net carry return on equity (short receives +rate when hold)
    reb = rebal_bps_per_day / 1e4 / 3.0   # per-8h rebalance drag
    ret = np.where(hold, r - reb, 0.0)
    # transaction cost on each flip in/out of the position (2 legs each side)
    flip = np.abs(np.diff(np.concatenate([[0], hold.astype(int)])))
    ret = ret - flip * (2 * FEE)          # open or close = 2 legs × fee
    eq = pd.Series((1 + ret).cumprod(), index=f["t"])
    return eq, hold.mean()


def monthly(eq):
    m = eq.resample("ME").last().pct_change().dropna()
    return m


def report(eq, util, label):
    m = monthly(eq)
    cagr, dd, rdd = bt.metrics(eq)
    oos = eq[eq.index >= eq.index[int(len(eq) * 0.6)]]
    om = monthly(oos)
    print(f"{label:30s} util {util:4.0%} | CAGR {cagr:6.1%} maxDD {dd:6.1%} | "
          f"green {(m>0).mean():4.0%} (OOS {(om>0).mean():4.0%}) | avg_mo {m.mean():5.2%} "
          f"worst {m.min():6.2%} | std {m.std():5.2%}")
    return m


if __name__ == "__main__":
    print("CASH-AND-CARRY funding harvest (market-neutral). Binance funding 2020-2026.\n")
    for sym in ["BTCUSDT", "ETHUSDT"]:
        print(f"--- {sym} ---")
        eq, u = run(sym, "always");              report(eq, u, "always-on (collect +/-)")
        eq, u = run(sym, "always", rebal_bps_per_day=2.0); report(eq, u, "always-on + 2bps/day rebal drag")
        eq, u = run(sym, "positive", thr=0.0);   report(eq, u, "only-when-funding>0 (trail 3d)")
        eq, u = run(sym, "positive", thr=0.00005); report(eq, u, "only-when-funding>0.5bp (trail 3d)")
        # raw funding context
        f = load_funding(sym); ann = f["rate"].mean() * 3 * 365
        print(f"    raw mean funding {f['rate'].mean()*100:.4f}%/8h  (~{ann*100:.1f}%/yr gross), "
              f"% of 8h periods positive: {(f['rate']>0).mean()*100:.0f}%\n")
    # correlation of carry monthly returns to BTC price (should be ~0)
    btc = bt.load("BTCUSDT", "1d"); bm = (btc.set_index(pd.to_datetime(btc['timestamp']))['close']
          .resample("ME").last().pct_change())
    eq, _ = run("BTCUSDT", "always"); cm = monthly(eq)
    j = pd.concat([cm, bm], axis=1, join="inner").dropna()
    print(f"Carry vs BTC monthly-return correlation: {j.iloc[:,0].corr(j.iloc[:,1]):+.2f}  (target ~0 = market-neutral)")
