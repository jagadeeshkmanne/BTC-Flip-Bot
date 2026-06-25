#!/usr/bin/env python3
"""backtest_grid_regime.py — Regime-gated grid vs naked grid vs buy&hold vs trend.

THE IDEA: a grid only earns in a range and bleeds in a trend. So gate it:
  - ADX < adx_on  -> RANGE: turn the grid ON (range from prior window, re-center on drift)
  - ADX > adx_off -> TREND: liquidate inventory, sit FLAT (let a trend bot handle that)
  (hysteresis: on/off thresholds differ to avoid flip-flopping)

Grid model = OpenTrader arithmetic grid (low/high/levels/qty), buy below / sell above,
filled buy -> sell one step up, filled sell -> buy one step down.

HONESTY: ADX/range from CLOSED bars only (no lookahead); fills on REAL intrabar path
(up bar open->low->high->close, down bar open->high->low->close); fee+slippage every
fill; equity = cash + inventory*close each bar.
"""
from __future__ import annotations

import argparse
import time

import pandas as pd
import requests

PAIR = "BTCUSDT"
BYBIT_BASE = "https://api.bybit.com"
FEE_PCT = 0.00055
SLIP_PCT = 0.0005


def fetch_bybit(symbol: str, interval: str, bars: int) -> pd.DataFrame:
    rows: list[list[str]] = []
    end_ms = None
    while len(rows) < bars:
        params = {"category": "linear", "symbol": symbol, "interval": interval,
                  "limit": min(1000, bars - len(rows))}
        if end_ms is not None:
            params["end"] = end_ms
        r = requests.get(f"{BYBIT_BASE}/v5/market/kline", params=params, timeout=20)
        r.raise_for_status()
        body = r.json()
        if body.get("retCode") != 0:
            raise RuntimeError(f"Bybit {body.get('retMsg')}")
        batch = body.get("result", {}).get("list", [])
        if not batch:
            break
        rows.extend(batch)
        end_ms = min(int(x[0]) for x in batch) - 1
        time.sleep(0.05)
    rows = sorted({int(x[0]): x for x in rows}.values(), key=lambda x: int(x[0]))
    return pd.DataFrame({
        "timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
        "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
        "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows],
    }).reset_index(drop=True)


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def adx(df, n=14):
    up = df["high"].diff(); dn = -df["low"].diff()
    pdm = ((up > dn) & (up > 0)) * up.clip(lower=0)
    ndm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1)
    a = ema(tr, n)
    pdi = 100*ema(pdm, n)/a; ndi = 100*ema(ndm, n)/a
    dx = 100*(pdi-ndi).abs()/(pdi+ndi).replace(0, 1e-9)
    return ema(dx, n)


def metrics(curve):
    e = pd.Series(curve)
    dd = e/e.cummax() - 1.0
    return (e.iloc[-1]-1.0)*100.0, float(dd.min()*100.0)


def buy_hold(df):
    p0, p1 = float(df.iloc[0]["close"]), float(df.iloc[-1]["close"])
    eq = [float(df.iloc[i]["close"])/p0 for i in range(len(df))]
    net, ddv = metrics(eq)
    return net, ddv


def trend_follow(df):
    """EMA13>EMA20 and close>EMA200 -> long, else flat (the live-bot rule, unlevered)."""
    c = df["close"]
    ef, es, eg = ema(c, 13), ema(c, 20), ema(c, 200)
    long = (ef > es) & (c > eg)
    cash, qty, cost = 1.0, 0.0, 0.0
    eq = []
    for i in range(200, len(df)-1):
        px = float(df.iloc[i+1]["open"])
        if qty == 0 and bool(long.iloc[i]):
            fill = px*(1+SLIP_PCT); fee = cash*FEE_PCT
            qty = (cash-fee)/fill; cost = cash; cash = 0.0
        elif qty > 0 and not bool(long.iloc[i]):
            fill = px*(1-SLIP_PCT); proc = qty*fill; proc -= proc*FEE_PCT
            cash = proc; qty = 0.0
        eq.append(cash + qty*float(df.iloc[i+1]["close"]))
    if qty > 0:
        eq.append(cash + qty*float(df.iloc[-1]["close"]))
    return metrics(eq)


class Grid:
    """Stateful arithmetic grid with intrabar path fills."""
    def __init__(self):
        self.active = False
        self.cash = 1.0
        self.inv = 0.0
        self.orders = {}
        self.low = self.high = self.step = 0.0
        self.lines = []
        self.fills = 0
        self.realized = 0.0
        self.q = 0.0

    def equity(self, px):
        return self.cash + self.inv*px

    def establish(self, p0, low, high, levels):
        self.low, self.high = low, high
        self.step = (high-low)/levels
        self.lines = [low + i*self.step for i in range(levels+1)]
        budget = self.cash                       # we are flat here
        self.q = budget/(levels*p0)
        above = [L for L in self.lines if L > p0]
        self.inv = self.q*len(above)
        fill = p0*(1+SLIP_PCT)
        self.cash -= self.inv*fill*(1+FEE_PCT)
        self.orders = {L: ("sell" if L > p0 else "buy") for L in self.lines if abs(L-p0) > 1e-9}
        self.active = True

    def liquidate(self, px):
        fill = px*(1-SLIP_PCT)
        self.cash += self.inv*fill*(1-FEE_PCT)
        self.inv = 0.0
        self.orders = {}
        self.active = False

    def _down(self, a, b):
        for L in self.lines:
            if b <= L < a and self.orders.get(L) == "buy":
                fpx = L*(1-SLIP_PCT); cost = self.q*fpx
                self.cash -= cost*(1+FEE_PCT); self.inv += self.q
                self.orders[L] = None
                up = L+self.step
                if up <= self.high+1e-9:
                    self.orders[up] = "sell"
                self.fills += 1

    def _up(self, a, b):
        for L in self.lines:
            if a < L <= b and self.orders.get(L) == "sell":
                fpx = L*(1+SLIP_PCT); proc = self.q*fpx
                self.cash += proc*(1-FEE_PCT); self.inv -= self.q
                self.orders[L] = None
                dn = L-self.step
                if dn >= self.low-1e-9:
                    self.orders[dn] = "buy"
                self.realized += self.q*self.step; self.fills += 1

    def step_bar(self, o, h, l, c):
        if c >= o:
            self._down(o, l); self._up(l, h)
        else:
            self._up(o, h); self._down(h, l)


def regime_grid(df, *, adx_on, adx_off, range_mult, levels, win):
    a = adx(df, 14)
    pl = df["low"].rolling(win).min().shift(1)
    ph = df["high"].rolling(win).max().shift(1)
    g = Grid()
    eq = []
    ts = []
    active_bars = 0
    liquidations = 0
    start = max(win, 60)
    for i in range(start, len(df)):
        row = df.iloc[i]
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        adx_i = float(a.iloc[i-1]) if pd.notna(a.iloc[i-1]) else 100.0   # use last CLOSED bar

        if g.active:
            # trend kicked in -> liquidate & go flat
            if adx_i > adx_off:
                g.liquidate(o); liquidations += 1
            # price drifted out of the box but still ranging -> re-center
            elif c < g.low or c > g.high:
                g.liquidate(o); liquidations += 1
                if adx_i < adx_off and pd.notna(pl.iloc[i]):
                    half = (float(ph.iloc[i])-float(pl.iloc[i]))/2*range_mult
                    if half > 0:
                        g.establish(o, max(o-half, o*0.4), o+half, levels)
        else:
            if adx_i < adx_on and pd.notna(pl.iloc[i]):
                half = (float(ph.iloc[i])-float(pl.iloc[i]))/2*range_mult
                if half > 0:
                    g.establish(o, max(o-half, o*0.4), o+half, levels)

        if g.active:
            g.step_bar(o, h, l, c); active_bars += 1
        eq.append(g.equity(c))
        ts.append(row["timestamp"])

    net, ddv = metrics(eq)
    return {"net": net, "dd": ddv, "fills": g.fills, "realized": g.realized*100,
            "active_pct": active_bars/max(len(eq), 1)*100, "liq": liquidations,
            "eq": eq, "ts": ts}


def monthly_income(ts, eq):
    """Resample equity to month-end, return (rows, mean%, pos_share, ann%)."""
    s = pd.Series(eq, index=pd.to_datetime(ts))
    m = s.resample("ME").last()
    rets = m.pct_change().dropna()*100
    if len(rets) == 0:
        return [], 0.0, 0.0, 0.0
    rows = [(idx.strftime("%Y-%m"), float(v)) for idx, v in rets.items()]
    mean = float(rets.mean())
    pos = float((rets > 0).mean()*100)
    years = (s.index[-1]-s.index[0]).days/365.25
    ann = ((eq[-1]/eq[0])**(1/years)-1)*100 if years > 0 else 0.0
    return rows, mean, pos, ann


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbol", default=PAIR)
    p.add_argument("--bars", type=int, default=6000)
    p.add_argument("--interval", default="240")
    args = p.parse_args()

    df = fetch_bybit(args.symbol, args.interval, args.bars)
    win = 6*30 if args.interval == "240" else (24*30 if args.interval == "60" else 180)
    days = (df.timestamp.iloc[-1]-df.timestamp.iloc[0]).total_seconds()/86400
    print(f"{args.symbol} {args.interval} bars={len(df)} (~{days:.0f}d) "
          f"{df.timestamp.iloc[0]} -> {df.timestamp.iloc[-1]}")

    split = int(len(df)*0.6)
    is_df = df.iloc[:split].reset_index(drop=True)
    oos_df = df.iloc[split:].reset_index(drop=True)
    print(f"IS: {is_df.timestamp.iloc[0]} -> {is_df.timestamp.iloc[-1]} ({len(is_df)} bars)")
    print(f"OOS: {oos_df.timestamp.iloc[0]} -> {oos_df.timestamp.iloc[-1]} ({len(oos_df)} bars)")

    # ---- optimize on IS ----
    print("\nIN-SAMPLE sweep (pick best by return/DD ratio):")
    print("adx_on,adx_off,rm,lv,net,dd,active%,liq")
    grid = []
    for adx_on in (18, 22, 25):
        for adx_off in (25, 30):
            if adx_off <= adx_on:
                continue
            for rm in (1.0, 1.5):
                for lv in (10, 20):
                    r = regime_grid(is_df, adx_on=adx_on, adx_off=adx_off,
                                    range_mult=rm, levels=lv, win=win)
                    score = r["net"]/abs(r["dd"]) if r["dd"] < 0 else r["net"]
                    grid.append((score, r, (adx_on, adx_off, rm, lv)))
                    print(f"{adx_on},{adx_off},{rm},{lv},{r['net']:.2f},{r['dd']:.2f},"
                          f"{r['active_pct']:.0f},{r['liq']}")
    grid.sort(key=lambda x: x[0], reverse=True)
    _, ris, cfg = grid[0]
    print(f"\nBEST IS config (by net/DD): adx_on={cfg[0]} adx_off={cfg[1]} rm={cfg[2]} lv={cfg[3]}")
    print(f"  IS: net={ris['net']:.2f}% dd={ris['dd']:.2f}% active={ris['active_pct']:.0f}%")

    # ---- validate that exact config on OOS ----
    roos = regime_grid(oos_df, adx_on=cfg[0], adx_off=cfg[1], range_mult=cfg[2], levels=cfg[3], win=win)
    bh = buy_hold(oos_df); tf = trend_follow(oos_df)
    print(f"\nOUT-OF-SAMPLE ({oos_df.timestamp.iloc[0].date()} -> {oos_df.timestamp.iloc[-1].date()}):")
    print(f"  regime_grid : net={roos['net']:.2f}% dd={roos['dd']:.2f}% "
          f"active={roos['active_pct']:.0f}% liq={roos['liq']} fills={roos['fills']}")
    print(f"  buy_hold    : net={bh[0]:.2f}% dd={bh[1]:.2f}%")
    print(f"  trend_follow: net={tf[0]:.2f}% dd={tf[1]:.2f}%")

    rows, mean, pos, ann = monthly_income(roos["ts"], roos["eq"])
    print(f"\nMONTHLY INCOME (OOS): avg={mean:+.2f}%/mo  positive_months={pos:.0f}%  annualized={ann:+.1f}%")
    for ym, v in rows:
        bar = "#" * int(abs(v)*4)
        print(f"  {ym}: {v:+6.2f}%  {bar}")

    verdict = "HOLDS UP" if (roos["net"] > 0 and mean > 0) else "FALLS APART"
    print(f"\nVERDICT: {verdict}")


if __name__ == "__main__":
    main()
