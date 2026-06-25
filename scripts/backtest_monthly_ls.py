#!/usr/bin/env python3
"""backtest_monthly_ls.py — long/short reverse vs short-on-signal, monthly grid + fine-tune.

reverse  : long when EMA_f>EMA_s, SHORT the entire time EMA_f<EMA_s (always in market).
sigshort : long when EMA_f>EMA_s; SHORT only when (EMA_f<EMA_s AND close<EMA_f), FLAT on
           bounces above EMA_f (so you sit out relief rallies instead of holding shorts into them).
Prints a year x month return grid for the chosen mode + summary stats, and a fine-tune
comparison across EMA pairs (full net, DD, ret/DD, %positive months, key bear years).
4h BTC, Binance, fee+slippage, next-open fills.
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
                         "open": [float(x[1]) for x in rows], "close": [float(x[4]) for x in rows]}).reset_index(drop=True)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def run(df, *, f, s, mode):
    c = df["close"]; ef = ema(c, f); es = ema(c, s)
    bull = ef > es
    bal = 1.0; side = 0; entry = 0.0; eq = []; ts = []; lp = sp = 0.0
    for i in range(s+5, len(df)-1):
        nxt = float(df.iloc[i+1]["open"]); cl = float(df.iloc[i]["close"])
        if bool(bull.iloc[i]):
            want = 1
        elif mode == "reverse":
            want = -1
        elif mode == "sigshort":
            want = -1 if cl < float(ef.iloc[i]) else 0
        else:
            want = 0
        if side != want:
            if side == 1:
                fpx = nxt*(1-SLIP_PCT); new = bal*(fpx/entry)*(1-2*FEE_PCT); lp += new-bal; bal = new
            elif side == -1:
                fpx = nxt*(1+SLIP_PCT); new = bal*((2*entry-fpx)/entry)*(1-2*FEE_PCT); sp += new-bal; bal = new
            if want == 1:
                entry = nxt*(1+SLIP_PCT)
            elif want == -1:
                entry = nxt*(1-SLIP_PCT)
            side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if side == 0 else (bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry))
        ts.append(df.iloc[i+1]["timestamp"])
    return pd.Series(eq, index=pd.to_datetime(ts)), lp*100, sp*100


def stats(eqser):
    dd = (eqser/eqser.cummax()-1).min()*100
    net = (eqser.iloc[-1]-1)*100
    m = eqser.resample("ME").last().pct_change().dropna()*100
    pos = (m > 0).mean()*100
    yr = {}
    for idx, v in eqser.resample("ME").last().pct_change().dropna().items():
        yr.setdefault(idx.year, 1.0)
    ys = eqser.resample("YE").last(); ychg = ys.pct_change()
    by = {}
    base = eqser.iloc[0]
    for idx in ys.index:
        by[idx.year] = (ys.loc[idx]/ys.shift(1).loc[idx]-1)*100 if idx != ys.index[0] else (ys.loc[idx]/base-1)*100
    return net, float(dd), pos, m, by


def print_grid(m, label):
    grid = {}
    for idx, v in m.items():
        grid.setdefault(idx.year, {})[idx.month] = v
    months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    print(f"\n{label} — monthly returns (%):")
    print("year  " + "".join(f"{mm:>7}" for mm in months) + f"{'YEAR':>9}")
    for y in sorted(grid):
        row = grid[y]; cells = ""
        yr = 1.0
        for mn in range(1, 13):
            if mn in row:
                cells += f"{row[mn]:7.1f}"; yr *= (1+row[mn]/100)
            else:
                cells += f"{'-':>7}"
        print(f"{y}  {cells}{(yr-1)*100:9.1f}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", default="2019-01-01"); ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200); a = ap.parse_args()
    df = fetch_binance(a.symbol, "4h", int(pd.Timestamp(a.start).timestamp()*1000))
    if len(df) < 500:
        print("insufficient data"); return
    print(f"{a.symbol} 4h  bars={len(df)} {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}")

    # monthly grid for the chosen pair, reverse
    eqr, lpr, spr = run(df, f=a.fast, s=a.slow, mode="reverse")
    netr, ddr, posr, mr, byr = stats(eqr)
    print_grid(mr, f"EMA{a.fast}/{a.slow} REVERSE")
    print(f"  net={netr:.0f}% dd={ddr:.1f}% ret/DD={netr/abs(ddr):.1f} positive_months={posr:.0f}% shortPnL={spr:.0f}%")

    # fine-tune comparison: reverse vs sigshort across pairs
    print("\nFINE-TUNE  (R=reverse, S=sigshort)  net% / DD% / ret-DD / %pos-mo | 2022 2025 2026")
    for f, s in ((8, 200), (13, 200), (21, 200), (50, 200), (50, 150)):
        for mode in ("reverse", "sigshort"):
            eqs, lp, sp = run(df, f=f, s=s, mode=mode)
            net, dd, pos, m, by = stats(eqs)
            tag = "R" if mode == "reverse" else "S"
            print(f"  EMA{f}/{s} {tag}: {net:7.0f} / {dd:6.1f} / {net/abs(dd):5.1f} / {pos:3.0f}% | "
                  f"{by.get(2022,0):6.1f} {by.get(2025,0):6.1f} {by.get(2026,0):6.1f}")


if __name__ == "__main__":
    main()
