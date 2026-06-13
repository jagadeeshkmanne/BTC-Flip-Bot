"""reconcile_window.py — BB%B scalper over the platform's EXACT window
(2020-12-01 -> 2023-12-01), BTC and ETH, honest fills + REAL vs REALIZED DD
+ the random-edge T-test.
"""
import numpy as np
import pandas as pd
from replicate_bbpct_scalp import load, backtest, random_compare

START, END = pd.Timestamp("2020-12-01"), pd.Timestamp("2023-12-01")

for sym in ("BTCUSDT", "ETHUSDT"):
    df = load(sym)
    df = df[(df["timestamp"] >= START) & (df["timestamp"] <= END)].reset_index(drop=True)
    r, hold = backtest(df)
    if len(r) == 0:
        print(f"{sym}: no trades"); continue
    bh = df["close"].iloc[-1] / df["close"].iloc[0] - 1
    rnd = random_compare(df, len(r), int(np.median(hold)))
    t = (r.mean() - rnd.mean()) / (rnd.std() + 1e-12)
    print(f"\n===== {sym}  {START.date()} -> {END.date()} =====")
    print(f"  trades {len(r)}  win% {(r>0).mean()*100:.1f}  hold {np.median(hold)*15/60:.1f}h  "
          f"gross/trade {r.mean()*100:+.4f}%")
    print(f"  EDGE vs random: T = {t:+.2f}  ({'REAL edge' if abs(t)>2 else 'NOT diff from random'})")
    print(f"  buy & hold this window: {bh*100:+.0f}%")
    print(f"  {'fee':>16} {'lev':>4} {'total':>9} {'REAL maxDD':>11}")
    for flabel, rt in [("taker+slip .14%", 0.0014), ("maker ~.05%", 0.0005), ("zero (platform)", 0.0)]:
        net = r - rt
        for lev in (1, 5):
            eq = [1.0]
            for x in net:
                nb = eq[-1] * (1 + lev * x); eq.append(max(nb, 0.0))
                if nb <= 0:
                    break
            eq = np.array(eq)
            dd = (eq/np.maximum.accumulate(eq)-1).min()*100
            tag = " LIQUIDATED" if eq[-1] <= 0 else ""
            print(f"  {flabel:>16} {lev:>3}x {(eq[-1]-1)*100:>+8.0f}% {dd:>10.0f}%{tag}")
