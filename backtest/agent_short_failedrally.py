"""agent_short_failedrally.py — SHORT THE FAILED RALLY hypothesis (honest test).

Hypothesis: in an established bear (close<EMA200 4h), wait for a rally INTO
resistance (close within tol of EMA100 or EMA150 from below), then short when
it rolls over (close crosses back below EMA50). Exit at prior-low touch
(close <= rolling min low of prior N bars at entry) or EMA50 cross back up.

Leader gate (fixed, mirrors v3): alts short only when BTC is also < its EMA200.
Shorts RECEIVE funding (held=-1 -> -held*fr adds funding).

Grid (8 configs, picked on IS Sharpe of the short sleeve ONLY):
  tol in {0.01, 0.02} x recency_lb in {6, 12} x priorlow_lb in {30, 60}

Honesty: signals on closed bars, position=signal.shift(1), cost=0.075%*|dpos|,
IS=..2022-12-31, OOS=2023-01-01.., no OOS peeking for selection.
"""
import numpy as np
import pandas as pd
import sys
sys.path.insert(0, "/Users/jags/Desktop/BTC-Flip-Bot/backtest")
from trend_improve_round2 import load, adx, COINS, COST, F, SPLIT

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


def emas(c):
    return {p: c.ewm(span=p, adjust=False, min_periods=p).mean() for p in (50, 100, 150, 200)}


def short_signal(df, tol, recency_lb, priorlow_lb, bear_gate_ext=None):
    """Stateful signal series in {0,-1}, computed bar-by-bar from closed data."""
    c = df["close"]; lo = df["low"]
    E = emas(c)
    bear = (c < E[200])
    if bear_gate_ext is not None:
        bear = bear & bear_gate_ext.reindex(c.index).ffill().fillna(False)
    # rally into resistance from below: close within tol below EMA100 or EMA150
    near = pd.Series(False, index=c.index)
    for p in (100, 150):
        near |= (c < E[p]) & (c >= E[p] * (1 - tol))
    touch = (bear & near)
    recent_touch = touch.rolling(recency_lb, min_periods=1).max().astype(bool)
    cross_dn = (c < E[50]) & (c.shift(1) >= E[50].shift(1))
    cross_up = (c > E[50])
    prior_low = lo.rolling(priorlow_lb).min().shift(1)  # excludes current bar

    entry = (bear & recent_touch & cross_dn).values
    cu = cross_up.values
    cl = c.values
    pl = prior_low.values

    sig = np.zeros(len(c))
    in_short = False
    tgt = np.nan
    for i in range(len(c)):
        if in_short:
            if cu[i] or (not np.isnan(tgt) and cl[i] <= tgt):
                in_short = False
            else:
                sig[i] = -1.0
                continue
        if not in_short and entry[i]:
            in_short = True
            tgt = pl[i]
            sig[i] = -1.0
    return pd.Series(sig, index=c.index)


def v3_long(df, btc_up=None):
    c = df["close"]
    pos = ((c.ewm(span=30, adjust=False, min_periods=30).mean() >
            c.ewm(span=150, adjust=False, min_periods=150).mean())
           & (c > c.ewm(span=50, adjust=False, min_periods=50).mean())
           & (adx(df) > 20)).astype(float)
    if btc_up is not None:
        pos = pos * btc_up.reindex(pos.index).ffill().fillna(0.0)
    return pos


def ret_from_pos(pos, c, fund):
    held = pos.shift(1).fillna(0.0)
    flips = held.diff().abs().fillna(0.0)
    fr = fund.reindex(c.index).fillna(0.0)
    return held * c.pct_change() - held * fr - COST * flips


def portfolio(pos_by_coin, P, fund):
    R = {s: ret_from_pos(pos_by_coin[s], P[s]["close"], fund) for s in COINS}
    return pd.DataFrame(R).mean(axis=1, skipna=True).dropna()


P = {s: load(s, "4h") for s in COINS}
fund = F["funding_rate"].resample("4h").ffill() / 2
btc_bear = P["BTCUSDT"]["close"] < emas(P["BTCUSDT"]["close"])[200]

# ---- sweep (sleeve only, IS Sharpe selection) ----
grid = [(tol, rlb, plb) for tol in (0.01, 0.02) for rlb in (6, 12) for plb in (30, 60)]
rows = []
sleeves = {}
for tol, rlb, plb in grid:
    pos = {}
    for s in COINS:
        gate = btc_bear if s != "BTCUSDT" else None
        pos[s] = short_signal(P[s], tol, rlb, plb, bear_gate_ext=gate)
    sr = portfolio(pos, P, fund)
    sleeves[(tol, rlb, plb)] = (pos, sr)
    is_t, is_s, is_d = met(sr[sr.index < SPLIT])
    oo_t, oo_s, oo_d = met(sr[sr.index >= SPLIT])
    ntr = sum(int((pos[s].diff() == -1).sum()) for s in COINS)
    rows.append((tol, rlb, plb, ntr, is_t, is_s, is_d, oo_t, oo_s, oo_d))

print("tol rlb plb Ntrades | IS: tot% sharpe dd% | OOS: tot% sharpe dd%")
for r in rows:
    print(f"{r[0]:.2f} {r[1]:>3} {r[2]:>3} {r[3]:>5} | {r[4]:8.1f} {r[5]:5.2f} {r[6]:6.1f} | {r[7]:8.1f} {r[8]:5.2f} {r[9]:6.1f}")

best = max(rows, key=lambda r: r[5])  # IS sharpe only
bk = (best[0], best[1], best[2])
print(f"\nBEST by IS Sharpe: tol={bk[0]} recency={bk[1]} priorlow={bk[2]}")
bpos, bsr = sleeves[bk]
print(f"  sleeve IS : tot {best[4]:.1f}%  sharpe {best[5]:.2f}  dd {best[6]:.1f}%")
print(f"  sleeve OOS: tot {best[7]:.1f}%  sharpe {best[8]:.2f}  dd {best[9]:.1f}%  trades(all) {best[3]}")

# trades per period
for s in COINS:
    p = bpos[s]
    ent = (p.diff() == -1)
    print(f"  {s}: entries IS {int(ent[ent.index<SPLIT].sum())}, OOS {int(ent[ent.index>=SPLIT].sum())}")

# ---- combined with v3 long sleeve ----
btc_up = v3_long(P["BTCUSDT"])
long_pos = {s: v3_long(P[s], btc_up if s != "BTCUSDT" else None) for s in COINS}

comb_pos = {}
for s in COINS:
    L = long_pos[s]
    Ssig = bpos[s].reindex(L.index).fillna(0.0)
    comb_pos[s] = L.where(L > 0, Ssig)  # long wins; short only when long flat

comb = portfolio(comb_pos, P, fund)
lo_only = portfolio(long_pos, P, fund)

for name, sr in (("LONG-ONLY v3", lo_only), ("COMBINED", comb)):
    it, isq, idd = met(sr[sr.index < SPLIT])
    ot, osq, odd = met(sr[sr.index >= SPLIT])
    print(f"\n{name}: IS tot {it:.1f}% sharpe {isq:.2f} dd {idd:.1f}% | OOS tot {ot:.1f}% sharpe {osq:.2f} dd {odd:.1f}%")

# yearly sleeve returns for window-luck check
yr = bsr.groupby(bsr.index.year).apply(lambda x: ((1+x).prod()-1)*100)
print("\nsleeve yearly %:", {int(k): round(v, 1) for k, v in yr.items()})
