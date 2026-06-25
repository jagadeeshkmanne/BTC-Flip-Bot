#!/usr/bin/env python3
"""backtest_squeeze.py — TTM Squeeze (+momentum) on the 4-coin basket vs EMA-reverse benchmark.

TTM Squeeze: Bollinger Bands (SMA20 +/- 2sd) inside Keltner Channels (EMA20 +/- 1.5*ATR20)
= 'squeeze on'. When it releases ('fires') with momentum up, you enter and RIDE (the video's
'don't wait for pullback' lesson). Momentum = linreg(close - avg(donchian-mid, SMA20), 20).

Variants (4-coin equal-weight, 1x):
  sqz_LF : enter long when squeeze fired recently AND mom>0; exit when mom<0 (flat).
  sqz_LS : same but go short when mom<0 (long/short).
  mom_LS : momentum direction only, NO squeeze gate (isolates the squeeze's contribution).
  EMA8/200 reverse : our benchmark.
Reports CAGR/maxDD/ret-DD/recent/worst-yr. Binance 4h.
"""
from __future__ import annotations
import time
import numpy as np
import pandas as pd
import requests

FEE_PCT = 0.00055; SLIP_PCT = 0.0005
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]


def fetch(symbol, start_ms):
    rows = []; url_ok = None; cur = start_ms
    while True:
        params = {"symbol": symbol, "interval": "4h", "startTime": cur, "limit": 1000}
        data = None
        for h in ([url_ok] if url_ok else HOSTS):
            try:
                r = requests.get(f"{h}/api/v3/klines", params=params, timeout=20)
                if r.status_code == 200:
                    data = r.json(); url_ok = h; break
            except Exception:
                continue
        if not data:
            break
        rows.extend(data)
        if len(data) < 1000:
            break
        cur = data[-1][0]+1; time.sleep(0.15)
    seen = {int(x[0]): x for x in rows}; rows = [seen[k] for k in sorted(seen)]
    return pd.DataFrame({"timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
                         "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
                         "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def ttm(df, n=20, bb_mult=2.0, kc_mult=1.5):
    c = df["close"]
    basis = c.rolling(n).mean(); sd = c.rolling(n).std()
    bb_u = basis + bb_mult*sd; bb_l = basis - bb_mult*sd
    pc = c.shift(1)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n).mean()
    kc_u = basis + kc_mult*atr; kc_l = basis - kc_mult*atr
    sqz_on = (bb_l > kc_l) & (bb_u < kc_u)
    hh = df["high"].rolling(n).max(); ll = df["low"].rolling(n).min()
    delta = c - ((hh+ll)/2 + basis)/2

    def linreg_last(y):
        x = np.arange(len(y)); s = x - x.mean()
        denom = (s*s).sum()
        slope = (s*(y-y.mean())).sum()/denom if denom else 0.0
        inter = y.mean() - slope*x.mean()
        return slope*(len(y)-1) + inter
    mom = delta.rolling(n).apply(linreg_last, raw=True)
    return sqz_on.fillna(False), mom.fillna(0.0)


def reverse_returns(df):
    c = df["close"]; bull = ema(c, 8) > ema(c, 200)
    bal = 1.0; side = 0; entry = 0.0; eq = []; ts = []
    for i in range(205, len(df)-1):
        nxt = float(df.iloc[i+1]["open"]); want = 1 if bool(bull.iloc[i]) else -1
        if side != want:
            if side == 1:
                f = nxt*(1-SLIP_PCT); bal = bal*(f/entry)*(1-2*FEE_PCT)
            elif side == -1:
                f = nxt*(1+SLIP_PCT); bal = bal*((2*entry-f)/entry)*(1-2*FEE_PCT)
            entry = nxt*(1+SLIP_PCT) if want == 1 else nxt*(1-SLIP_PCT); side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry); ts.append(df.iloc[i+1]["timestamp"])
    return pd.Series(eq, index=pd.to_datetime(ts)).pct_change()


def squeeze_returns(df, allow_short, gate, arm=10):
    sqz_on, mom = ttm(df)
    fired = sqz_on.shift(1, fill_value=False) & (~sqz_on)
    armed = fired.rolling(arm, min_periods=1).max().astype(bool) if gate else pd.Series(True, index=df.index)
    bal = 1.0; side = 0; entry = 0.0; eq = []; ts = []
    for i in range(205, len(df)-1):
        m = float(mom.iloc[i]); a = bool(armed.iloc[i])
        if side == 0:
            want = 1 if (a and m > 0) else (-1 if (a and m < 0 and allow_short) else 0)
        elif side == 1:
            want = 1 if m > 0 else (-1 if (m < 0 and allow_short) else 0)
        else:
            want = -1 if m < 0 else (1 if m > 0 else 0)
        nxt = float(df.iloc[i+1]["open"])
        if side != want:
            if side == 1:
                f = nxt*(1-SLIP_PCT); bal = bal*(f/entry)*(1-2*FEE_PCT)
            elif side == -1:
                f = nxt*(1+SLIP_PCT); bal = bal*((2*entry-f)/entry)*(1-2*FEE_PCT)
            if want == 1:
                entry = nxt*(1+SLIP_PCT)
            elif want == -1:
                entry = nxt*(1-SLIP_PCT)
            side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if side == 0 else (bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry))
        ts.append(df.iloc[i+1]["timestamp"])
    return pd.Series(eq, index=pd.to_datetime(ts)).pct_change()


def stats(R):
    e = (1+R.mean(axis=1, skipna=True).fillna(0)).cumprod()
    yrs = (e.index[-1]-e.index[0]).days/365.25
    cagr = ((e.iloc[-1]/e.iloc[0])**(1/yrs)-1)*100; dd = (e/e.cummax()-1).min()*100
    ych = e.resample("YE").last().pct_change().dropna()*100
    by = {idx.year: float(ych.loc[idx]) for idx in ych.index}
    recent = [by[y] for y in by if y >= 2023]
    rec = (pd.Series([1+r/100 for r in recent]).prod()**(1/len(recent))-1)*100 if recent else 0
    return cagr, float(dd), rec, (min(by.values()) if by else 0)


def main():
    coins = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
    start = int(pd.Timestamp("2020-08-01").timestamp()*1000)
    data = {s: fetch(s, start) for s in coins}
    print("4-coin basket, 1x — TTM Squeeze vs EMA-reverse benchmark\n")
    print(f"{'strategy':28s} {'CAGR%':>7} {'maxDD%':>8} {'ret/DD':>7} {'recent%':>8} {'worstYr%':>9} {'green':>6}")
    configs = {
        "EMA8/200 reverse (bench)": pd.DataFrame({s: reverse_returns(data[s]) for s in coins}),
        "squeeze long/flat":        pd.DataFrame({s: squeeze_returns(data[s], False, True) for s in coins}),
        "squeeze long/short":       pd.DataFrame({s: squeeze_returns(data[s], True, True) for s in coins}),
        "momentum only (no gate)":  pd.DataFrame({s: squeeze_returns(data[s], True, False) for s in coins}),
    }
    for name, R in configs.items():
        cagr, dd, rec, worst = stats(R.sort_index())
        print(f"{name:28s} {cagr:7.0f} {dd:8.0f} {cagr/abs(dd):7.2f} {rec:8.0f} {worst:9.0f} {'YES' if worst > 0 else 'no':>6}")


if __name__ == "__main__":
    main()
