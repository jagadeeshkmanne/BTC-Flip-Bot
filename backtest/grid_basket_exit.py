"""grid_basket_exit.py — fresh, honest test of: 'open a grid, take many small
grid trades, close EVERYTHING when overall P&L is positive, restart.'

Deliberately GENEROUS assumptions (tilted toward the strategy):
  - spot, 1x, no liquidation possible
  - grid fills at exact level prices as MAKER (0.05%/side)
  - basket exit at close price, taker 0.10%
  - fills evaluated on 5m closes (no wick ambiguity)

Mechanics:
  - invest $5,000, N levels spaced s% below current price, equal USDT per level
  - price crosses a level -> buy that level's lot; lot sells at its buy price +s%
  - every bar: equity = cash + inventory x close
    if equity >= cycle_start_equity x (1 + basket_target): SELL ALL, restart
      grid centered at current price (this is the user's 'overall grid profit' exit)
  - report: final equity, max MTM drawdown, longest underwater stretch,
    completed basket cycles, worst floating loss
"""
import pandas as pd
import numpy as np

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"
INVEST = 5000.0
FEE_MAKER = 0.0005
FEE_TAKER = 0.0010


def run(closes, ts, spacing, n_levels, target):
    cash = INVEST
    cycle_base = INVEST
    p0 = closes[0]
    levels = [p0 * (1 - spacing * (i + 1)) for i in range(n_levels)]
    lot_usdt = INVEST / n_levels
    filled = {}          # level_idx -> (qty, buy_px)
    cycles = 0
    eq_hist = np.empty(len(closes))
    worst_float = 0.0

    for i in range(len(closes)):
        c = closes[i]
        # sells: lot exits one spacing above its own buy price (maker)
        for k in list(filled.keys()):
            qty, bpx = filled[k]
            tgt = bpx * (1 + spacing)
            if c >= tgt:
                cash += qty * tgt * (1 - FEE_MAKER)
                del filled[k]
        # buys: any unfilled level at/above current close (maker)
        for k in range(n_levels):
            if k not in filled and c <= levels[k] and cash >= lot_usdt:
                px = levels[k]
                qty = (lot_usdt * (1 - FEE_MAKER)) / px
                cash -= lot_usdt
                filled[k] = (qty, px)
        inv_qty = sum(q for q, _ in filled.values())
        inv_cost = sum(q * p for q, p in filled.values())
        equity = cash + inv_qty * c
        if inv_qty > 0:
            worst_float = min(worst_float, inv_qty * c - inv_cost)
        # USER'S RULE: exit when OVERALL profit target reached -> restart grid
        if equity >= cycle_base * (1 + target) and inv_qty >= 0:
            if inv_qty > 0:
                cash += inv_qty * c * (1 - FEE_TAKER)
                filled = {}
            equity = cash
            cycles += 1
            cycle_base = equity
            p0 = c
            levels = [p0 * (1 - spacing * (j + 1)) for j in range(n_levels)]
            lot_usdt = equity / n_levels
        eq_hist[i] = equity

    eq = pd.Series(eq_hist, index=ts)
    peak = eq.cummax()
    dd = ((peak - eq) / peak).max() * 100
    under = eq < peak * 0.999
    # longest underwater stretch in days
    longest = 0; cur = 0
    uv = under.values
    for u in uv:
        cur = cur + 1 if u else 0
        longest = max(longest, cur)
    longest_days = longest * 5 / 60 / 24
    return {"final": eq.iloc[-1], "dd": dd, "under_days": longest_days,
            "cycles": cycles, "worst_float": worst_float,
            "inv_left": sum(q for q, _ in filled.values())}


def main():
    df = pd.read_csv(CSV, parse_dates=["timestamp"])
    closes = df["close"].values
    ts = df["timestamp"].values
    print(f"Window: {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}"
          f"   invest ${INVEST:,.0f}   buy&hold: {closes[-1]/closes[0]*INVEST:,.0f}\n")
    print(f"{'spacing':>8} {'levels':>7} {'range':>7} {'target':>7} | "
          f"{'final $':>9} {'ret%':>7} {'maxDD%':>7} {'underwater':>11} {'cycles':>7} {'worstFloat':>11}")
    for spacing, n in [(0.005, 20), (0.01, 20), (0.01, 40), (0.02, 20)]:
        for target in [0.01, 0.02, 0.05]:
            r = run(closes, ts, spacing, n, target)
            rng = spacing * n * 100
            print(f"{spacing*100:>7.1f}% {n:>7} {rng:>6.0f}% {target*100:>6.0f}% | "
                  f"{r['final']:>9,.0f} {(r['final']/INVEST-1)*100:>+6.1f}% {r['dd']:>6.1f}% "
                  f"{r['under_days']:>8.0f} d {r['cycles']:>7} {r['worst_float']:>10,.0f}")


if __name__ == "__main__":
    main()
