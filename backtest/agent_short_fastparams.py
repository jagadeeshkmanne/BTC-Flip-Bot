"""agent_short_fastparams.py — HYPOTHESIS: crashes unfold ~3x faster than rallies,
so the short side may need much FASTER EMAs than the long side (v3 uses 30/150 + px<50).

Short entry: EMA(fast) < EMA(slow)  AND  price < EMA(exit_span)  AND  ADX(14) > 20.
Alts additionally require BTC in the same bear condition (leader gate, mirrored from v3).
Exit when any condition fails (state is just the boolean condition, mirrored from v3 longs).

Grid (6 configs, <=12 allowed):
  (fast, slow) in {(10,50), (15,75), (20,100)}  x  exit_span in {20, 30}

Pick best by IS Sharpe of the SHORT SLEEVE ONLY (..2022-12-31). Report its OOS untouched.
Then combine with v3 long sleeve (long wins on conflict) and compare vs long-only baseline.

Honesty: signals on closed bars (shift(1)); fees 0.075% per |position change|;
funding via held*fr (held<0 -> shorts RECEIVE); no OOS peeking for selection.
"""
import numpy as np
import pandas as pd
import sys

sys.path.insert(0, "/Users/jags/Desktop/BTC-Flip-Bot/backtest")
from trend_improve_round2 import load, adx, pos_c, F, COINS, COST

SPLIT = pd.Timestamp("2023-01-01")
ANN = 2190.0


def met(sr):
    sr = sr.dropna()
    if len(sr) == 0 or sr.std() == 0:
        return 0.0, 0.0, 0.0
    eq = (1 + sr).cumprod()
    total = (eq.iloc[-1] - 1) * 100
    sharpe = sr.mean() / sr.std() * np.sqrt(ANN)
    dd = ((eq / eq.cummax()) - 1).min() * 100
    return total, sharpe, dd


def bear_sig(df, fast, slow, exit_span, adx_thr=20):
    c = df["close"]
    ef = c.ewm(span=fast, adjust=False, min_periods=fast).mean()
    es = c.ewm(span=slow, adjust=False, min_periods=slow).mean()
    ex = c.ewm(span=exit_span, adjust=False, min_periods=exit_span).mean()
    return ((ef < es) & (c < ex) & (adx(df) > adx_thr)).astype(float)


P = {s: load(s, "4h") for s in COINS}
fund = (F["funding_rate"].resample("4h").ffill() / 2)  # per 4h bar

# v3 long positions (with leader gate for alts)
btc_up = pos_c(P["BTCUSDT"])
LONGPOS = {}
for s in COINS:
    pos = pos_c(P[s])
    if s != "BTCUSDT":
        pos = pos * btc_up.reindex(pos.index).ffill().fillna(0.0)
    LONGPOS[s] = pos


def sleeve_returns(fast, slow, exit_span):
    """short-only sleeve: -1 when bear signal, else 0 (per coin), portfolio = mean."""
    btc_bear = bear_sig(P["BTCUSDT"], fast, slow, exit_span)
    R = {}
    for s in COINS:
        df = P[s]
        c = df["close"]
        sig = bear_sig(df, fast, slow, exit_span)
        if s != "BTCUSDT":
            sig = sig * btc_bear.reindex(sig.index).ffill().fillna(0.0)
        pos = -sig  # short = -1
        held = pos.shift(1).fillna(0.0)
        flips = held.diff().abs().fillna(0.0)
        fr = fund.reindex(c.index).fillna(0.0)
        R[s] = held * c.pct_change() - held * fr - COST * flips
    return pd.DataFrame(R).mean(axis=1, skipna=True).dropna()


def combined_returns(fast, slow, exit_span):
    """long per v3; short when bear signal fires AND v3 long flat; long wins."""
    btc_bear = bear_sig(P["BTCUSDT"], fast, slow, exit_span)
    R = {}
    for s in COINS:
        df = P[s]
        c = df["close"]
        sig = bear_sig(df, fast, slow, exit_span)
        if s != "BTCUSDT":
            sig = sig * btc_bear.reindex(sig.index).ffill().fillna(0.0)
        lp = LONGPOS[s]
        pos = lp.where(lp == 1.0, -sig)  # long wins; else short if signal
        held = pos.shift(1).fillna(0.0)
        flips = held.diff().abs().fillna(0.0)
        fr = fund.reindex(c.index).fillna(0.0)
        R[s] = held * c.pct_change() - held * fr - COST * flips
    return pd.DataFrame(R).mean(axis=1, skipna=True).dropna()


def longonly_returns():
    R = {}
    for s in COINS:
        c = P[s]["close"]
        held = LONGPOS[s].shift(1).fillna(0.0)
        flips = held.diff().abs().fillna(0.0)
        fr = fund.reindex(c.index).fillna(0.0)
        R[s] = held * c.pct_change() - held * fr - COST * flips
    return pd.DataFrame(R).mean(axis=1, skipna=True).dropna()


GRID = [(f, sl, ex) for (f, sl) in [(10, 50), (15, 75), (20, 100)] for ex in [20, 30]]

print("=== SHORT SLEEVE SWEEP (select by IS Sharpe ONLY) ===")
print(f"{'cfg':<16}{'IS tot%':>9}{'IS Shp':>8}{'IS DD%':>8} | {'OOS tot%':>9}{'OOS Shp':>8}{'OOS DD%':>8}")
best = None
results = {}
for f, sl, ex in GRID:
    r = sleeve_returns(f, sl, ex)
    ris, roos = r[r.index < SPLIT], r[r.index >= SPLIT]
    mis, moos = met(ris), met(roos)
    # also count round trips (entries) IS/OOS for honesty
    results[(f, sl, ex)] = (mis, moos)
    print(f"e{f}/{sl} ex{ex:<6}{mis[0]:>9.1f}{mis[1]:>8.2f}{mis[2]:>8.1f} | {moos[0]:>9.1f}{moos[1]:>8.2f}{moos[2]:>8.1f}")
    if best is None or mis[1] > results[best][0][1]:
        best = (f, sl, ex)

f, sl, ex = best
print(f"\nBEST BY IS SHARPE: ema{f}/{sl}, exit ema{ex}")

# trade count for best config
btc_bear = bear_sig(P["BTCUSDT"], f, sl, ex)
n_is = n_oos = 0
for s in COINS:
    sig = bear_sig(P[s], f, sl, ex)
    if s != "BTCUSDT":
        sig = sig * btc_bear.reindex(sig.index).ffill().fillna(0.0)
    ent = (sig.diff() == 1)
    n_is += int(ent[ent.index < SPLIT].sum())
    n_oos += int(ent[ent.index >= SPLIT].sum())
print(f"short entries: IS={n_is}, OOS={n_oos}")

# yearly breakdown of best sleeve (window-luck check)
r = sleeve_returns(f, sl, ex)
print("\nbest sleeve yearly returns %:")
print(((1 + r).groupby(r.index.year).prod() - 1).mul(100).round(1).to_string())

print("\n=== COMBINED (v3 long + best short sleeve) vs LONG-ONLY ===")
rc = combined_returns(f, sl, ex)
rl = longonly_returns()
for name, rr in [("long-only", rl), ("combined", rc)]:
    ris, roos = rr[rr.index < SPLIT], rr[rr.index >= SPLIT]
    mis, moos = met(ris), met(roos)
    print(f"{name:<10} IS  tot {mis[0]:>8.1f}%  Shp {mis[1]:.2f}  DD {mis[2]:.1f}%")
    print(f"{name:<10} OOS tot {moos[0]:>8.1f}%  Shp {moos[1]:.2f}  DD {moos[2]:.1f}%")

print("\ncombined yearly returns %:")
print(((1 + rc).groupby(rc.index.year).prod() - 1).mul(100).round(1).to_string())
