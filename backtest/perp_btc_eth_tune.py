"""perp_btc_eth_tune.py — perp-only trend strategy, BTC+ETH, honest fine-tune.
(user 2026-06-12: no spot, BTC/ETH only, fine-tune for best profit)

Model: long PERP when trend up, flat otherwise. Longs PAY funding while held
(BTC funding history applied to both coins — proxy). Taker fee+slip on flips.
Fine-tune protocol (anti-overfit): grid-search EMA fast/slow/exit on IS
(..2022-12-31), then judge ONLY by OOS (2023..). Then leverage with liquidation.
"""
import numpy as np
import pandas as pd

COST = 0.00075
ANN = 2190.0
SPLIT = pd.Timestamp("2023-01-01")
COINS = ["BTCUSDT", "ETHUSDT"]

F = pd.read_csv("/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_funding.csv",
                parse_dates=["timestamp"]).set_index("timestamp")
fund4h = (F["funding_rate"].resample("4h").ffill() / 2.0)   # 8h rate -> per 4h bar

PX = {}
for s in COINS:
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{s}_4h.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    PX[s] = df["close"]


def strat(c, fast, slow, exit_ema):
    ef = c.ewm(span=fast, adjust=False, min_periods=fast).mean()
    es = c.ewm(span=slow, adjust=False, min_periods=slow).mean()
    pos = ef > es
    if exit_ema:
        ee = c.ewm(span=exit_ema, adjust=False, min_periods=exit_ema).mean()
        pos = pos & (c > ee)
    ret = c.pct_change()
    held = pos.astype(float).shift(1).fillna(0.0)
    flips = held.diff().abs().fillna(0.0)
    fr = fund4h.reindex(c.index).fillna(0.0)
    return (held * ret - COST * flips - held * fr).dropna()   # funding paid while long


def metrics(sr, lev=1.0):
    s = sr * lev
    eq = 1.0; mx = 1.0; dd = 0.0
    for x in s.values:
        eq *= (1 + x)
        if eq <= 0:
            return None  # liquidated
        mx = max(mx, eq); dd = max(dd, 1 - eq / mx)
    yrs = len(s) / ANN
    sh = sr.mean() / sr.std() * np.sqrt(ANN) if sr.std() > 0 else 0
    return eq - 1, (eq) ** (1 / yrs) - 1, sh, -dd


rows = []
for fast in (20, 30, 50):
    for slow in (100, 150, 200, 300):
        if slow <= fast * 2:
            continue
        for exit_ema in (None, 50):
            port = pd.DataFrame({s: strat(PX[s], fast, slow, exit_ema) for s in COINS}).mean(axis=1, skipna=True).dropna()
            is_, oos = port[port.index < SPLIT], port[port.index >= SPLIT]
            mi, mo = metrics(is_), metrics(oos)
            if mi and mo:
                rows.append((fast, slow, exit_ema, mi, mo))

rows.sort(key=lambda r: r[3][2], reverse=True)   # rank by IS Sharpe (honest: choose on IS)
print("PERP BTC+ETH, funding charged to longs. Grid ranked by IN-SAMPLE Sharpe; judge by OOS.")
print(f"  {'fast':>4} {'slow':>5} {'exit':>5} | {'IS tot':>9} {'IS Sh':>6} {'IS DD':>6} | {'OOS tot':>9} {'OOS Sh':>7} {'OOS DD':>7}")
for fast, slow, ee, mi, mo in rows[:8]:
    print(f"  {fast:>4} {slow:>5} {str(ee or '-'):>5} | {mi[0]*100:>+8.0f}% {mi[2]:>6.2f} {mi[3]*100:>5.0f}% | "
          f"{mo[0]*100:>+8.0f}% {mo[2]:>7.2f} {mo[3]*100:>6.0f}%")

# pick the IS-best config, show full-period + leverage honestly
fast, slow, ee, _, _ = rows[0]
port = pd.DataFrame({s: strat(PX[s], fast, slow, ee) for s in COINS}).mean(axis=1, skipna=True).dropna()
print(f"\nCHOSEN ON IS: EMA{fast}/{slow}, exit={'px<EMA'+str(ee) if ee else 'cross only'}")
py = port.groupby(port.index.year).apply(lambda s: (1 + s).prod() - 1)
print("  per-year: " + "  ".join(f"{y}:{v*100:+.0f}%" for y, v in py.items()))
print(f"\n  leverage on full period (perp, funding paid, liquidation modeled):")
print(f"  {'lev':>4} {'total':>11} {'CAGR':>6} {'maxDD':>7}")
for lev in (1, 2, 3):
    m = metrics(port, lev)
    if m is None:
        print(f"  {lev:>3}x  LIQUIDATED")
    else:
        print(f"  {lev:>3}x {m[0]*100:>+10,.0f}% {m[1]*100:>5.0f}% {m[3]*100:>6.0f}%")
