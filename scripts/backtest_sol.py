#!/usr/bin/env python3
"""backtest_sol.py — SOL EMA reverse, year/month grid + trailing-stop fine-tune.

Base: EMA f/s long/short reverse on SOL 4h. Optional chandelier TRAILING STOP: exit to
flat when price retraces trail_k*ATR from the favorable extreme; re-enter only on the next
EMA cross (so trailing locks profit, then you wait for a fresh signal). Sweeps trail_k and
EMA pairs; prints the full monthly grid for the chosen config. Binance, fee+slippage.
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
def atr(df, n=14):
    pc = df["close"].shift(1)
    return pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1).ewm(alpha=1/n, adjust=False).mean()


def run(df, *, f, s, reverse=True, trail_k=0.0):
    c = df["close"]; ef = ema(c, f); es = ema(c, s); a = atr(df, 14)
    bull = ef > es
    cross_up = bull & (~bull.shift(1, fill_value=False))
    cross_dn = (~bull) & (bull.shift(1, fill_value=True))
    bal = 1.0; side = 0; entry = 0.0; ext = 0.0; eq = []; ts = []
    stopped_dir = 0
    for i in range(s+5, len(df)-1):
        nxt = float(df.iloc[i+1]["open"]); cl = float(df.iloc[i]["close"]); av = float(a.iloc[i])
        # desired base side from EMA
        base = 1 if bool(bull.iloc[i]) else (-1 if reverse else 0)
        want = base
        if trail_k > 0 and side != 0:
            ext = max(ext, cl) if side == 1 else min(ext, cl)
            hit = (cl < ext - trail_k*av) if side == 1 else (cl > ext + trail_k*av)
            if hit:
                want = 0; stopped_dir = side       # go flat; wait for fresh cross to re-enter
        if trail_k > 0 and side == 0:
            # re-enter only on a fresh cross (not just because EMA still aligned)
            if base == 1 and not bool(cross_up.iloc[i]):
                want = 0
            elif base == -1 and not bool(cross_dn.iloc[i]):
                want = 0
            else:
                want = base
        if side != want:
            if side == 1:
                fpx = nxt*(1-SLIP_PCT); bal = bal*(fpx/entry)*(1-2*FEE_PCT)
            elif side == -1:
                fpx = nxt*(1+SLIP_PCT); bal = bal*((2*entry-fpx)/entry)*(1-2*FEE_PCT)
            if want == 1:
                entry = nxt*(1+SLIP_PCT); ext = nxt
            elif want == -1:
                entry = nxt*(1-SLIP_PCT); ext = nxt
            side = want
        nc = float(df.iloc[i+1]["close"])
        eq.append(bal if side == 0 else (bal*nc/entry if side == 1 else bal*(2*entry-nc)/entry))
        ts.append(df.iloc[i+1]["timestamp"])
    return pd.Series(eq, index=pd.to_datetime(ts))


def summ(e):
    net = (e.iloc[-1]/e.iloc[0]-1)*100; dd = (e/e.cummax()-1).min()*100
    mo = e.resample("ME").last().pct_change().dropna()*100
    return net, float(dd), (mo > 0.2).mean()*100, mo


def grid(mo, label):
    g = {}
    for idx, v in mo.items():
        g.setdefault(idx.year, {})[idx.month] = v
    mn = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    print(f"\n{label} monthly %:")
    print("year " + "".join(f"{m:>7}" for m in mn) + f"{'YEAR':>9}")
    for y in sorted(g):
        row = g[y]; cells = ""; yr = 1.0
        for m in range(1, 13):
            if m in row:
                cells += f"{row[m]:7.1f}"; yr *= (1+row[m]/100)
            else:
                cells += f"{'-':>7}"
        print(f"{y} {cells}{(yr-1)*100:9.0f}")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="SOLUSDT"); a = ap.parse_args()
    df = fetch_binance(a.symbol, "4h", int(pd.Timestamp("2020-01-01").timestamp()*1000))
    if len(df) < 500:
        print("insufficient data"); return
    print(f"{a.symbol} 4h ({df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()})  buy&hold {(df.iloc[-1]['close']/df.iloc[0]['close']-1)*100:.0f}%")
    print("\nFINE-TUNE (reverse):  net% / DD% / ret-DD / +mo%")
    best = None
    for f, s in ((8, 200), (13, 200), (21, 200), (50, 200)):
        for tk in (0.0, 2.0, 3.0, 4.0):
            e = run(df, f=f, s=s, reverse=True, trail_k=tk)
            net, dd, pos, mo = summ(e)
            tag = f"EMA{f}/{s} trail{tk if tk else 'off'}"
            print(f"  {tag:20s} {net:9.0f} / {dd:6.1f} / {net/abs(dd):6.1f} / {pos:.0f}%")
            score = net/abs(dd)
            if best is None or score > best[0]:
                best = (score, tag, e)
    _, tag, e = best
    net, dd, pos, mo = summ(e)
    grid(mo, f"BEST: {tag}  (net {net:.0f}% dd {dd:.1f}% ret/DD {net/abs(dd):.0f} +mo {pos:.0f}%)")


if __name__ == "__main__":
    main()
