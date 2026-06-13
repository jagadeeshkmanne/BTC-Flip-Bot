"""agent_short_fundingsqueeze.py
Hypothesis: FUNDING-CONDITIONED shorts — short only when structure is bearish
(price < EMA150 on 4h) AND funding is still POSITIVE (crowded longs = fuel,
and the short collects funding). Exit when funding flips negative or trend up.
Variants use raw funding>0 or trailing-90d funding percentile, shift(1)'d.

Honesty: signals on closed bars only (held = pos.shift(1)); costs 0.075%/side
on |position change|; IS = ..2022-12-31, OOS = 2023-01-01.. ; config chosen
on IS Sharpe only.
"""
import sys
sys.path.insert(0, "/Users/jags/Desktop/BTC-Flip-Bot/backtest")
import numpy as np
import pandas as pd
from trend_improve_round2 import load, adx, pos_c, F, COINS, COST

SPLIT = pd.Timestamp("2023-01-01")
TF = "4h"
ANN = 2190.0

# funding per 4h bar (8h rate / 2), ffill
fund = (F["funding_rate"].resample(TF).ffill() / 2)

P = {s: load(s, TF) for s in COINS}

# ---- v3 long sleeve (baseline): EMA30>150 & c>EMA50 & ADX>20, leader gate for alts
btc_up = pos_c(P["BTCUSDT"], 20)
LONGPOS = {}
for s in COINS:
    lp = pos_c(P[s], 20)
    if s != "BTCUSDT":
        lp = lp * btc_up.reindex(lp.index).ffill().fillna(0.0)
    LONGPOS[s] = lp

# BTC bear-structure for leader gate on shorts
c_btc = P["BTCUSDT"]["close"]
btc_bear = (c_btc < c_btc.ewm(span=150, adjust=False, min_periods=150).mean()).astype(float)

# funding percentile over trailing 90d (540 4h-bars), shift(1) to avoid lookahead
fr4 = fund
f_pct = fr4.rolling(540, min_periods=270).rank(pct=True).shift(1)
f_pos = (fr4 > 0).astype(float).shift(1)


def fund_cond(mode, idx):
    if mode == "raw>0":
        fc = f_pos
    elif mode == "pct>0.5":
        fc = (f_pct > 0.5).astype(float)
    else:  # pct>0.7
        fc = (f_pct > 0.7).astype(float)
    return fc.reindex(idx).fillna(0.0)


def short_positions(mode, use_adx, leader):
    SP = {}
    for s in COINS:
        df = P[s]; c = df["close"]
        bear = (c < c.ewm(span=150, adjust=False, min_periods=150).mean())
        cond = bear & (fund_cond(mode, c.index) > 0)
        if use_adx:
            cond = cond & (adx(df) > 20)
        cond = cond.astype(float)
        if leader and s != "BTCUSDT":
            cond = cond * btc_bear.reindex(cond.index).ffill().fillna(0.0)
        SP[s] = -cond  # short = -1
    return SP


def port_returns(POS):
    """POS: dict coin -> position series (+1/0/-1). returns portfolio return series."""
    R = {}
    for s in COINS:
        c = P[s]["close"]
        pos = POS[s].reindex(c.index).fillna(0.0)
        held = pos.shift(1).fillna(0.0)
        flips = held.diff().abs().fillna(0.0)
        fr = fund.reindex(c.index).fillna(0.0)
        # longs PAY funding, shorts RECEIVE: -held*fr handles both signs
        R[s] = held * c.pct_change() - held * fr - COST * flips
    return pd.DataFrame(R).mean(axis=1, skipna=True).dropna()


def met(sr):
    if len(sr) == 0:
        return dict(total=0.0, sharpe=0.0, dd=0.0, n=0)
    eq = (1 + sr).cumprod()
    total = (eq.iloc[-1] - 1) * 100
    sh = sr.mean() / sr.std() * np.sqrt(ANN) if sr.std() > 0 else 0.0
    dd = (eq / eq.cummax() - 1).min() * 100
    return dict(total=total, sharpe=sh, dd=dd)


def split_met(sr):
    return met(sr[sr.index < SPLIT]), met(sr[sr.index >= SPLIT])


def n_trades(POS):
    n = 0
    for s in COINS:
        held = POS[s].shift(1).fillna(0.0)
        n += int((held.diff().fillna(0.0) < 0).sum())  # short entries
    return n


# ---------------- baseline long-only ----------------
base_r = port_returns(LONGPOS)
bi, bo = split_met(base_r)
print(f"BASELINE long-only  IS  total {bi['total']:+8.1f}%  Sh {bi['sharpe']:.2f}  DD {bi['dd']:.1f}%")
print(f"BASELINE long-only  OOS total {bo['total']:+8.1f}%  Sh {bo['sharpe']:.2f}  DD {bo['dd']:.1f}%")
print()

# ---------------- sweep (12 configs), pick by IS Sharpe ----------------
configs = []
for mode in ["raw>0", "pct>0.5", "pct>0.7"]:
    for use_adx in [False, True]:
        for leader in [False, True]:
            configs.append((mode, use_adx, leader))

rows = []
print("SHORT SLEEVE (standalone)")
print(f"{'mode':8s} {'adx':3s} {'ldr':3s} | {'IS tot':>8s} {'IS Sh':>6s} {'IS DD':>6s} | {'OOS tot':>8s} {'OOS Sh':>6s} {'OOS DD':>6s} | trd")
for mode, ua, ld in configs:
    SP = short_positions(mode, ua, ld)
    r = port_returns(SP)
    i, o = split_met(r)
    nt = n_trades(SP)
    rows.append(dict(mode=mode, adx=ua, leader=ld, is_sh=i['sharpe'],
                     i=i, o=o, nt=nt, SP=SP))
    print(f"{mode:8s} {str(ua)[0]:3s} {str(ld)[0]:3s} | {i['total']:+8.1f} {i['sharpe']:6.2f} {i['dd']:6.1f} | {o['total']:+8.1f} {o['sharpe']:6.2f} {o['dd']:6.1f} | {nt}")

best = max(rows, key=lambda r: r['is_sh'])
print(f"\nBEST by IS Sharpe: mode={best['mode']} adx={best['adx']} leader={best['leader']}")
print(f"  sleeve IS : total {best['i']['total']:+.1f}%  Sh {best['i']['sharpe']:.2f}  DD {best['i']['dd']:.1f}%")
print(f"  sleeve OOS: total {best['o']['total']:+.1f}%  Sh {best['o']['sharpe']:.2f}  DD {best['o']['dd']:.1f}%  trades {best['nt']}")

# ---------------- combined: long wins; short only when long flat ----------------
COMB = {}
for s in COINS:
    lp = LONGPOS[s]
    sp = best['SP'][s].reindex(lp.index).fillna(0.0)
    COMB[s] = lp + sp * (lp == 0).astype(float)
comb_r = port_returns(COMB)
ci, co = split_met(comb_r)
print(f"\nCOMBINED IS : total {ci['total']:+.1f}%  Sh {ci['sharpe']:.2f}  DD {ci['dd']:.1f}%")
print(f"COMBINED OOS: total {co['total']:+.1f}%  Sh {co['sharpe']:.2f}  DD {co['dd']:.1f}%")

# yearwise sleeve returns for window-luck diagnosis
sl = port_returns(best['SP'])
print("\nsleeve by year:")
print(((1 + sl).groupby(sl.index.year).prod() - 1).mul(100).round(1))
print("\ncombined by year:")
print(((1 + comb_r).groupby(comb_r.index.year).prod() - 1).mul(100).round(1))
print("\nbaseline by year:")
print(((1 + base_r).groupby(base_r.index.year).prod() - 1).mul(100).round(1))
