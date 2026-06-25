#!/usr/bin/env python3
"""backtest_multibot_2x.py — 4-coin EMA8/200 reverse with leverage + per-position STOP LOSS.

Each coin: EMA8/200 long/short reverse at leverage L, with a hard stop at stop_pct adverse
move from entry. After a stop, go FLAT until the next EMA cross (so we don't instantly re-enter
into the same adverse move). Liquidation modeled if price moves 1/L against us before the stop.
Equal-weight portfolio. Sweeps L x stop_pct. Reports CAGR / maxDD / ret-DD / recent / worst-yr.
Binance 4h.  Goal: does 2x+stop reduce DD / beat 1x?
"""
from __future__ import annotations
import time
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


def run(df, f, s, L, stop_pct):
    c = df["close"]; bull = ema(c, f) > ema(c, s)
    cu = bull & (~bull.shift(1, fill_value=False)); cd = (~bull) & (bull.shift(1, fill_value=True))
    bal = 1.0; side = 0; entry = 0.0; stop = 0.0; liq = 0.0; active = True; eq = []; ts = []
    for i in range(s+5, len(df)-1):
        hi = float(df.iloc[i]["high"]); lo = float(df.iloc[i]["low"])
        # intrabar stop / liquidation on current position
        if side != 0 and bal > 1e-9:
            if side == 1:
                if lo <= liq:
                    bal = 0.0; side = 0; active = False
                elif stop_pct > 0 and lo <= stop:
                    r = stop/entry-1; bal = bal*(1+L*r)*(1-2*FEE_PCT*L); bal = max(bal, 0.0); side = 0; active = False
            else:
                if hi >= liq:
                    bal = 0.0; side = 0; active = False
                elif stop_pct > 0 and hi >= stop:
                    r = 1-stop/entry; bal = bal*(1+L*r)*(1-2*FEE_PCT*L); bal = max(bal, 0.0); side = 0; active = False
        base = 1 if bool(bull.iloc[i]) else -1
        if bool(cu.iloc[i]) or bool(cd.iloc[i]):
            active = True
        want = base if active else 0
        nxt = float(df.iloc[i+1]["open"])
        if side != want and bal > 1e-9:
            if side == 1:
                fpx = nxt*(1-SLIP_PCT); bal = bal*(1+L*(fpx/entry-1))*(1-2*FEE_PCT*L)
            elif side == -1:
                fpx = nxt*(1+SLIP_PCT); bal = bal*(1+L*(1-fpx/entry))*(1-2*FEE_PCT*L)
            bal = max(bal, 0.0)
            if want == 1 and bal > 1e-9:
                entry = nxt*(1+SLIP_PCT); stop = entry*(1-stop_pct); liq = entry*(1-1.0/L)
            elif want == -1 and bal > 1e-9:
                entry = nxt*(1-SLIP_PCT); stop = entry*(1+stop_pct); liq = entry*(1+1.0/L)
            side = want
        nc = float(df.iloc[i+1]["close"])
        if side == 1 and bal > 1e-9:
            eq.append(max(bal*(1+L*(nc/entry-1)), 0.0))
        elif side == -1 and bal > 1e-9:
            eq.append(max(bal*(1+L*(1-nc/entry)), 0.0))
        else:
            eq.append(bal)
        ts.append(df.iloc[i+1]["timestamp"])
    return pd.Series(eq, index=pd.to_datetime(ts)).pct_change()


def stats(R):
    e = (1+R.mean(axis=1, skipna=True).fillna(0)).cumprod()
    yrs = (e.index[-1]-e.index[0]).days/365.25
    cagr = ((e.iloc[-1]/e.iloc[0])**(1/yrs)-1)*100
    dd = (e/e.cummax()-1).min()*100
    ych = e.resample("YE").last().pct_change().dropna()*100
    by = {idx.year: float(ych.loc[idx]) for idx in ych.index}
    recent = [by[y] for y in by if y >= 2023]
    rec = (pd.Series([1+r/100 for r in recent]).prod()**(1/len(recent))-1)*100 if recent else 0
    return cagr, float(dd), rec, (min(by.values()) if by else 0)


def main():
    coins = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT"]
    start = int(pd.Timestamp("2020-08-01").timestamp()*1000)
    data = {s: fetch(s, start) for s in coins}
    print("4-coin EMA8/200 reverse multibot — leverage x stop-loss sweep\n")
    print(f"{'lev':>4} {'stop':>6} | {'CAGR%':>7} {'maxDD%':>8} {'ret/DD':>7} {'recent%':>8} {'worstYr%':>9} {'green':>6}")
    for L in (1, 2):
        for sp in (0.0, 0.08, 0.12, 0.18):
            R = pd.DataFrame({s: run(data[s], 8, 200, L, sp) for s in coins}).sort_index()
            cagr, dd, rec, worst = stats(R)
            stxt = f"{int(sp*100)}%" if sp else "none"
            print(f"{L:>3}x {stxt:>6} | {cagr:7.0f} {dd:8.0f} {cagr/abs(dd):7.2f} {rec:8.0f} {worst:9.0f} {'YES' if worst > 0 else 'no':>6}")


if __name__ == "__main__":
    main()
