#!/usr/bin/env python3
"""momo_lev_sweep.py — honest leverage sweep of MOMO v1 toward the 1%/day goal.

Question: take the ONE honest survivor (momo_v1: daily close>SMA200 AND
RSI14>70 within 7d, long/flat) and execute it on perps at leverage L,
parking flat-day capital in funding harvest. What is the maximum honest
geometric daily growth rate, and at what leverage does it peak?

Honest checklist (FINDINGS.md):
  1. Signals on CLOSED bars only; fills at NEXT bar open.
  2. Liquidation checked against intraday low (long): liq when the day's low
     breaches entry*(1 - 1/L + MMR). Liquidation loses the full margin.
  3. Liquidation and exit-signal same bar -> take the liquidation (pessimistic).
  4. Perp taker fee 0.055% per side on NOTIONAL (= L x margin).
  5. Funding paid while long: 0.01%/8h (10.95% APR) on notional — the
     long-run BTC perp average; longs normally pay.
  6. Mark-to-market equity daily; maxDD on MTM, not closed balance.
  7. Year-wise + IS/OOS halves reported.

Flat-day income (funding harvest, FINDINGS.md #5): +8% APR on equity,
reported both with and without.
"""
import pandas as pd
import numpy as np

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1d.csv"
SMA_LEN, RSI_LEN, RSI_TRIG, HOLD = 200, 14, 70.0, 7
FEE = 0.00055          # perp taker per side, on notional
FUND_LONG_D = 0.0003   # funding paid per day while long, on notional (0.01%/8h)
HARVEST_D = 0.08 / 365 # funding-harvest income per day on equity while flat
MMR = 0.005            # maintenance margin
START = 5000.0


def wilder_rsi(close, n):
    d = close.diff()
    ag = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + ag / al)


def run(df, L, harvest=True):
    eq = START
    peak, maxdd = eq, 0.0
    pos = None  # dict(entry, margin, notional_qty, liq)
    liqs = trades = 0
    daily_eq = []  # (date, equity)
    for i in range(SMA_LEN + 1, len(df)):
        bar = df.iloc[i]          # today's bar: we trade at its open
        prev = df.iloc[i - 1]     # last closed bar = signal bar
        target_long = bool(prev["sig"])

        if pos is None and target_long and eq > 1:
            margin = eq
            notional = margin * L
            entry = bar["open"]
            fee = notional * FEE
            qty = notional / entry
            liq_px = entry * (1 - 1 / L + MMR)
            eq -= fee
            pos = {"entry": entry, "margin": eq, "qty": qty, "liq": liq_px}
            trades += 1
            # liquidation possible on entry day
            if bar["low"] <= liq_px:
                eq = 0.0
                pos = None
                liqs += 1
        elif pos is not None:
            # pessimistic: check liquidation before honoring an exit signal
            if bar["low"] <= pos["liq"]:
                eq = 0.0
                pos = None
                liqs += 1
            elif not target_long:
                px = bar["open"]
                pnl = pos["qty"] * (px - pos["entry"])
                fee = pos["qty"] * px * FEE
                eq = pos["margin"] + pnl - fee
                pos = None

        if pos is not None:
            # funding paid on notional, MTM at close
            eq_close = pos["margin"] + pos["qty"] * (bar["close"] - pos["entry"])
            fund = pos["qty"] * bar["close"] * FUND_LONG_D
            pos["margin"] -= fund
            eq = max(eq_close - fund, 0.0)
            if eq <= 0:
                pos = None
                liqs += 1
        elif harvest and eq > 0:
            eq *= 1 + HARVEST_D

        peak = max(peak, eq)
        if peak > 0:
            maxdd = max(maxdd, 1 - eq / peak)
        daily_eq.append((bar["timestamp"], eq))
        if eq <= 0:
            break

    n_days = len(daily_eq)
    final = daily_eq[-1][1]
    g_daily = (final / START) ** (1 / n_days) - 1 if final > 0 else -1.0
    return {"L": L, "final": final, "g_daily_pct": g_daily * 100,
            "cagr_pct": ((final / START) ** (365 / n_days) - 1) * 100 if final > 0 else -100,
            "maxdd_pct": maxdd * 100, "trades": trades, "liqs": liqs,
            "daily_eq": daily_eq}


def main():
    df = pd.read_csv(CSV)
    df["sma"] = df["close"].rolling(SMA_LEN).mean()
    df["rsi"] = wilder_rsi(df["close"], RSI_LEN)
    df["sig"] = (df["close"] > df["sma"]) & (
        df["rsi"].rolling(HOLD).max() > RSI_TRIG)

    print(f"data: {df['timestamp'].iloc[0]} .. {df['timestamp'].iloc[-1]}  ({len(df)} days)")
    print(f"{'L':>4} {'harv':>5} {'final$':>12} {'g/day%':>8} {'CAGR%':>8} "
          f"{'maxDD%':>7} {'trades':>6} {'liqs':>4}")
    results = []
    for L in [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]:
        for harvest in (False, True):
            r = run(df, L, harvest)
            results.append((harvest, r))
            print(f"{L:>4} {str(harvest):>5} {r['final']:>12,.0f} "
                  f"{r['g_daily_pct']:>8.4f} {r['cagr_pct']:>8.1f} "
                  f"{r['maxdd_pct']:>7.1f} {r['trades']:>6} {r['liqs']:>4}")

    # year-wise for the best harvest=True cell that never liquidated
    ok = [r for h, r in results if h and r["liqs"] == 0 and r["final"] > 0]
    if ok:
        best = max(ok, key=lambda r: r["g_daily_pct"])
        print(f"\nbest never-liquidated (harvest on): L={best['L']}  "
              f"g/day={best['g_daily_pct']:.4f}%  CAGR={best['cagr_pct']:.1f}%  "
              f"maxDD={best['maxdd_pct']:.1f}%")
        de = pd.DataFrame(best["daily_eq"], columns=["ts", "eq"])
        de["year"] = pd.to_datetime(de["ts"]).dt.year
        first = de.groupby("year")["eq"].first()
        last = de.groupby("year")["eq"].last()
        print("year-wise:", {int(y): f"{(last[y] / first[y] - 1) * 100:+.1f}%" for y in first.index})
        half = len(de) // 2
        h1 = de["eq"].iloc[half - 1] / de["eq"].iloc[0] - 1
        h2 = de["eq"].iloc[-1] / de["eq"].iloc[half - 1] - 1
        print(f"halves: H1 {h1 * 100:+.1f}%  H2 {h2 * 100:+.1f}%")


if __name__ == "__main__":
    main()
