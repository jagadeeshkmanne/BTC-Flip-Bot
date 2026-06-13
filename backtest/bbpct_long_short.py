"""bbpct_long_short.py — does adding the SHORT side help the BB%B scalper?
long  : enter when %B pctile<10 & uptrend(HMA>EMA) & daily up  -> exit %B>90
short : enter when %B pctile>90 & downtrend(HMA<EMA) & daily down -> exit %B<10
"""
import numpy as np
import pandas as pd
from replicate_bbpct_scalp import load, random_compare

RT_TAKER, RT_MAKER = 0.0014, 0.0005


def bt(df, mode):
    o = df["open"].values; cl = df["close"].values
    pr, ratio, dayup = df["pr"].values, df["ratio"].values, df["dayup"].values
    n = len(df); i = 1; rets = []; holds = []
    while i < n - 1:
        sig = 0
        if mode in ("long", "both") and pr[i] < 0.10 and ratio[i] > 1.0005 and dayup[i] == True:
            sig = 1
        elif mode in ("short", "both") and pr[i] > 0.90 and ratio[i] < 0.9995 and dayup[i] == False:
            sig = -1
        if sig == 0 or np.isnan(pr[i]):
            i += 1; continue
        entry = o[i + 1]; j = i + 1
        while j < n - 1:
            if (sig == 1 and pr[j] > 0.90) or (sig == -1 and pr[j] < 0.10):
                break
            j += 1
        exit_px = o[j + 1] if j + 1 < n else cl[-1]
        rets.append((exit_px / entry - 1) * sig); holds.append(j - i); i = j + 1
    return np.array(rets), np.array(holds)


for sym in ("BTCUSDT", "ETHUSDT"):
    df = load(sym)
    print(f"\n===== {sym} (full history) =====")
    print(f"  {'mode':>6} {'trades':>7} {'win%':>6} {'gross/t':>9} {'T':>6} "
          f"{'net taker':>10} {'net maker':>10}")
    for mode in ("long", "short", "both"):
        r, hold = bt(df, mode)
        if len(r) == 0:
            print(f"  {mode:>6}  no trades"); continue
        rnd = random_compare(df, len(r), max(1, int(np.median(hold))))
        t = (r.mean() - rnd.mean()) / (rnd.std() + 1e-12)
        nt = (np.prod(1 + (r - RT_TAKER)) - 1) * 100
        nm = (np.prod(1 + (r - RT_MAKER)) - 1) * 100
        print(f"  {mode:>6} {len(r):>7} {(r>0).mean()*100:>5.1f}% {r.mean()*100:>+8.4f}% "
              f"{t:>+6.2f} {nt:>+9.0f}% {nm:>+9.0f}%")
