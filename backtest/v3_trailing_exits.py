"""v3_trailing_exits.py — trailing-TP / profit-lock variants ON v3.
v3 entry everywhere: EMA30>EMA150 & px>EMA50 & ADX>20 (+BTC-leader for alts).
Exits compared:
  base    : v3 rules exit (px<EMA50 or cross/ADX fail)  [EMA50 = built-in trail]
  chand3  : ATR(22)x3 trail from highest high since entry (classic chandelier)
  chand2  : tighter ATR x2 trail
  partial : sell half at +8% gain, remainder exits on v3 rules
  plock   : exit all if open profit retraces 40% from its peak (profit-lock)
Perp, funding paid, fees per leg change. IS/OOS.
"""
import numpy as np
import pandas as pd
import trend_improve_round2 as T

SPLIT = pd.Timestamp("2023-01-01")
COST = T.COST
COINS = T.COINS
fund = (T.F["funding_rate"].resample("4h").ffill() / 2.0)
P = {s: T.load(s, "4h") for s in COINS}
BTC_UP = T.pos_c(P["BTCUSDT"])


def episodes(sym, mode):
    df = P[sym]
    c = df["close"].values; h = df["high"].values
    idx = df.index
    sig = T.pos_c(df).values
    if sym != "BTCUSDT":
        sig = sig * BTC_UP.reindex(idx).ffill().fillna(0.0).values
    a = T.atr(df).values
    fr = fund.reindex(idx).fillna(0.0).values
    ret = np.zeros(len(c))
    pos = 0.0; entry = 0.0; hh = 0.0; peak_pnl = 0.0
    for i in range(1, len(c)):
        prev = pos
        if pos > 0:
            ret[i] += pos * (c[i]/c[i-1] - 1) - pos * fr[i]
            hh = max(hh, h[i])
            pnl = c[i]/entry - 1
            peak_pnl = max(peak_pnl, pnl)
            exit_all = False
            if mode == "base":
                exit_all = sig[i] == 0
            elif mode in ("chand3", "chand2"):
                k = 3.0 if mode == "chand3" else 2.0
                exit_all = (c[i] < hh - k*a[i]) or sig[i] == 0
            elif mode == "partial":
                if pos == 1.0 and pnl >= 0.08:
                    ret[i] -= 0.5*COST; pos = 0.5      # bank half
                exit_all = sig[i] == 0
            elif mode == "plock":
                exit_all = (peak_pnl > 0.05 and pnl < 0.6*peak_pnl) or sig[i] == 0
            if exit_all:
                ret[i] -= pos*COST; pos = 0.0
        if pos == 0.0 and sig[i] == 1 and (mode == "base" or sig[i-1] == 0 or prev == 0):
            if sig[i] == 1 and (i+1) < len(c):
                pos = 1.0; entry = c[i]; hh = h[i]; peak_pnl = 0.0
                ret[i] -= COST
    return pd.Series(ret, index=idx)


def met(sr):
    eq = (1+sr).cumprod()
    sh = sr.mean()/sr.std()*np.sqrt(2190.0) if sr.std() > 0 else 0
    dd = (eq/eq.cummax()-1).min()
    return (eq.iloc[-1]-1)*100, sh, dd*100


print(f"  {'exit':>9} | {'IS tot':>9} {'IS Sh':>6} {'IS DD':>6} | {'OOS tot':>8} {'OOS Sh':>7} {'OOS DD':>7}")
for mode in ("base", "chand3", "chand2", "partial", "plock"):
    port = pd.DataFrame({s: episodes(s, mode) for s in COINS}).mean(axis=1, skipna=True).dropna()
    i, o = port[port.index < SPLIT], port[port.index >= SPLIT]
    ti, si, di = met(i); to, so, do = met(o)
    print(f"  {mode:>9} | {ti:>+8.0f}% {si:>6.2f} {di:>5.0f}% | {to:>+7.0f}% {so:>7.2f} {do:>6.0f}%")
