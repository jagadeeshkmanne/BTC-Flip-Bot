"""agent_short_breakdown.py — Turtle-style BREAKDOWN shorts on 4h, 4 pairs.

Hypothesis: enter SHORT when close < prior N-bar Donchian low (severe leg),
exit when close > prior M-bar high OR EMA30 crosses back above EMA150.
Optional gates: ADX(14)>20 at entry; BTC itself in broken-down state (leader).

Honesty: signals on closed bars, position = state.shift(1); rolling extremes
use .shift(1) (prior bars only); costs 0.075% per side on position changes;
shorts RECEIVE funding (R = held*ret - held*fr, held=-1 -> +fr).
IS < 2023-01-01, OOS >= 2023. Config chosen on IS Sharpe ONLY.
"""
import numpy as np
import pandas as pd

COST = 0.00075
SPLIT = pd.Timestamp("2023-01-01")
COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]
ANN = 2190.0

F = pd.read_csv("/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_funding.csv",
                parse_dates=["timestamp"]).set_index("timestamp")
FUND4H = F["funding_rate"].resample("4h").ffill() / 2.0


def load(sym):
    df = pd.read_csv(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_4h.csv",
                     parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    return df[["open", "high", "low", "close"]]


def atr(df, p=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"]-df["low"], (df["high"]-pc).abs(), (df["low"]-pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1/p, adjust=False).mean()


def adx(df, p=14):
    up = df["high"].diff(); dn = -df["low"].diff()
    plus = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, p)
    pdi = 100*plus.ewm(alpha=1/p, adjust=False).mean()/a
    mdi = 100*minus.ewm(alpha=1/p, adjust=False).mean()/a
    dx = 100*(pdi-mdi).abs()/(pdi+mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/p, adjust=False).mean()


def pos_long_v3(df, adx_thr=20):
    c = df["close"]
    return ((c.ewm(span=30, adjust=False, min_periods=30).mean() > c.ewm(span=150, adjust=False, min_periods=150).mean())
            & (c > c.ewm(span=50, adjust=False, min_periods=50).mean())
            & (adx(df) > adx_thr)).astype(float)


def short_state(df, n_entry=55, m_exit=20, adx_gate=True, leader_mask=None):
    """state series: -1 while short, 0 flat. Entry/exit eval'd on closed bar t,
    so held position = state.shift(1). leader_mask: bool series (BTC broken)."""
    c = df["close"].values
    lo = df["low"].rolling(n_entry).min().shift(1).values   # prior N-bar low (excl current)
    hi = df["high"].rolling(m_exit).max().shift(1).values   # prior M-bar high
    e30 = df["close"].ewm(span=30, adjust=False, min_periods=30).mean().values
    e150 = df["close"].ewm(span=150, adjust=False, min_periods=150).mean().values
    ax = adx(df).values
    lead = leader_mask.reindex(df.index).fillna(False).values if leader_mask is not None \
        else np.ones(len(c), dtype=bool)
    st = np.zeros(len(c))
    in_short = False
    for t in range(len(c)):
        if np.isnan(lo[t]) or np.isnan(hi[t]):
            continue
        if in_short:
            # exit: recovery above M-bar high OR EMA30 back above EMA150
            if c[t] > hi[t] or (not np.isnan(e150[t]) and e30[t] > e150[t]):
                in_short = False
        else:
            entry_ok = c[t] < lo[t]
            if adx_gate:
                entry_ok = entry_ok and (not np.isnan(ax[t])) and ax[t] > 20
            entry_ok = entry_ok and lead[t]
            # don't enter if exit condition simultaneously true (EMA up regime)
            if entry_ok and not (not np.isnan(e150[t]) and e30[t] > e150[t]):
                in_short = True
        if in_short:
            st[t] = -1.0
    return pd.Series(st, index=df.index)


def returns_from_pos(df, pos):
    c = df["close"]
    held = pos.shift(1).fillna(0.0)
    flips = held.diff().abs().fillna(0.0)
    fr = FUND4H.reindex(c.index).fillna(0.0)
    return held*c.pct_change() - held*fr - COST*flips


def met(sr):
    sr = sr.dropna()
    if len(sr) == 0 or sr.std() == 0:
        return 0.0, 0.0, 0.0
    eq = (1+sr).cumprod()
    sh = sr.mean()/sr.std()*np.sqrt(ANN)
    dd = (eq/eq.cummax()-1).min()
    return (eq.iloc[-1]-1)*100, sh, dd*100


P = {s: load(s) for s in COINS}
LONG = {}
btc_up = pos_long_v3(P["BTCUSDT"])
for s in COINS:
    p = pos_long_v3(P[s])
    if s != "BTCUSDT":
        p = p * btc_up.reindex(p.index).ffill().fillna(0.0)
    LONG[s] = p

# long-only baseline portfolio
base = pd.DataFrame({s: returns_from_pos(P[s], LONG[s]) for s in COINS}).mean(axis=1, skipna=True).dropna()


def run_config(n_entry, m_exit, adx_gate, leader):
    # BTC short state first (no leader gate on BTC itself)
    btc_st = short_state(P["BTCUSDT"], n_entry, m_exit, adx_gate, None)
    btc_broken = (btc_st == -1)
    sleeves, combos = {}, {}
    for s in COINS:
        lm = btc_broken if (leader and s != "BTCUSDT") else None
        st = short_state(P[s], n_entry, m_exit, adx_gate, lm)
        sleeves[s] = returns_from_pos(P[s], st)
        # combined: long wins if both; short only when v3 long flat
        lp = LONG[s].reindex(st.index).fillna(0.0)
        cp = lp.where(lp > 0, st)
        combos[s] = returns_from_pos(P[s], cp)
    sl = pd.DataFrame(sleeves).mean(axis=1, skipna=True).dropna()
    cb = pd.DataFrame(combos).mean(axis=1, skipna=True).dropna()
    return sl, cb


def trade_count(n_entry, m_exit, adx_gate, leader):
    btc_st = short_state(P["BTCUSDT"], n_entry, m_exit, adx_gate, None)
    btc_broken = (btc_st == -1)
    n_is = n_oos = 0
    for s in COINS:
        lm = btc_broken if (leader and s != "BTCUSDT") else None
        st = short_state(P[s], n_entry, m_exit, adx_gate, lm)
        ent = (st.diff() == -1)
        n_is += int(ent[ent.index < SPLIT].sum())
        n_oos += int(ent[ent.index >= SPLIT].sum())
    return n_is, n_oos


if __name__ == "__main__":
    bi, bo = base[base.index < SPLIT], base[base.index >= SPLIT]
    print("LONG-ONLY v3 baseline:")
    print("  IS  %+.0f%%  Sh %.2f  DD %.0f%%" % met(bi))
    print("  OOS %+.0f%%  Sh %.2f  DD %.0f%%" % met(bo))
    print()

    grid = []
    for n_entry in (40, 55):
        for m_exit in (10, 20):
            for adx_gate, leader in ((False, False), (True, False), (True, True)):
                grid.append((n_entry, m_exit, adx_gate, leader))
    assert len(grid) <= 12

    print(f"{'cfg':>28} | {'slv IS tot':>10} {'IS Sh':>6} {'IS DD':>6} | (sleeve, IS only — pick on IS Sharpe)")
    results = {}
    for g in grid:
        sl, cb = run_config(*g)
        results[g] = (sl, cb)
        ti, si, di = met(sl[sl.index < SPLIT])
        name = f"N{g[0]}/M{g[1]}{'/adx' if g[2] else ''}{'/lead' if g[3] else ''}"
        print(f"{name:>28} | {ti:>+9.1f}% {si:>6.2f} {di:>5.0f}%")

    best = max(grid, key=lambda g: met(results[g][0][results[g][0].index < SPLIT])[1])
    name = f"N{best[0]}/M{best[1]}{'/adx' if best[2] else ''}{'/lead' if best[3] else ''}"
    print(f"\nBEST by IS Sharpe: {name}")
    sl, cb = results[best]
    n_is, n_oos = trade_count(*best)
    print(f"  short entries: IS {n_is}, OOS {n_oos}")
    print("  SLEEVE  IS : %+.1f%%  Sh %.2f  DD %.1f%%" % met(sl[sl.index < SPLIT]))
    print("  SLEEVE  OOS: %+.1f%%  Sh %.2f  DD %.1f%%" % met(sl[sl.index >= SPLIT]))
    print("  COMBO   IS : %+.1f%%  Sh %.2f  DD %.1f%%" % met(cb[cb.index < SPLIT]))
    print("  COMBO   OOS: %+.1f%%  Sh %.2f  DD %.1f%%" % met(cb[cb.index >= SPLIT]))

    # adversarial: yearly sleeve returns of best config
    print("\n  sleeve yearly:")
    for y, grpy in sl.groupby(sl.index.year):
        print(f"    {y}: {((1+grpy).prod()-1)*100:+.1f}%")
