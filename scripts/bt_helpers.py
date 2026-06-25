#!/usr/bin/env python3
"""bt_helpers.py — shared HONEST backtest engine for the multi-strategy agent search.

Every agent imports this so fills/fees/metrics are identical (no spurious winners from
inconsistent implementations). Honesty rules baked in:
  - signals on CLOSED bars, fills at NEXT bar OPEN
  - fee 0.055%/side + 0.05% slippage/side
  - intrabar TP/SL on REAL high/low (STOP-FIRST on a straddle)
  - in-sample/out-of-sample split + a walk-forward helper

Data: cached Binance CSVs. 1h native for BTC/ETH/BNB/SOL; resample up for 2h/4h/6h/12h/1d.
5m/15m only for BTC (from the 5m cache).
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
COST = FEE_PCT + SLIP_PCT
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data/cache")

_1H = {"BTCUSDT": "BTCUSDT_1h_binance_full.csv", "ETHUSDT": "ETHUSDT_1h_binance.csv",
       "BNBUSDT": "BNBUSDT_1h_binance.csv", "SOLUSDT": "SOLUSDT_1h_binance.csv"}
_RULE = {"15m": "15min", "30m": "30min", "1h": "1h", "2h": "2h", "4h": "4h",
         "6h": "6h", "12h": "12h", "1d": "1D"}


def load(coin: str, tf: str) -> pd.DataFrame:
    """OHLC DataFrame (timestamp/open/high/low/close) for coin+timeframe. Honest, oldest-first."""
    if tf in ("5m", "15m"):
        if coin != "BTCUSDT":
            raise ValueError(f"{tf} only available for BTCUSDT")
        df = pd.read_csv(os.path.join(CACHE, "BTCUSDT_5m_binance.csv"), parse_dates=["timestamp"])
        if tf == "15m":
            df = df.set_index("timestamp").resample("15min").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
        return df
    df = pd.read_csv(os.path.join(CACHE, _1H[coin]), parse_dates=["timestamp"])
    if tf != "1h":
        df = df.set_index("timestamp").resample(_RULE[tf]).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    return df.reset_index(drop=True)


# ---- indicators ----
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()


def rsi(s, n=14):
    d = s.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    rsx = ema(up, n) / ema(dn, n).replace(0, 1e-9)
    return 100 - 100 / (1 + rsx)


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return ema(tr, n)


def bbands(s, n=20, k=2.0):
    m = s.rolling(n).mean(); sd = s.rolling(n).std(ddof=0)
    return m - k * sd, m, m + k * sd


def adx(df, n=14):
    up = df["high"].diff(); dn = -df["low"].diff()
    pdm = ((up > dn) & (up > 0)) * up.clip(lower=0)
    ndm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    a = atr(df, n)
    pdi = 100 * ema(pdm, n) / a; ndi = 100 * ema(ndm, n) / a
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, 1e-9)
    return ema(dx, n)


# ---- backtest cores ----
def backtest_signal(df, pos):
    """Vectorized: pos in {-1,0,1} decided at close[t], held open[t+1]->open[t+2]. Fees on turnover.
    Returns (equity Series indexed by timestamp, n_trades)."""
    pos = pd.Series(np.asarray(pos, float), index=df.index)
    held = pos.shift(1).fillna(0)
    oo = (df["open"].shift(-1) / df["open"] - 1).fillna(0)
    turn = held.diff().abs().fillna(held.abs())
    r = held * oo - turn * COST
    eq = (1 + r).cumprod(); eq.index = pd.to_datetime(df["timestamp"])
    return eq, int((turn > 0).sum())


def backtest_tpsl(df, long_in, short_in=None, tp=None, sl=None, max_hold=None, trail_atr=None, atr_n=14):
    """Stateful one-position TP/SL engine. long_in/short_in: boolean arrays (signal on bar i,
    fill next open). tp/sl as fractions (e.g. 0.01). Optional ATR trailing stop / time exit.
    Returns (equity Series, n_trades, win_rate, profit_factor)."""
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    li = np.asarray(long_in, bool)
    si = np.asarray(short_in, bool) if short_in is not None else np.zeros(len(df), bool)
    a = atr(df, atr_n).values if trail_atr else None
    n = len(df); bal = 1.0; side = 0; entry = tpx = slx = trail = 0.0; held_bars = 0
    eq = np.ones(n); trades = []

    def close(px, dirn):
        nonlocal bal, side
        fpx = px * (1 - SLIP_PCT * dirn)
        r = (fpx / entry - 1) if dirn == 1 else (entry - fpx) / entry
        bal *= (1 + r) * (1 - 2 * FEE_PCT); trades.append(r); side = 0

    start = max(atr_n + 2, 2)
    for i in range(start, n - 1):
        oN, hN, lN, cN = o[i + 1], h[i + 1], l[i + 1], c[i + 1]
        if side != 0:
            held_bars += 1
            dirn = side
            if dirn == 1:
                if sl is not None and lN <= slx: close(slx, 1)
                elif trail_atr and lN <= trail: close(trail, 1)
                elif tp is not None and hN >= tpx: close(tpx, 1)
                elif max_hold and held_bars >= max_hold: close(oN, 1)
                elif trail_atr: trail = max(trail, cN - trail_atr * a[i])
            else:
                if sl is not None and hN >= slx: close(slx, -1)
                elif trail_atr and hN >= trail: close(trail, -1)
                elif tp is not None and lN <= tpx: close(tpx, -1)
                elif max_hold and held_bars >= max_hold: close(oN, -1)
                elif trail_atr: trail = min(trail, cN + trail_atr * a[i])
        if side == 0:
            if li[i]:
                side = 1; entry = oN * (1 + SLIP_PCT); held_bars = 0
                tpx = entry * (1 + tp) if tp else None; slx = entry * (1 - sl) if sl else None
                trail = (oN - trail_atr * a[i]) if trail_atr else 0.0
            elif si[i]:
                side = -1; entry = oN * (1 - SLIP_PCT); held_bars = 0
                tpx = entry * (1 - tp) if tp else None; slx = entry * (1 + sl) if sl else None
                trail = (oN + trail_atr * a[i]) if trail_atr else 0.0
        eq[i + 1] = bal if side == 0 else (bal * cN / entry if side == 1 else bal * (2 * entry - cN) / entry)
    eqs = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[start:]
    tr = np.array(trades); w = tr[tr > 0]; loss = tr[tr <= 0]
    wr = len(w) / len(tr) * 100 if len(tr) else 0.0
    pf = (w.sum() / -loss.sum()) if len(loss) and loss.sum() < 0 else (float("inf") if len(w) else 0.0)
    return eqs, len(tr), wr, pf


# ---- metrics / validation ----
def metrics(eq):
    """(CAGR, maxDD, ret/DD) from an equity Series. Bars/yr inferred from index."""
    eq = eq.dropna()
    if len(eq) < 10 or eq.iloc[-1] <= 0:
        return (-1.0, -1.0, -1.0)
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return (cagr, dd, cagr / abs(dd) if dd < -1e-9 else 0.0)


def oos_split(eq, frac=0.6):
    cut = eq.index[int(len(eq) * frac)]
    return eq[eq.index < cut], eq[eq.index >= cut]


def buyhold(df):
    eq = df["close"] / df["close"].iloc[0]; eq.index = pd.to_datetime(df["timestamp"])
    return eq
