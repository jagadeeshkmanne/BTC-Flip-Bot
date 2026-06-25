#!/usr/bin/env python3
"""backtest_grid_neutral.py — neutral, dynamically re-centering (trailing) grid on 4h BTC.

Neutral grid: start flat; place BUY limits below price and SELL limits above (on a perp,
selling above opens shorts) -> inventory can go long OR short. Each filled buy places a
sell one step up; each filled sell places a buy one step down -> banks ~one step per cycle.
Dynamic movement: when price exits the [low,high] band, CLOSE inventory at market and
REBUILD the grid around the new price (the grid trails price so it's never fully one-sided
forever). Half-width = pct of price; levels = grid lines.

HONESTY: fills on REAL intrabar path (up bar open->low->high->close; down bar reverse);
fee+slippage every fill incl. re-center; equity = cash + inv*price (inv +/-); IS/OOS 60/40;
benchmark = buy&hold. Data: Binance 4h.
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


def metrics(eq):
    e = pd.Series(eq)
    if len(e) < 2:
        return 0.0, 0.0
    dd = e/e.cummax()-1
    return (e.iloc[-1]-1)*100, float(dd.min()*100)


class NGrid:
    def __init__(self, budget):
        self.cash = budget; self.inv = 0.0; self.orders = {}
        self.low = self.high = self.step = 0.0; self.lines = []; self.q = 0.0
    def equity(self, p): return self.cash + self.inv*p
    def build(self, p, half_pct, levels):
        self.low = p*(1-half_pct); self.high = p*(1+half_pct)
        self.step = (self.high-self.low)/levels
        self.lines = [self.low + i*self.step for i in range(levels+1)]
        self.q = self.equity(p)/(levels*p)            # size to ~full capital across band
        self.orders = {L: ("buy" if L < p else "sell") for L in self.lines if abs(L-p) > 1e-9}
    def recenter(self, p):
        # close inventory at market
        if self.inv > 0:
            self.cash += self.inv*p*(1-SLIP_PCT)*(1-FEE_PCT)
        elif self.inv < 0:
            self.cash += self.inv*p*(1+SLIP_PCT); self.cash -= abs(self.inv)*p*FEE_PCT
        self.inv = 0.0
    def _down(self, a, b):
        for L in self.lines:
            if b <= L < a and self.orders.get(L) == "buy":
                f = L*(1-SLIP_PCT); self.cash -= self.q*f*(1+FEE_PCT); self.inv += self.q
                self.orders[L] = None
                up = L+self.step
                if up <= self.high+1e-9:
                    self.orders[up] = "sell"
    def _up(self, a, b):
        for L in self.lines:
            if a < L <= b and self.orders.get(L) == "sell":
                f = L*(1+SLIP_PCT); self.cash += self.q*f*(1-FEE_PCT); self.inv -= self.q
                self.orders[L] = None
                dn = L-self.step
                if dn >= self.low-1e-9:
                    self.orders[dn] = "buy"
    def bar(self, o, h, l, c):
        if c >= o:
            self._down(o, l); self._up(l, h)
        else:
            self._up(o, h); self._down(h, l)


def run(df, *, half_pct, levels, recenter_mult=1.0):
    g = NGrid(1.0)
    g.build(float(df.iloc[0]["open"]), half_pct, levels)
    eq = []; recenters = 0
    for i in range(len(df)):
        o, h, l, c = (float(df.iloc[i][k]) for k in ("open", "high", "low", "close"))
        g.bar(o, h, l, c)
        # dynamic movement: price left the band -> re-center around new price
        if c > g.high*recenter_mult or c < g.low/recenter_mult:
            g.recenter(c); g.build(c, half_pct, levels); recenters += 1
        eq.append(g.equity(c))
    g.recenter(float(df.iloc[-1]["close"]))
    net, dd = metrics(eq)
    return {"net": net, "dd": dd, "recenters": recenters}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--start", default="2019-01-01"); a = ap.parse_args()
    df = fetch_binance(a.symbol, "4h", int(pd.Timestamp(a.start).timestamp()*1000))
    if len(df) < 500:
        print("insufficient data"); return
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).days
    split = int(len(df)*0.6); oos = df.iloc[split:].reset_index(drop=True)
    bh = (float(df.iloc[-1]['close'])/float(df.iloc[0]['close'])-1)*100
    bho = (float(oos.iloc[-1]['close'])/float(oos.iloc[0]['close'])-1)*100
    print(f"{a.symbol} 4h neutral trailing grid  bars={len(df)} (~{days/365:.1f}y) {df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}")
    print(f"buy&hold FULL {bh:.1f}% | OOS {bho:.1f}%\n")
    print(f"{'half_pct':>8} {'levels':>6} | {'FULL net%':>9} {'DD%':>7} {'recent':>7} | {'OOS net%':>8} {'OOS DD%':>8}")
    for hp in (0.05, 0.10, 0.20):
        for lv in (10, 20):
            rf = run(df, half_pct=hp, levels=lv); ro = run(oos, half_pct=hp, levels=lv)
            print(f"{hp:8.2f} {lv:6d} | {rf['net']:9.1f} {rf['dd']:7.1f} {rf['recenters']:7d} | {ro['net']:8.1f} {ro['dd']:8.1f}")


if __name__ == "__main__":
    main()
