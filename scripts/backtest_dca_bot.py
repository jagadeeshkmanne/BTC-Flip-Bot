#!/usr/bin/env python3
"""backtest_dca_bot.py — pure DCA bot (3Commas-style) on BTC 1h, HONEST capital accounting.

Long DCA deal:
  - base order at market; place a ladder of safety orders (SOs) below.
  - each SO triggers when price falls to its level (deviation from base, spaced by step_scale);
    SO sizes grow by volume_scale. Filling SOs averages the entry DOWN.
  - take profit when price >= avg_entry * (1 + tp%): close the whole position, bank profit,
    immediately start a new deal.
  - if all SOs are used and price keeps falling, the deal sits in an unrealised "bag" until
    price recovers to TP (or the backtest ends).

HONEST: return measured on the FULL reserved budget (base + every SO) — the capital you must
set aside so the ladder never runs dry / liquidates. (Measuring on the base order only is the
common DCA-backtest lie.) Fills on real intrabar high/low, fee+slippage per side.

Reports: return on budget, max equity drawdown, deals closed, win%, max SOs used, and the
deepest unrealised bag — the risk that actually matters.
"""
from __future__ import annotations
import os
import numpy as np
import pandas as pd

FEE_PCT = 0.00055
SLIP_PCT = 0.0005
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(tf):
    if tf == "1h":
        return pd.read_csv(os.path.join(HERE, "data/cache/BTCUSDT_1h_binance_full.csv"), parse_dates=["timestamp"])
    df = pd.read_csv(os.path.join(HERE, "data/cache/BTCUSDT_1h_binance_full.csv"), parse_dates=["timestamp"])
    return df.set_index("timestamp").resample(tf).agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()


def ladder(base_px, dev, step_scale, vol_scale, max_so, base_size, so_size):
    """Return list of (trigger_price, usd_size) for the safety orders."""
    levels = []
    cum_dev = 0.0; spacing = dev; size = so_size
    for k in range(max_so):
        cum_dev += spacing
        levels.append((base_px * (1 - cum_dev / 100.0), size))
        spacing *= step_scale
        size *= vol_scale
    return levels


def run(df, tp, dev, step_scale, vol_scale, max_so, base_size=100.0, so_size=100.0):
    budget = base_size + sum(s for _, s in ladder(100, dev, step_scale, vol_scale, max_so, base_size, so_size))
    cash = budget
    o = df["open"].values; h = df["high"].values; l = df["low"].values; c = df["close"].values
    n = len(df)

    in_deal = False; qty = 0.0; cost = 0.0; sos = []  # remaining SO list
    eq = np.empty(n); deals = wins = 0; max_so_used = 0; worst_bag = 0.0

    def buy(px, usd):
        nonlocal cash, qty, cost
        fill = px * (1 + SLIP_PCT); q = usd / fill
        cash -= usd * (1 + FEE_PCT); qty += q; cost += usd

    for i in range(n):
        px_o, px_h, px_l, px_c = o[i], h[i], l[i], c[i]
        if not in_deal:
            buy(px_o, base_size)
            base_px = px_o * (1 + SLIP_PCT)
            sos = ladder(base_px, dev, step_scale, vol_scale, max_so, base_size, so_size)
            in_deal = True; used = 0
        else:
            # fill any SOs whose trigger was reached intrabar (price fell to them)
            while sos and px_l <= sos[0][0]:
                trig, usd = sos.pop(0)
                buy(trig, usd); used += 1
            max_so_used = max(max_so_used, used)
            avg = cost / qty
            # take profit if price rose to TP intrabar
            tp_px = avg * (1 + tp / 100.0)
            if px_h >= tp_px:
                fill = tp_px * (1 - SLIP_PCT)
                cash += qty * fill * (1 - FEE_PCT)
                deals += 1; wins += 1
                qty = 0.0; cost = 0.0; in_deal = False; sos = []
            else:
                worst_bag = min(worst_bag, (px_l - avg) / avg)  # deepest unrealised excursion
        eq[i] = cash + qty * px_c

    # mark any open deal at the end
    eqs = pd.Series(eq, index=pd.to_datetime(df["timestamp"]))
    ret = (eqs.iloc[-1] / budget - 1) * 100
    dd = (eqs / eqs.cummax() - 1).min() * 100
    yrs = (eqs.index[-1] - eqs.index[0]).days / 365.25
    cagr = ((eqs.iloc[-1] / budget) ** (1 / max(yrs, 1e-9)) - 1) * 100
    return dict(ret=ret, cagr=cagr, dd=dd, deals=deals, max_so=max_so_used,
                worst_bag=worst_bag * 100, open_end=in_deal, budget=budget)


def main():
    df = load("1h")
    span = f"{df.timestamp.iloc[0].date()}->{df.timestamp.iloc[-1].date()}"
    bh = (df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100
    print("=" * 100)
    print(f"BTC 1h — PURE DCA BOT (honest, return on full reserved budget)  {span}  | buy&hold {bh:.0f}%")
    print("=" * 100)
    print(f"  {'config (tp/dev/steps/maxSO)':<34}{'CAGR%':>7}{'totRet%':>9}{'maxDD%':>8}{'deals':>7}{'maxSO':>6}{'worstBag%':>10}{'openEnd':>8}")
    configs = [
        ("tp1.0 dev1.0 step1.0 vol1.5 so5", dict(tp=1.0, dev=1.0, step_scale=1.0, vol_scale=1.5, max_so=5)),
        ("tp1.5 dev1.5 step1.2 vol1.5 so5", dict(tp=1.5, dev=1.5, step_scale=1.2, vol_scale=1.5, max_so=5)),
        ("tp2.0 dev2.0 step1.3 vol1.6 so6", dict(tp=2.0, dev=2.0, step_scale=1.3, vol_scale=1.6, max_so=6)),
        ("tp1.0 dev1.2 step1.3 vol1.8 so8", dict(tp=1.0, dev=1.2, step_scale=1.3, vol_scale=1.8, max_so=8)),
        ("tp2.5 dev2.5 step1.5 vol2.0 so6", dict(tp=2.5, dev=2.5, step_scale=1.5, vol_scale=2.0, max_so=6)),
        ("tp3.0 dev3.0 step1.5 vol2.0 so7", dict(tp=3.0, dev=3.0, step_scale=1.5, vol_scale=2.0, max_so=7)),
    ]
    for name, kw in configs:
        r = run(df, **kw)
        print(f"  {name:<34}{r['cagr']:>7.1f}{r['ret']:>9.0f}{r['dd']:>8.0f}{r['deals']:>7d}{r['max_so']:>6d}"
              f"{r['worst_bag']:>10.0f}{('OPEN' if r['open_end'] else '-'):>8}")
    print("\n  worstBag% = deepest unrealised loss vs average entry (the bag-holding risk).")
    print("  openEnd=OPEN means a deal never recovered to TP by the end (stuck bag).")


if __name__ == "__main__":
    main()
