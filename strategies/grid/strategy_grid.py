"""
strategy_grid.py — Directional Grid Strategy backtest.

Long grid in uptrend + Short grid in downtrend.
$5K → $831K | +126% CAGR | -25.3% DD | PF 2.64 | 240 trades/yr | 76% WR
"""
import os, numpy as np, pandas as pd
from grid_core import *

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def load_15m():
    df = pd.read_csv(os.path.join(ROOT, "data", "cache", "BTCUSDT_15m_1825d.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    if df["timestamp"].dt.tz is not None:
        df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    for c in ["open","high","low","close","volume"]: df[c] = df[c].astype(float)
    return df.sort_values("timestamp").reset_index(drop=True)

def run():
    src = load_15m()
    print(f"Source: {len(src):,} 15m bars")

    df = build_signals(src)
    print(f"1H bars: {len(df):,}")

    closes = df["close"].values; highs = df["high"].values; lows = df["low"].values
    FEE = 0.0004; SLIP = 0.0003; START = 5000

    cap = START; peak = cap; mdd = 0; trades = []; yearly = {}
    grid_on = False; grid_side = 0; grid_levels = []; grid_filled = []
    sb = 0; grid_base = 0

    for i in range(200, len(df)):
        price = closes[i]; yr = df.iloc[i]["timestamp"].year
        if yr not in yearly: yearly[yr] = {"start":cap,"end":cap,"t":[]}
        yr_r = yearly[yr]
        rsi_v = df.iloc[i]["rsi"]; ema_v = df.iloc[i]["ema200"]
        if pd.isna(rsi_v) or pd.isna(ema_v): continue

        if grid_on:
            profit = 0
            if grid_side == 1:
                for g in range(len(grid_levels)):
                    buy_px = grid_levels[g]; sell_px = buy_px * (1 + TP_PER_GRID_PCT)
                    if not grid_filled[g] and lows[i] <= buy_px: grid_filled[g] = True
                    if grid_filled[g] and highs[i] >= sell_px:
                        pnl = (sell_px - buy_px) / buy_px; alloc = 1.0 / N_GRIDS
                        profit += pnl * LEVERAGE * alloc - (FEE+SLIP) * 2 * LEVERAGE * alloc
                        grid_filled[g] = False
            else:
                for g in range(len(grid_levels)):
                    sell_px = grid_levels[g]; buy_px = sell_px * (1 - TP_PER_GRID_PCT)
                    if not grid_filled[g] and highs[i] >= sell_px: grid_filled[g] = True
                    if grid_filled[g] and lows[i] <= buy_px:
                        pnl = (sell_px - buy_px) / sell_px; alloc = 1.0 / N_GRIDS
                        profit += pnl * LEVERAGE * alloc - (FEE+SLIP) * 2 * LEVERAGE * alloc
                        grid_filled[g] = False

            if profit != 0:
                cap *= (1 + profit); trades.append(profit * 100); yr_r["t"].append(profit * 100)

            should_stop = False
            if grid_side == 1 and rsi_v > RSI_LONG_EXIT: should_stop = True
            if grid_side == -1 and rsi_v < RSI_SHORT_EXIT: should_stop = True
            if GRID_SL_PCT:
                if grid_side == 1 and price < grid_base * (1 - GRID_SL_PCT): should_stop = True
                if grid_side == -1 and price > grid_base * (1 + GRID_SL_PCT): should_stop = True
            if (i - sb) > MAX_HOLD_BARS: should_stop = True
            if grid_side == 1 and price < ema_v: should_stop = True
            if grid_side == -1 and price > ema_v: should_stop = True

            if should_stop:
                close_pnl = 0
                for g in range(len(grid_levels)):
                    if grid_filled[g]:
                        if grid_side == 1: pnl = (price - grid_levels[g]) / grid_levels[g]
                        else: pnl = (grid_levels[g] - price) / grid_levels[g]
                        alloc = 1.0 / N_GRIDS
                        close_pnl += pnl * LEVERAGE * alloc - (FEE+SLIP) * 2 * LEVERAGE * alloc
                if close_pnl != 0:
                    cap *= (1 + close_pnl); trades.append(close_pnl * 100); yr_r["t"].append(close_pnl * 100)
                grid_on = False; grid_levels = []; grid_filled = []
        else:
            if price > ema_v and rsi_v < RSI_LONG_TRIGGER:
                grid_on = True; grid_side = 1; sb = i; grid_base = price
                grid_low = price * (1 - GRID_RANGE_PCT)
                spacing = (price - grid_low) / N_GRIDS
                grid_levels = [grid_low + spacing * j for j in range(N_GRIDS)]
                grid_filled = [False] * N_GRIDS; grid_filled[-1] = True
            elif price < ema_v and rsi_v > RSI_SHORT_TRIGGER:
                grid_on = True; grid_side = -1; sb = i; grid_base = price
                grid_high = price * (1 + GRID_RANGE_PCT)
                spacing = (grid_high - price) / N_GRIDS
                grid_levels = [price + spacing * j for j in range(N_GRIDS)]
                grid_filled = [False] * N_GRIDS; grid_filled[0] = True

        peak = max(peak, cap); dd = (cap - peak) / peak * 100
        if dd < mdd: mdd = dd
        yr_r["end"] = cap

    yrs = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[200]).days / 365.25
    n = len(trades)
    wins = [t for t in trades if t > 0]; losses = [t for t in trades if t <= 0]
    wr = len(wins) / max(n, 1) * 100
    gw = sum(wins); gl = abs(sum(losses)); pf = gw / max(gl, 1e-9)
    cagr = ((cap / START) ** (1 / yrs) - 1) * 100 if cap > 0 else 0

    print(f"\n{'='*68}")
    print(f"  DIRECTIONAL GRID — Long uptrend + Short downtrend")
    print(f"  {N_GRIDS} grids | {GRID_RANGE_PCT*100:.0f}% range | {TP_PER_GRID_PCT*100:.0f}% TP/grid | {GRID_SL_PCT*100:.0f}% SL")
    print(f"  Trend: EMA{EMA_TREND_PERIOD} | Entry: RSI <{RSI_LONG_TRIGGER}/>{ RSI_SHORT_TRIGGER}")
    print(f"  lev={LEVERAGE}x")
    print(f"{'='*68}")
    print(f"  Start → Final:  ${START:,.0f} → ${cap:,.2f}  ({(cap/START-1)*100:+.1f}%)")
    print(f"  CAGR:           {cagr:+.1f}%")
    print(f"  Max drawdown:   {mdd:+.1f}%")
    print(f"  Trades:         {n}  (~{n/yrs:.0f}/yr)")
    print(f"  Win rate:       {wr:.1f}%  ({len(wins)}W/{len(losses)}L)")
    print(f"  Profit factor:  {pf:.2f}")

    print(f"\n  Year-by-year:")
    print(f"  {'Year':<6}{'Start':>12}{'End':>12}{'Ret%':>9}{'N':>6}")
    for y in sorted(yearly.keys()):
        d = yearly[y]; ret = (d["end"]/d["start"]-1)*100 if d["start"]>0 else 0
        print(f"  {y:<6}${d['start']:>10,.0f}${d['end']:>10,.0f}{ret:>+9.1f}{len(d['t']):>6}")


if __name__ == "__main__":
    run()
