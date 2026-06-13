#!/usr/bin/env python3
"""bb_guru_mtf.py — honest test of the YouTube "10-year Bollinger" strategy
(2026-06-13, user request): HTF trend bias + 5m pullback to HIGH/LOW-based
Bollinger bands + hammer-candle confirmation, with-trend only.

Mechanized faithfully from the video:
  HTF bias (1h, resampled from 5m): SMA20(close) slope over 3 bars.
    rising -> long bias only; falling -> short bias only.
  5m BUY band:  EMA20(high) +/- 2*std20(high)  (video: "price base high, EMA")
  5m SELL band: EMA20(low)  +/- 2*std20(low)
  LONG signal:  bias up, bar LOW <= lower buy band, hammer confirm
                (close>open AND lower wick >= body).
  SHORT signal: bias down, bar HIGH >= upper sell band, inverted hammer
                (close<open AND upper wick >= body).
  Entry: next 5m bar OPEN +/- 0.02% slip. SL: signal bar wick -/+ 0.05% buffer.
  TP variants (pre-registered, video gives no exact exit): 1R, 2R,
  MID (close back across EMA20-basis), plus 12h time stop on all.
  Same-bar TP+SL -> SL (pessimistic). Fees 0.055%/side + slip. 1 position.
  (Simplification: video layers a 2nd WMA-basis band ~equal to the EMA one.)

Prior context (FINDINGS): BB%B 5m scalper decayed 2025-26; candle patterns
+ filters lost 5y OOS; no honest intraday edge after fees has survived here.
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

FEE, SLIP = 0.00055, 0.0002
BUF = 0.0005
TIME_STOP = 144          # 5m bars = 12h
PAIR = sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT"


def load() -> pd.DataFrame:
    df = pd.read_csv(f"data/cache/{PAIR}_5m.csv", parse_dates=["timestamp"])
    return df.set_index("timestamp").sort_index()


def prep(df: pd.DataFrame) -> pd.DataFrame:
    # HTF: 1h resample, SMA20 slope; map each closed 1h bar onto later 5m bars
    h1 = df["close"].resample("1h").last().dropna()
    sma = h1.rolling(20).mean()
    bias = pd.Series(np.where(sma > sma.shift(3), 1,
                     np.where(sma < sma.shift(3), -1, 0)), index=h1.index)
    # bias known at 1h bar CLOSE -> applies to 5m bars of the NEXT hour
    df["bias"] = bias.reindex(df.index.floor("h") - pd.Timedelta(hours=1)).values

    eh = df["high"].ewm(span=20, adjust=False).mean()
    sh = df["high"].rolling(20).std()
    el = df["low"].ewm(span=20, adjust=False).mean()
    sl_ = df["low"].rolling(20).std()
    df["buy_lo"] = eh - 2 * sh        # bottom of green buy band
    df["buy_mid"] = eh
    df["sell_hi"] = el + 2 * sl_      # top of red sell band
    df["sell_mid"] = el
    body = (df["close"] - df["open"]).abs()
    lw = df[["open", "close"]].min(axis=1) - df["low"]
    uw = df["high"] - df[["open", "close"]].max(axis=1)
    df["long_sig"] = ((df["bias"] == 1) & (df["low"] <= df["buy_lo"])
                      & (df["close"] > df["open"]) & (lw >= body))
    df["short_sig"] = ((df["bias"] == -1) & (df["high"] >= df["sell_hi"])
                       & (df["close"] < df["open"]) & (uw >= body))
    return df


def run(df: pd.DataFrame, tp_mode: str) -> pd.DataFrame:
    o = df["open"].values; h = df["high"].values; l = df["low"].values
    c = df["close"].values
    bm = df["buy_mid"].values; sm = df["sell_mid"].values
    ls = df["long_sig"].values; ss = df["short_sig"].values
    ts = df.index
    trades, i, n = [], 25, len(df)
    while i < n - 1:
        side = 1 if ls[i] else (-1 if ss[i] else 0)
        if side == 0:
            i += 1
            continue
        entry = o[i + 1] * (1 + side * SLIP)
        sl = l[i] * (1 - BUF) if side == 1 else h[i] * (1 + BUF)
        risk = abs(entry - sl)
        if risk <= 0 or risk / entry > 0.03:          # nonsense wick — skip
            i += 1
            continue
        tp = entry + side * risk * (1 if tp_mode == "1R" else 2) \
            if tp_mode in ("1R", "2R") else None
        j, ex, why = i + 1, None, None
        while j < n:
            if (side == 1 and l[j] <= sl) or (side == -1 and h[j] >= sl):
                ex, why = (min(sl, o[j]) if side == 1 else max(sl, o[j])), "SL"
            elif tp is not None and ((side == 1 and h[j] >= tp)
                                     or (side == -1 and l[j] <= tp)):
                ex, why = tp, "TP"
            elif tp is None and ((side == 1 and c[j] >= bm[j])
                                 or (side == -1 and c[j] <= sm[j])):
                ex, why = c[j], "MID"
            elif j - i >= TIME_STOP:
                ex, why = c[j], "TIME"
            if ex is not None:
                fill = ex * (1 - side * SLIP)
                net = side * (fill - entry) / entry - 2 * FEE
                trades.append({"t": ts[i], "side": side, "net": net, "why": why})
                break
            j += 1
        i = j + 1
    return pd.DataFrame(trades)


def report(tr: pd.DataFrame, label: str) -> None:
    if not len(tr):
        print(f"{label}: no trades")
        return
    eq = (1 + tr["net"]).cumprod()
    wins, losses = tr[tr.net > 0], tr[tr.net <= 0]
    pf = wins.net.sum() / abs(losses.net.sum()) if len(losses) else float("inf")
    print(f"\n{label}: N={len(tr)}  win {len(wins)/len(tr)*100:.0f}%  "
          f"PF {pf:.2f}  avg {tr.net.mean()*100:+.3f}%  "
          f"compounded {(eq.iloc[-1]-1)*100:+.1f}%")
    yr = tr.set_index("t").groupby(lambda x: x.year)["net"]
    print("  yearly: " + "  ".join(
        f"{y}:{(np.prod(1+g.values)-1)*100:+.0f}%" for y, g in yr))
    print("  exits: " + "  ".join(
        f"{w}:{len(g)}({g.net.mean()*100:+.2f}%)"
        for w, g in tr.groupby("why")))


def main() -> None:
    df = prep(load())
    n_l, n_s = int(df["long_sig"].sum()), int(df["short_sig"].sum())
    print(f"{PAIR} 5m {df.index[0].date()} -> {df.index[-1].date()} "
          f"({len(df):,} bars) | signals: {n_l} long / {n_s} short")
    for mode in ("1R", "2R", "MID"):
        tr = run(df, mode)
        report(tr, f"TP={mode}")
        if len(tr):
            for s, g in tr.groupby("side"):
                print(f"    {'LONG' if s==1 else 'SHORT'}: N={len(g)} "
                      f"avg {g.net.mean()*100:+.3f}%")


if __name__ == "__main__":
    main()
