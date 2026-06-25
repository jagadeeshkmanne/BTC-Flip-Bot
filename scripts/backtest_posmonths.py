#!/usr/bin/env python3
"""backtest_posmonths.py — try to INCREASE positive months on the EMA8/200 reverse.

Baseline: EMA8/200 long/short reverse (60% positive months).
Add-ons to test whether they raise %positive / cut %negative months:
  +ADX   : only take a position when ADX>thr (trending); FLAT in chop (avoid whipsaw months)
  +htf   : only long if also above EMA200 on the higher (1D) trend; only short if below
Reports %positive / %negative / %flat months + net/DD/ret-DD + bear years. 4h BTC, Binance.
"""
from __future__ import annotations
import argparse, time
import pandas as pd
import requests

FEE_PCT = 0.00055; SLIP_PCT = 0.0005
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]


def fetch_binance(symbol, interval, start_ms):
    rows = []; url_ok = None; cur = start_ms
    while True:
        params = {"symbol": symbol, "interval": interval, "startTime": cur, "limit": 1000}
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
        cur = data[-1][0] + 1; time.sleep(0.2)
    seen = {int(x[0]): x for x in rows}
    rows = [seen[k] for k in sorted(seen)]
    return pd.DataFrame({"timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
                         "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
                         "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def atr_df(df, n=14):
    pc = df["close"].shift(1)
    return pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1).ewm(alpha=1/n, adjust=False).mean()
def adx(df, n=14):
    up = df["high"].diff(); dn = -df["low"].diff()
    pdm = ((up > dn) & (up > 0)) * up.clip(lower=0); ndm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    a = atr_df(df, n); pdi = 100*ema(pdm, n)/a; ndi = 100*ema(ndm, n)/a
    dx = 100*(pdi-ndi).abs()/(pdi+ndi).replace(0, 1e-9)
    return ema(dx, n)


def run(df, *, f, s, adx_thr, htf):
    c = df["close"]; ef = ema(c, f); es = ema(c, s); ax = adx(df, 14)
    d1 = (df.set_index("timestamp").resample("1D").agg({"close": "last"}).dropna())
    d1["e200"] = ema(d1["close"], 200); d1["avail"] = d1.index + pd.Timedelta(days=1)
    m = pd.merge_asof(df.sort_values("timestamp"), d1.reset_index()[["avail", "close", "e200"]].rename(columns={"close": "d_close", "e200": "d_e200"}).sort_values("avail"),
                      left_on="timestamp", right_on="avail", direction="backward")
    bull = ef > es
    bal = 1.0; side = 0; entry = 0.0; eq = []; ts = []
    for i in range(s+5, len(df)-1):
        nxt = float(df.iloc[i+1]["open"])
        b = bool(bull.iloc[i])
        want = 1 if b else -1
        if adx_thr and float(ax.iloc[i]) < adx_thr:
            want = 0
        if htf and pd.notna(m.iloc[i]["d_e200"]):
            dc = float(m.iloc[i]["d_close"]); de = float(m.iloc[i]["d_e200"])
            if want == 1 and not (dc > de):
                want = 0
            if want == -1 and not (dc < de):
                want = 0
        if side != want:
            if side == 1:
                fpx = nxt*(1-SLIP_PCT); bal = bal*(fpx/entry)*(1-2*FEE_PCT)
            elif side == -1:
                fpx = nxt*(1+SLIP_PCT); bal = bal*((2*entry-fpx)/entry)*(1-2*FEE_PCT)
            if want == 1:
                entry = nxt*(1+SLIP_PCT)
            elif want == -1:
                entry = nxt*(1-SLIP_PCT)
            side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if side == 0 else (bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry))
        ts.append(df.iloc[i+1]["timestamp"])
    e = pd.Series(eq, index=pd.to_datetime(ts))
    net = (e.iloc[-1]-1)*100; dd = (e/e.cummax()-1).min()*100
    mo = e.resample("ME").last().pct_change().dropna()*100
    pos = (mo > 0.2).mean()*100; neg = (mo < -0.2).mean()*100; flat = ((mo.abs() <= 0.2)).mean()*100
    ys = e.resample("YE").last(); base = e.iloc[0]
    by = {idx.year: ((ys.loc[idx]/ys.shift(1).loc[idx]-1)*100 if idx != ys.index[0] else (ys.loc[idx]/base-1)*100) for idx in ys.index}
    return {"net": net, "dd": dd, "pos": pos, "neg": neg, "flat": flat, "by": by}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="BTCUSDT"); ap.add_argument("--start", default="2019-01-01"); a = ap.parse_args()
    df = fetch_binance(a.symbol, "4h", int(pd.Timestamp(a.start).timestamp()*1000))
    if len(df) < 500:
        print("insufficient data"); return
    print(f"{a.symbol} 4h EMA8/200 reverse — increasing positive months  ({df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()})\n")
    print(f"{'variant':22s} {'net%':>8} {'DD%':>7} {'+mo%':>5} {'-mo%':>5} {'flat%':>6} | {'2022':>6} {'2025':>6} {'2026':>6}")
    cfgs = [
        ("baseline reverse", dict(adx_thr=0, htf=False)),
        ("+ADX>15", dict(adx_thr=15, htf=False)),
        ("+ADX>20", dict(adx_thr=20, htf=False)),
        ("+ADX>25", dict(adx_thr=25, htf=False)),
        ("+1D-trend filter", dict(adx_thr=0, htf=True)),
        ("+ADX>20 +1D", dict(adx_thr=20, htf=True)),
    ]
    for name, kw in cfgs:
        r = run(df, f=8, s=200, **kw)
        print(f"{name:22s} {r['net']:8.0f} {r['dd']:7.1f} {r['pos']:5.0f} {r['neg']:5.0f} {r['flat']:6.0f} | "
              f"{r['by'].get(2022,0):6.1f} {r['by'].get(2025,0):6.1f} {r['by'].get(2026,0):6.1f}")


if __name__ == "__main__":
    main()
