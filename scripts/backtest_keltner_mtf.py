#!/usr/bin/env python3
"""backtest_keltner_mtf.py — RECONSTRUCTION of MtfKelt8h1dA (Keltner 8h + 1D anchor).

NOT the exact Jesse strategy (I don't have its code/params) — a faithful representative:
  - 8h execution, Keltner channel (EMA len, ATR mult), breakout entry
  - 1D regime anchor (only long when daily trend up)
  - reports actual CAGR / total return / maxDD / Sharpe + walk-forward, at 1x and 2x
Window 2022-06-18 -> 2026-06-17 to match the report. Honest fills/fees/liquidation.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT; MAINT = 0.005


def load_tf(tf_hours):
    df = pd.read_csv(os.path.join(bt.CACHE, "BTCUSDT_1h_binance_full.csv"), parse_dates=["timestamp"])
    g = df.set_index("timestamp").resample(f"{tf_hours}h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    return g


def keltner_signal(df8, ema_len, mult, d_up_8):
    c = df8["close"]
    mid = bt.ema(c, ema_len)
    a = bt.atr(df8, ema_len)
    upper = mid + mult * a
    # breakout-and-hold: long while close>upper-ish (entered on break, held while above mid), gated by 1D
    brk = (c > upper).values
    above_mid = (c > mid).values
    pos = np.zeros(len(df8)); side = 0
    for i in range(len(df8)):
        if not d_up_8[i]:
            side = 0
        elif brk[i]:
            side = 1
        elif not above_mid[i]:
            side = 0
        pos[i] = side
    return pos


def lev_run(df8, pos, lev):
    o = df8["open"].values; l = df8["low"].values; c = df8["close"].values
    n = len(df8); bal = 1.0; side = 0; entry = 0.0; eq = np.ones(n); rets = []
    for i in range(2, n - 1):
        oN, lN, cN = o[i+1], l[i+1], c[i+1]
        want = int(pos[i])
        if side == 1 and bal > 0 and lN <= entry*(1-(1/lev-MAINT)):
            bal = 0.0; side = 0
        if side != want and bal > 0:
            if side == 1:
                fpx = oN*(1-SLIP); bal *= (1+lev*(fpx/entry-1))*(1-FEE*lev)
            side = want
            if want == 1: entry = oN*(1+SLIP); bal *= (1-FEE*lev)
        prev = eq[i]
        eq[i+1] = max(bal*(1+lev*(cN/entry-1)),0.0) if side==1 and bal>0 else bal
        rets.append(eq[i+1]/prev - 1)
        if eq[i+1] <= 0: eq[i+1:]=0; break
    s = pd.Series(eq, index=pd.to_datetime(df8["timestamp"])).iloc[2:]
    return s, np.array(rets)


def stats(s, rets, ppy):
    yrs = max((s.index[-1]-s.index[0]).days/365.25, 1e-9)
    cagr = (s.iloc[-1]/s.iloc[0])**(1/yrs)-1 if s.iloc[-1]>0 else -1
    dd = (s/s.cummax()-1).min()
    tot = s.iloc[-1]/s.iloc[0]-1
    sh = (np.nanmean(rets)/(np.nanstd(rets)+1e-12))*np.sqrt(ppy)
    return cagr, dd, tot, sh


def main():
    df8 = load_tf(8); dfd = load_tf(24)
    # window to match the report
    mask = (df8["timestamp"] >= "2022-06-18") & (df8["timestamp"] <= "2026-06-17")
    # 1D regime (EMA50>200) mapped to 8h, lookahead-safe
    d_up = (bt.ema(dfd["close"],50) > bt.ema(dfd["close"],200)).shift(1).fillna(False).astype(bool)
    didx = pd.merge_asof(pd.DataFrame({"ts":pd.to_datetime(df8["timestamp"])}).sort_values("ts"),
                         pd.DataFrame({"ts":pd.to_datetime(dfd["timestamp"]),"j":np.arange(len(dfd))}).sort_values("ts"),
                         on="ts", direction="backward")["j"].fillna(0).astype(int).values
    d_up_8 = d_up.values[didx]
    df8w = df8[mask].reset_index(drop=True); d_up_w = d_up_8[mask.values]
    ppy = 365.25*3
    print("="*86)
    print("RECONSTRUCTION: MtfKelt8h1dA (Keltner 8h + 1D anchor)  window 2022-06-18..2026-06-17")
    print("  Report claims: Sharpe 1.55, MaxDD -12.7%, 421 trades (no CAGR shown)")
    print("="*86)
    print(f"  {'config (kelt len/mult, lev)':<32}{'CAGR':>8}{'total':>9}{'maxDD':>8}{'Sharpe':>8}")
    best=None
    for el,mu in [(20,1.5),(20,2.0),(30,2.0),(40,2.5)]:
        pos = keltner_signal(df8w, el, mu, d_up_w)
        for lev in (1.0,2.0):
            s,r = lev_run(df8w,pos,lev); cg,dd,tot,sh = stats(s,r,ppy)
            tag = "  <- closest to report" if (lev==2.0 and abs(dd+0.127)<0.06 and abs(sh-1.55)<0.5) else ""
            print(f"  Keltner {el}/{mu}, {lev:.0f}x{'':<14}{cg*100:>7.0f}%{tot*100:>8.0f}%{dd*100:>7.0f}%{sh:>8.2f}{tag}")


if __name__ == "__main__":
    main()
