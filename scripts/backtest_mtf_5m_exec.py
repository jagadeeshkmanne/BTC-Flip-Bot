#!/usr/bin/env python3
"""backtest_mtf_5m_exec.py — 15m BIAS + 5m EXECUTION (MACD / EMA / RSI), honest.

The user's idea: get the bias right on 15m (EMA trend + MACD), then execute entries on 5m
(RSI pullback / EMA cross / MACD cross) only in the bias direction; manage with an ATR
trailing stop. Tests it across triggers/exits/direction and ranks by OOS risk-adjusted return.

MTF alignment is lookahead-SAFE: the 15m bias applied to a 5m bar is from the most recent
15m bar that has fully CLOSED (bias series re-indexed by close-time, forward-filled to 5m).

15m BIAS options:
  trend     : EMA50>EMA200 -> long bias ; else short bias
  trend_macd: close>EMA200 AND MACD_hist>0 -> long ; close<EMA200 AND hist<0 -> short ; else FLAT

5m TRIGGER (must agree with bias to enter):
  rsi   : RSI(14) crosses up through 35 (long) / down through 65 (short)  [oscillator pullback]
  ema   : EMA9 crosses EMA21 in the bias direction
  macd  : MACD line crosses its signal in the bias direction

EXIT:
  atr   : ATR(14) trailing stop (intrabar) OR bias flips
  flip  : exit only when 15m bias flips

Honesty: 5m signal on CLOSED bar, fill NEXT 5m open, fee 0.055%/side + 0.05% slip, ATR stop
on real intrabar high/low, 60/40 OOS. Data: Binance 5m since 2023 (cached), 15m resampled from it.
"""
from __future__ import annotations
import os, time
import pandas as pd
import numpy as np
import requests

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data/cache/BTCUSDT_5m_binance.csv")
HOSTS = ["https://api.binance.com", "https://data-api.binance.vision"]


def fetch_5m(start="2023-01-01"):
    if os.path.exists(CACHE):
        df = pd.read_csv(CACHE, parse_dates=["timestamp"])
        if (pd.Timestamp.utcnow().tz_localize(None) - df["timestamp"].iloc[-1]) < pd.Timedelta("2D"):
            return df
    cur = int(pd.Timestamp(start).timestamp() * 1000); rows, url_ok = [], None
    while True:
        params = {"symbol": "BTCUSDT", "interval": "5m", "startTime": cur, "limit": 1000}
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
        cur = data[-1][0] + 1; time.sleep(0.08)
    seen = {int(x[0]): x for x in rows}; rows = [seen[k] for k in sorted(seen)]
    df = pd.DataFrame({"timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
                       "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
                       "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows]})
    os.makedirs(os.path.dirname(CACHE), exist_ok=True); df.to_csv(CACHE, index=False)
    return df


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def rsi(s, n=14):
    d = s.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    rs = ema(up, n) / ema(dn, n).replace(0, 1e-9)
    return 100 - 100 / (1 + rs)


def macd(s, f=12, sl=26, sig=9):
    line = ema(s, f) - ema(s, sl)
    sg = ema(line, sig)
    return line, sg, line - sg


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return ema(tr, n)


def build_bias(df5, mode):
    g = df5.set_index("timestamp").resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    c = g["close"]
    if mode == "trend":
        bias = np.where(ema(c, 50) > ema(c, 200), 1, -1)
    else:
        _, _, hist = macd(c)
        up = (c > ema(c, 200)) & (hist > 0)
        dn = (c < ema(c, 200)) & (hist < 0)
        bias = np.where(up, 1, np.where(dn, -1, 0))
    b = pd.Series(bias, index=g.index + pd.Timedelta("15min"))   # valid only AFTER the bar closes
    return b.reindex(df5["timestamp"], method="ffill").fillna(0).values


def run(df5, bias, trigger, exit_mode, direction, atr_mult=2.5):
    c = df5["close"]
    r = rsi(c, 14).values
    e9, e21 = ema(c, 9).values, ema(c, 21).values
    ml, sg, _ = macd(c)
    ml, sg = ml.values, sg.values
    o = df5["open"].values; h = df5["high"].values; l = df5["low"].values; cl = c.values
    a = atr(df5, 14).values
    n = len(df5)
    bal = 1.0; side = 0; entry = 0.0; trail = 0.0
    eq = np.ones(n); held = np.zeros(n); trades = []
    start = 250

    def long_trig(i):
        if trigger == "rsi":  return r[i - 1] < 35 <= r[i]
        if trigger == "ema":  return e9[i] > e21[i] and e9[i - 1] <= e21[i - 1]
        return ml[i] > sg[i] and ml[i - 1] <= sg[i - 1]

    def short_trig(i):
        if trigger == "rsi":  return r[i - 1] > 65 >= r[i]
        if trigger == "ema":  return e9[i] < e21[i] and e9[i - 1] >= e21[i - 1]
        return ml[i] < sg[i] and ml[i - 1] >= sg[i - 1]

    for i in range(start, n - 1):
        oN, hN, lN, cN, aN = o[i + 1], h[i + 1], l[i + 1], cl[i + 1], a[i]
        b = bias[i]
        # manage open position
        if side != 0:
            flip = (b != side)
            if exit_mode == "atr":
                if side == 1:
                    if lN <= trail:
                        fpx = trail * (1 - SLIP_PCT); bal *= (fpx / entry) * (1 - 2 * FEE_PCT)
                        trades.append(fpx / entry - 1); side = 0
                    else:
                        trail = max(trail, cN - atr_mult * aN)
                elif side == -1:
                    if hN >= trail:
                        fpx = trail * (1 + SLIP_PCT); bal *= ((2 * entry - fpx) / entry) * (1 - 2 * FEE_PCT)
                        trades.append((entry - fpx) / entry); side = 0
            if side != 0 and flip:                       # bias flip always closes
                dirn = side; fpx = oN * (1 - SLIP_PCT * dirn)
                bal *= ((fpx / entry) if side == 1 else ((2 * entry - fpx) / entry)) * (1 - 2 * FEE_PCT)
                trades.append((fpx / entry - 1) if side == 1 else (entry - fpx) / entry); side = 0
        # entries (only with bias, on a 5m trigger)
        if side == 0:
            if b == 1 and long_trig(i):
                side = 1; entry = oN * (1 + SLIP_PCT); trail = oN - atr_mult * aN
            elif b == -1 and direction == "ls" and short_trig(i):
                side = -1; entry = oN * (1 - SLIP_PCT); trail = oN + atr_mult * aN
        # mark
        if side == 1:   eq[i + 1] = bal * cN / entry
        elif side == -1: eq[i + 1] = bal * (2 * entry - cN) / entry
        else:           eq[i + 1] = bal
        held[i + 1] = side
    s = pd.Series(eq, index=pd.to_datetime(df5["timestamp"]))
    return s.iloc[start:], len(trades), trades


def metrics(eq):
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1
    dd = (eq / eq.cummax() - 1).min()
    return cagr, dd, (cagr / abs(dd) if dd < -1e-9 else 0.0)


def main():
    df5 = fetch_5m()
    idx = pd.to_datetime(df5["timestamp"])
    cut_ts = idx.iloc[int(len(df5) * 0.6)]
    span = f"{idx.iloc[0]:%Y-%m-%d}->{idx.iloc[-1]:%Y-%m-%d}"
    print("=" * 104)
    print(f"BTC — 15m BIAS + 5m EXECUTION (MACD/EMA/RSI)   ({len(df5)} 5m bars, {span}, OOS {cut_ts:%Y-%m-%d})")
    print("=" * 104)
    print(f"  {'bias':<11}{'trigger':<7}{'exit':<6}{'dir':<4}{'CAGR':>8}{'DD':>6}{'r/DD':>6}{'trades':>8}   {'oCAGR':>7}{'oDD':>6}{'or/DD':>6}")

    biases = {}
    rows = []
    for bias_mode in ("trend", "trend_macd"):
        biases[bias_mode] = build_bias(df5, bias_mode)
        for trig in ("rsi", "ema", "macd"):
            for ex in ("atr", "flip"):
                for direction in ("lf", "ls"):
                    eq, nt, _ = run(df5, biases[bias_mode], trig, ex, direction)
                    eo = eq[eq.index >= cut_ts]
                    m, mo = metrics(eq), metrics(eo)
                    rows.append((bias_mode, trig, ex, direction, m, mo, nt))

    rows.sort(key=lambda x: x[5][2], reverse=True)   # OOS ret/DD
    for bm, tg, ex, d, m, mo, nt in rows:
        print(f"  {bm:<11}{tg:<7}{ex:<6}{d:<4}{m[0]*100:>7.0f}%{m[1]*100:>5.0f}%{m[2]:>6.2f}{nt:>8}   "
              f"{mo[0]*100:>6.0f}%{mo[1]*100:>5.0f}%{mo[2]:>6.2f}")

    bh = df5["close"] / df5["close"].iloc[0]; bh.index = idx
    bm = metrics(bh); bo = metrics(bh[bh.index >= cut_ts])
    print(f"\n  buy&hold full ret/DD {bm[2]:.2f} (CAGR {bm[0]*100:.0f}%, DD {bm[1]*100:.0f}%) | OOS ret/DD {bo[2]:.2f}")


if __name__ == "__main__":
    main()
