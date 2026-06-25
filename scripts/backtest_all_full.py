#!/usr/bin/env python3
"""backtest_all_full.py — all 4 live bots over their FULL available history.

Reports for each: window, total years, CAGR, absolute return ($5,000 -> $X and multiple), max DD.
Windows differ by data availability (alts only from ~2021; BTC/ETH from 2017).
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt
from backtest_compare_all import btcv2_eq, btcalts_eq, allweather_eq

CAP0 = 5000.0


def trend_btc_eq():
    """trend_btc: long/flat 4h BTC trend (EMA13>20 AND close>EMA200), vol-targeted leverage
    (~live bot's dynamic-lev, capped). Approximation of the deployed dynamic-leverage bot."""
    d = bt.load("BTCUSDT", "4h"); c = d["close"]
    long = ((bt.ema(c, 13) > bt.ema(c, 20)) & (c > bt.ema(c, 200))).shift(1).fillna(False)
    rv = c.pct_change().rolling(30).std()
    lev = (rv.rolling(720, min_periods=90).median() / rv).clip(0.6, 2.5).shift(1).fillna(1.0)
    pos = long.astype(float) * lev
    oo = (d["open"].shift(-1) / d["open"] - 1).fillna(0)
    turn = pos.diff().abs().fillna(pos.abs())
    eq = (1 + pos * oo - turn * (bt.FEE_PCT + bt.SLIP_PCT)).cumprod()
    eq.index = pd.to_datetime(d["timestamp"])
    return eq


def report(name, eq, approx=False):
    eq = eq[eq > 0]
    if len(eq) < 50:
        print(f"  {name:<26} (insufficient data)"); return
    s = eq / eq.iloc[0] * CAP0
    yrs = (s.index[-1] - s.index[0]).days / 365.25
    cg = (s.iloc[-1] / CAP0) ** (1 / yrs) - 1
    dd = (s / s.cummax() - 1).min()
    mult = s.iloc[-1] / CAP0
    tag = " *approx" if approx else ""
    print(f"  {name:<26}{s.index[0].date()}->{s.index[-1].date()}  {yrs:>4.1f}y  CAGR {cg*100:>4.0f}%  "
          f"${s.iloc[-1]:>13,.0f} ({mult:>6,.0f}x)  DD {dd*100:>4.0f}%{tag}")


def main():
    print("=" * 104)
    print("ALL 4 LIVE BOTS — full available history (start $5,000). Windows differ by data availability.")
    print("=" * 104)
    print(f"  {'bot':<26}{'window':<24}{'yrs':>5}  {'CAGR':>9}  {'final $ (x from 5k)':>23}  {'maxDD':>7}")
    report("btcv2 (V2 conv+ETHshort)", btcv2_eq())
    report("btcalts (BTC->alts)", btcalts_eq())
    report("allweather (4-coin)", allweather_eq())
    report("trend_btc (4h BTC trend)", trend_btc_eq(), approx=True)
    print("\n  NOTE: btcv2 has the longest history (3 bears incl. 2018); btcalts/allweather are alt-")
    print("  data-limited to ~2021+; trend_btc is BTC-only (longest BTC history). *trend_btc is an")
    print("  approximation of its dynamic-leverage logic. Absolute $ are inflated by BTC's early growth.")


if __name__ == "__main__":
    main()
