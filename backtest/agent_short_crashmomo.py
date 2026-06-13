"""agent_short_crashmomo.py — TIME-BOXED CRASH MOMENTUM shorts.

Hypothesis: after a violent down-impulse (12-bar return < -thr AND ADX rising
AND price < EMA150), short for a FIXED window of H bars, then exit
unconditionally (no overstay into the squeeze).

Sweep (12 configs): thr in {6%, 8%, 10%} x H in {12, 24, 36, 48} bars (4h bars).
Pick best by IS Sharpe ONLY, report OOS untouched.
Then combine with the v3 long sleeve (long wins on conflict) and compare to
the long-only baseline.

Honesty: signals on closed bars, position = signal.shift(1); fees 0.075% per
side on |position change|; shorts RECEIVE funding; IS ..2022-12-31, OOS 2023..
"""
import sys
sys.path.insert(0, "/Users/jags/Desktop/BTC-Flip-Bot/backtest")
import numpy as np
import pandas as pd
from trend_improve_round2 import load, adx, pos_c, F, COINS, COST

SPLIT = pd.Timestamp("2023-01-01")
TF = "4h"
ANN = 2190.0

# ---------- data ----------
P = {s: load(s, TF) for s in COINS}
FUND = (F["funding_rate"].resample(TF).ffill() / 2)  # per-4h-bar funding

ADX = {s: adx(P[s]) for s in COINS}
EMA150 = {s: P[s]["close"].ewm(span=150, adjust=False, min_periods=150).mean() for s in COINS}

# v3 long sleeve positions (leader gate for alts)
btc_up = pos_c(P["BTCUSDT"], 20)
LONG = {}
for s in COINS:
    lp = pos_c(P[s], 20)
    if s != "BTCUSDT":
        lp = lp * btc_up.reindex(lp.index).ffill().fillna(0.0)
    LONG[s] = lp


def short_signal(s, thr, H):
    """-1 while inside the H-bar window after a crash trigger, else 0."""
    c = P[s]["close"]
    trig = ((c.pct_change(12) < -thr)
            & (ADX[s].diff(3) > 0)          # ADX rising (past data only)
            & (c < EMA150[s]))
    inwin = trig.astype(float).rolling(H, min_periods=1).max().fillna(0.0)
    return -inwin  # 0 or -1


def pnl(pos_by_coin):
    """Equal-weight portfolio returns from per-coin position series."""
    R = {}
    for s in COINS:
        c = P[s]["close"]
        held = pos_by_coin[s].reindex(c.index).fillna(0.0).shift(1).fillna(0.0)
        flips = held.diff().abs().fillna(0.0)
        fr = FUND.reindex(c.index).fillna(0.0)
        # longs PAY funding (held>0 -> -fr), shorts RECEIVE (held<0 -> +fr)
        R[s] = held * c.pct_change() - held * fr - COST * flips
    return pd.DataFrame(R).mean(axis=1, skipna=True).dropna()


def met(sr):
    if len(sr) == 0 or sr.std() == 0:
        return dict(total=0.0, sharpe=0.0, dd=0.0, n=len(sr))
    eq = (1 + sr).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    return dict(total=(eq.iloc[-1] - 1) * 100,
                sharpe=sr.mean() / sr.std() * np.sqrt(ANN),
                dd=dd * 100, n=len(sr))


def split(sr):
    return sr[sr.index < SPLIT], sr[sr.index >= SPLIT]


def trades(pos_by_coin, lo, hi):
    n = 0
    for s in COINS:
        p = pos_by_coin[s]
        p = p[(p.index >= lo) & (p.index < hi)]
        n += int(((p.diff() == -1) | ((p == -1) & p.shift(1).isna())).sum())
    return n


# ---------- baseline long-only ----------
base = pnl(LONG)
b_is, b_oos = split(base)
mb_is, mb_oos = met(b_is), met(b_oos)
print(f"BASELINE long-only  IS  total {mb_is['total']:+.1f}%  Sharpe {mb_is['sharpe']:.2f}  DD {mb_is['dd']:.1f}%")
print(f"BASELINE long-only  OOS total {mb_oos['total']:+.1f}%  Sharpe {mb_oos['sharpe']:.2f}  DD {mb_oos['dd']:.1f}%")
print()

# ---------- sweep short sleeve (IS Sharpe only for selection) ----------
THRS = [0.06, 0.08, 0.10]
HS = [12, 24, 36, 48]
rows = []
for thr in THRS:
    for H in HS:
        SP = {s: short_signal(s, thr, H) for s in COINS}
        sr = pnl(SP)
        s_is, s_oos = split(sr)
        mi = met(s_is)
        nt = trades(SP, pd.Timestamp("2000-01-01"), SPLIT)
        rows.append(dict(thr=thr, H=H, is_sharpe=mi["sharpe"], is_total=mi["total"],
                         is_dd=mi["dd"], is_trades=nt))
        print(f"thr={thr:.0%} H={H:2d}  IS total {mi['total']:+7.1f}%  IS Sharpe {mi['sharpe']:+5.2f}  IS DD {mi['dd']:6.1f}%  IS trade-windows {nt}")

best = max(rows, key=lambda r: r["is_sharpe"])
print(f"\nBEST by IS Sharpe: thr={best['thr']:.0%} H={best['H']}")

# ---------- best config: full sleeve report ----------
thr, H = best["thr"], best["H"]
SP = {s: short_signal(s, thr, H) for s in COINS}
sr = pnl(SP)
s_is, s_oos = split(sr)
ms_is, ms_oos = met(s_is), met(s_oos)
nt_is = trades(SP, pd.Timestamp("2000-01-01"), SPLIT)
nt_oos = trades(SP, SPLIT, pd.Timestamp("2100-01-01"))
print(f"SLEEVE IS  total {ms_is['total']:+.1f}%  Sharpe {ms_is['sharpe']:.2f}  DD {ms_is['dd']:.1f}%  windows {nt_is}")
print(f"SLEEVE OOS total {ms_oos['total']:+.1f}%  Sharpe {ms_oos['sharpe']:.2f}  DD {ms_oos['dd']:.1f}%  windows {nt_oos}")

# per-year sleeve breakdown (honesty: is it one lucky window?)
print("\nSleeve by year:")
for y, g in sr.groupby(sr.index.year):
    eq = (1 + g).cumprod()
    print(f"  {y}: {(eq.iloc[-1]-1)*100:+7.2f}%")

# ---------- combined: v3 long + crash shorts (long wins) ----------
COMB = {}
for s in COINS:
    lp = LONG[s]
    sp = SP[s].reindex(lp.index).fillna(0.0)
    COMB[s] = lp + sp * (lp == 0)
cr = pnl(COMB)
c_is, c_oos = split(cr)
mc_is, mc_oos = met(c_is), met(c_oos)
print(f"\nCOMBINED IS  total {mc_is['total']:+.1f}%  Sharpe {mc_is['sharpe']:.2f}  DD {mc_is['dd']:.1f}%")
print(f"COMBINED OOS total {mc_oos['total']:+.1f}%  Sharpe {mc_oos['sharpe']:.2f}  DD {mc_oos['dd']:.1f}%")
print(f"\nvs baseline OOS total {mb_oos['total']:+.1f}%  Sharpe {mb_oos['sharpe']:.2f}  DD {mb_oos['dd']:.1f}%")
