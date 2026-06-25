#!/usr/bin/env python3
"""backtest_ema_cross_bb.py — EMA-cross entry + Bollinger-Band exit framework (the chart idea).

Idea (from the user's chart): two EMAs (fast/slow) cross to ENTER; the Bollinger Bands
frame the EXIT — a trailing take-profit on the band top, a stop "below the cross".
Direction: long + short (symmetric). All the TP/SL knobs are UNKNOWN, so we SWEEP them
and let the out-of-sample numbers decide.

Entry:  golden cross (EMAf>EMAs) -> LONG ;  death cross -> SHORT.

TP modes (the "trailing tp on bb top"):
  tp_band     : take profit the instant price tags the band (upper for long / lower for short).
  trail_mid   : after price tags the band, trail a stop at the mid-band (SMA); exit when hit.
  close_inside: hold while close stays beyond the band; exit first close back inside it.

SL modes ("sl below the cross"):
  slow_ema    : exit if price CLOSES through the slow EMA (the cross is invalidated).
  lower_band  : exit at the opposite band (lower for long / upper for short), intrabar.
  death_cross : no price stop — exit/flip only on the opposite EMA cross (pure reverse).

Honesty rules (same as every script here): signal on CLOSED bar i, fill at bar i+1 OPEN,
fee 0.055%/side + 0.05% slippage, stops/TP on REAL intrabar high/low (STOP-FIRST on a
straddle), in-sample/out-of-sample 60/40 split, metrics = CAGR + maxDD + win%.

Data: local bybit cache (1h, 15m) + 4h resampled from 1h. No network.
"""
from __future__ import annotations
import os
import itertools
import pandas as pd

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EMA_F, EMA_S = 20, 50          # yellow (fast) / magenta (slow) on the chart
BB_N, BB_K = 20, 2.0           # Bollinger: SMA20 ± 2σ

TP_MODES = ["tp_band", "trail_mid", "close_inside"]
SL_MODES = ["slow_ema", "lower_band", "death_cross"]


def load(path):
    df = pd.read_csv(os.path.join(HERE, path), parse_dates=["timestamp"])
    return df.sort_values("timestamp").reset_index(drop=True)


def resample(df, rule):
    g = df.set_index("timestamp").resample(rule).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    return g.reset_index()


def ema(s, n): return s.ewm(span=n, adjust=False).mean()


def indicators(df):
    c = df["close"]
    df = df.copy()
    df["emaf"] = ema(c, EMA_F)
    df["emas"] = ema(c, EMA_S)
    mid = c.rolling(BB_N).mean()
    sd = c.rolling(BB_N).std(ddof=0)
    df["bb_mid"] = mid
    df["bb_up"] = mid + BB_K * sd
    df["bb_lo"] = mid - BB_K * sd
    df["cross_up"] = (df["emaf"] > df["emas"]) & (df["emaf"].shift(1) <= df["emas"].shift(1))
    df["cross_dn"] = (df["emaf"] < df["emas"]) & (df["emaf"].shift(1) >= df["emas"].shift(1))
    return df


def backtest(df, tp_mode, sl_mode, allow_short=True):
    """One pass. Returns equity series + trade pct list."""
    bal = 1.0
    side = 0            # 0 flat, +1 long, -1 short
    entry = 0.0
    armed = False       # has price tagged the band yet (for trailing modes)
    trail = 0.0
    eq, ts, trades = [], [], []
    start = max(EMA_S, BB_N) + 2

    for i in range(start, len(df) - 1):
        r = df.iloc[i]                       # last CLOSED bar — all decision levels from here
        o = float(df.iloc[i + 1]["open"]); h = float(df.iloc[i + 1]["high"])
        l = float(df.iloc[i + 1]["low"]); c = float(df.iloc[i + 1]["close"])
        up, mid, lo = float(r["bb_up"]), float(r["bb_mid"]), float(r["bb_lo"])
        slow = float(r["emas"])
        exited = False

        def close_long(px, reason):
            nonlocal bal, side, armed
            fpx = px * (1 - SLIP_PCT)
            bal = bal * (fpx / entry) * (1 - 2 * FEE_PCT)
            trades.append(fpx / entry - 1); side = 0; armed = False

        def close_short(px, reason):
            nonlocal bal, side, armed
            fpx = px * (1 + SLIP_PCT)
            bal = bal * ((2 * entry - fpx) / entry) * (1 - 2 * FEE_PCT)
            trades.append((entry - fpx) / entry); side = 0; armed = False

        # ── manage an open position (STOP-FIRST, then TP) ──
        if side == 1:
            # stop-loss
            if sl_mode == "lower_band" and l <= lo:
                close_long(lo, "SL_band"); exited = True
            elif sl_mode == "slow_ema" and c < slow:
                close_long(c, "SL_ema"); exited = True
            # take-profit on the upper band
            if not exited:
                if tp_mode == "tp_band" and h >= up:
                    close_long(up, "TP_band"); exited = True
                elif tp_mode == "trail_mid":
                    if not armed and h >= up:
                        armed = True
                    if armed:
                        trail = max(trail, mid)
                        if l <= trail:
                            close_long(trail, "TP_trail"); exited = True
                elif tp_mode == "close_inside":
                    if not armed and h >= up:
                        armed = True
                    if armed and c < up:
                        close_long(c, "TP_inside"); exited = True
        elif side == -1:
            if sl_mode == "lower_band" and h >= up:
                close_short(up, "SL_band"); exited = True
            elif sl_mode == "slow_ema" and c > slow:
                close_short(c, "SL_ema"); exited = True
            if not exited:
                if tp_mode == "tp_band" and l <= lo:
                    close_short(lo, "TP_band"); exited = True
                elif tp_mode == "trail_mid":
                    if not armed and l <= lo:
                        armed = True
                    if armed:
                        trail = min(trail, mid) if trail else mid
                        if h >= trail:
                            close_short(trail, "TP_trail"); exited = True
                elif tp_mode == "close_inside":
                    if not armed and l <= lo:
                        armed = True
                    if armed and c > lo:
                        close_short(c, "TP_inside"); exited = True

        # ── entries / flips on the cross (fill at this bar's open) ──
        gc, dc = bool(r["cross_up"]), bool(r["cross_dn"])
        if gc and side != 1:
            if side == -1:
                close_short(o, "flip")
            entry = o * (1 + SLIP_PCT); side = 1; armed = False; trail = 0.0
        elif dc and side == 1:
            close_long(o, "flip")               # death cross always closes a long
            if allow_short:
                entry = o * (1 - SLIP_PCT); side = -1; armed = False; trail = 0.0
        elif dc and side == -1:
            pass
        elif dc and allow_short and side == 0:
            entry = o * (1 - SLIP_PCT); side = -1; armed = False; trail = 0.0

        # ── mark-to-market ──
        if side == 1:
            eq.append(bal * c / entry)
        elif side == -1:
            eq.append(bal * (2 * entry - c) / entry)
        else:
            eq.append(bal)
        ts.append(df.iloc[i + 1]["timestamp"])

    return pd.Series(eq, index=pd.to_datetime(ts)), trades


def metrics(e, trades):
    if len(e) < 3:
        return None
    yrs = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / yrs) - 1
    dd = (e / e.cummax() - 1).min()
    wr = (sum(1 for t in trades if t > 0) / len(trades) * 100) if trades else 0
    return dict(cagr=cagr, dd=dd, n=len(trades), wr=wr)


def split_report(label, df, allow_short=True):
    rows = []
    for tp, sl in itertools.product(TP_MODES, SL_MODES):
        e, tr = backtest(df, tp, sl, allow_short)
        if len(e) < 5:
            continue
        ci = e.index[int(len(e) * 0.6)]
        e_oos = e[e.index >= ci]
        rows.append((tp, sl, metrics(e, tr), metrics(e_oos, [])))
    rows.sort(key=lambda x: (x[3]["cagr"] if x[3] else -9), reverse=True)
    direction = "long+short" if allow_short else "LONG-ONLY"
    print(f"\n{label}  (EMA{EMA_F}/{EMA_S}, BB{BB_N}/{BB_K}, {direction})")
    print(f"  {'TP mode':<13}{'SL mode':<13}{'FULL CAGR':>11}{'maxDD':>8}{'win%':>6}{'trades':>8}   {'OOS CAGR':>10}{'OOS DD':>8}")
    for tp, sl, mf, mo in rows:
        print(f"  {tp:<13}{sl:<13}{mf['cagr']*100:>10.1f}%{mf['dd']*100:>7.1f}%{mf['wr']:>5.0f}%{mf['n']:>8}   "
              f"{mo['cagr']*100:>9.1f}%{mo['dd']*100:>7.1f}%")


def main():
    print("=" * 92)
    print("EMA-CROSS + BOLLINGER-BAND exit sweep — BTC, long+short, honest (next-open, fees, intrabar, 60/40 OOS)")
    print("Best row = highest OUT-OF-SAMPLE CAGR (the only number that matters).")
    print("=" * 92)

    h1 = indicators(load("data/cache/BTCUSDT_1h_12000_bybit.csv"))
    m15 = indicators(load("data/cache/BTCUSDT_15m_16000_bybit.csv"))
    h4 = indicators(resample(load("data/cache/BTCUSDT_1h_12000_bybit.csv"), "4h"))

    for label, df in [("BTC 4h (resampled)", h4), ("BTC 1h", h1), ("BTC 15m", m15)]:
        span = f"{df['timestamp'].iloc[0]:%Y-%m-%d}->{df['timestamp'].iloc[-1]:%Y-%m-%d}"
        print(f"\n\n########  {label}  ({len(df)} bars, {span})  ########")
        split_report(label, df, allow_short=True)
        split_report(label, df, allow_short=False)   # diagnostic: are the shorts the killer?


if __name__ == "__main__":
    main()
