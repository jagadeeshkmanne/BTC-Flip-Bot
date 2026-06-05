"""Gemini v1 OOS backtest — baseline + variant flags.

Reimplements bot_gemini.py's entry/exit logic so we can A/B the proposed
improvements (post-win cooldown + volume gate) against the current code
across 29 months of OOS data before committing to a v2 build.

Variants tested (via flags):
  --variant baseline     v1 as it runs today
  --variant cooldown     v1 + 3-bar cooldown after WINS too (not just losses)
  --variant volume       v1 + require vol[i] > 1.1 × SMA(volume, 20)
  --variant both         both filters

Match to live v1:
  - 5m bars only
  - Regime: BULL / BEAR / RANGE / SQUEEZE (per bot_gemini.classify_regime)
  - BULL/BEAR entries: pullback (any of last 4 bars touched EMA20) + RSI hook
    + green/red close
  - RANGE entries: BB band extreme + RSI 30/70 hook + green/red close
  - SQUEEZE: no entries
  - Risk-based sizing 0.5%, SL cap 0.60%, 3× leverage cap
  - SL: BULL/BEAR = swing low/high of last 6 bars (capped); RANGE = fixed 0.4%
  - TP: BULL/BEAR = upper/lower BB band; RANGE = BB basis
  - 1-loss → 15-min (3 bars) cooldown (live PaperBook setting)
  - Worst-case intra-bar: if SL and TP both hit same bar, SL wins
  - Costs: 0.04% commission per side, no slippage modeled
"""
from __future__ import annotations
import argparse
import sys
from calendar import monthrange
from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
CACHE_5M = REPO / "data" / "cache" / "BTCUSDT_5m.csv"

# Match live v1 params exactly
INITIAL_CAPITAL   = 5000.0
RISK_PCT          = 0.005     # 0.5%
SL_CAP            = 0.006     # 0.60%
RANGE_SL          = 0.004     # 0.40%
LEVERAGE          = 3.0
COMMISSION        = 0.0004    # per side
EMA_SLOPE_BARS    = 24        # 2h
FLAT_SLOPE        = 0.0012    # 0.12%
SQUEEZE_BW        = 0.008
PULLBACK_LOOKBACK = 4
SWING_LOOKBACK    = 6
BREAKER_LOSSES    = 1
BREAKER_BARS      = 3         # 15 min ≈ 3 × 5m

# Variant flags (set in main)
USE_WIN_COOLDOWN  = False     # variant: 3-bar cooldown after WINS too
WIN_COOLDOWN_BARS = 3
USE_VOLUME_GATE   = False     # variant: require vol > 1.1 × SMA(20)
VOL_MULT          = 1.1


# ── Indicators ──
def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()
def rsi(s, n=7):
    d = s.diff()
    g = d.where(d > 0, 0.0).ewm(alpha=1.0/n, adjust=False).mean()
    l = (-d.where(d < 0, 0.0)).ewm(alpha=1.0/n, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - 100 / (1 + rs)
def bollinger(s, n=20, k=2.0):
    m = s.rolling(n).mean()
    sd = s.rolling(n).std()
    return m - k*sd, m, m + k*sd


def load_5m(start_iso: str, end_iso: str) -> pd.DataFrame:
    df = pd.read_csv(CACHE_5M, parse_dates=["timestamp"])
    df = df[(df.timestamp >= start_iso) & (df.timestamp <= end_iso)].reset_index(drop=True)
    return df


def classify_regime(df, i):
    """Return regime at bar i — matches bot_gemini.classify_regime."""
    if i < EMA_SLOPE_BARS + 1: return "WARMUP"
    c = df.close.iat[i]; e20 = df.ema20.iat[i]; e200 = df.ema200.iat[i]
    e20_prev = df.ema20.iat[i - EMA_SLOPE_BARS]; e200_prev = df.ema200.iat[i - EMA_SLOPE_BARS]
    if e200_prev == 0: return "WARMUP"
    e20_up = e20 > e20_prev
    e200_slope = abs(e200 / e200_prev - 1)
    bb_low = df.bb_low.iat[i]; bb_mid = df.bb_mid.iat[i]; bb_up = df.bb_up.iat[i]
    if bb_mid == 0: return "WARMUP"
    bw = (bb_up - bb_low) / bb_mid
    if e200_slope < FLAT_SLOPE:
        return "SQUEEZE" if bw < SQUEEZE_BW else "RANGE"
    if c > e200 and e20 > e200 and e20_up: return "BULL"
    if c < e200 and e20 < e200 and not e20_up: return "BEAR"
    return "RANGE" if bw >= SQUEEZE_BW else "SQUEEZE"


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    side: str
    regime: str
    entry: float
    exit: float
    qty: float
    pnl_pct: float
    reason: str


def run_backtest(df: pd.DataFrame) -> tuple[list[Trade], float, float]:
    """Single-pass backtest. Returns (trades, final_equity, max_dd_pct)."""
    # Indicators
    df = df.copy()
    df["ema20"]  = ema(df.close, 20)
    df["ema200"] = ema(df.close, 200)
    df["rsi"]    = rsi(df.close, 7)
    df["bb_low"], df["bb_mid"], df["bb_up"] = bollinger(df.close, 20, 2.0)
    if USE_VOLUME_GATE:
        df["vol_sma20"] = sma(df.volume, 20)

    equity = INITIAL_CAPITAL
    peak = equity
    max_dd = 0.0
    trades: list[Trade] = []
    pos = None
    cooldown_until_bar = -1
    n = len(df)

    for i in range(max(220, EMA_SLOPE_BARS + 1), n):
        # ── Manage open position ──
        if pos:
            h = df.high.iat[i]; l = df.low.iat[i]
            sl_hit = (pos["side"] == "LONG"  and l <= pos["sl"]) or \
                     (pos["side"] == "SHORT" and h >= pos["sl"])
            tp_hit = (pos["side"] == "LONG"  and h >= pos["tp"]) or \
                     (pos["side"] == "SHORT" and l <= pos["tp"])
            if sl_hit:  # SL wins ties (conservative)
                exit_px = pos["sl"]; reason = "SL"
            elif tp_hit:
                exit_px = pos["tp"]; reason = "TP"
            else:
                continue
            gross = (exit_px - pos["entry"]) * pos["qty"] if pos["side"] == "LONG" \
                    else (pos["entry"] - exit_px) * pos["qty"]
            fees = (pos["entry"] + exit_px) * pos["qty"] * COMMISSION
            net = gross - fees
            pnl_pct = net / equity * 100
            equity += net
            trades.append(Trade(
                entry_time=pos["entry_time"], exit_time=df.timestamp.iat[i],
                side=pos["side"], regime=pos["regime"], entry=pos["entry"],
                exit=exit_px, qty=pos["qty"], pnl_pct=pnl_pct, reason=reason))
            # Equity / DD bookkeeping
            if equity > peak: peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd: max_dd = dd
            # Cooldown logic
            if net <= 0:
                cooldown_until_bar = i + BREAKER_BARS   # loss → always cooldown
            elif USE_WIN_COOLDOWN:
                cooldown_until_bar = i + WIN_COOLDOWN_BARS  # variant: also after wins
            pos = None
            continue

        # ── No position: check cooldown + entry ──
        if i < cooldown_until_bar: continue

        regime = classify_regime(df, i)
        if regime in ("SQUEEZE", "WARMUP"): continue

        # Volume gate (variant)
        if USE_VOLUME_GATE:
            v = df.volume.iat[i]; vsma = df.vol_sma20.iat[i]
            if not (vsma > 0 and v > vsma * VOL_MULT): continue

        rsi_now = df.rsi.iat[i]; rsi_prev = df.rsi.iat[i-1]
        if not (np.isfinite(rsi_now) and np.isfinite(rsi_prev)): continue
        open_px = df.open.iat[i]; close_px = df.close.iat[i]
        green = close_px > open_px; red = close_px < open_px
        e20 = df.ema20.iat[i]

        # Pullback detection (last 4 bars)
        pulled_long = (df.low.iloc[i-PULLBACK_LOOKBACK:i+1].values <=
                       df.ema20.iloc[i-PULLBACK_LOOKBACK:i+1].values).any()
        rallied_short = (df.high.iloc[i-PULLBACK_LOOKBACK:i+1].values >=
                         df.ema20.iloc[i-PULLBACK_LOOKBACK:i+1].values).any()

        side = None; sl = None; tp = None
        if regime == "BULL":
            if pulled_long and rsi_prev <= 50 and rsi_now > rsi_prev and green:
                side = "LONG"
                entry = close_px
                swing = df.low.iloc[i-SWING_LOOKBACK:i+1].min()
                # Match live: sl = min(swing, entry × 0.999), then capped at entry × 0.994
                sl = min(swing, entry * (1 - 0.001))    # at least 0.1% below
                sl = max(sl, entry * (1 - SL_CAP))      # at most 0.6% below
                tp = df.bb_up.iat[i]
        elif regime == "BEAR":
            if rallied_short and rsi_prev >= 50 and rsi_now < rsi_prev and red:
                side = "SHORT"
                entry = close_px
                swing = df.high.iloc[i-SWING_LOOKBACK:i+1].max()
                sl = max(swing, entry * (1 + 0.001))    # at least 0.1% above
                sl = min(sl, entry * (1 + SL_CAP))      # at most 0.6% above
                tp = df.bb_low.iat[i]
        elif regime == "RANGE":
            prev_close = df.close.iat[i-1]
            prev_bb_low = df.bb_low.iat[i-1]; prev_bb_up = df.bb_up.iat[i-1]
            if prev_close < prev_bb_low and rsi_prev <= 30 and rsi_now > 30 and green:
                side = "LONG"; entry = close_px
                sl = entry * (1 - RANGE_SL); tp = df.bb_mid.iat[i]
            elif prev_close > prev_bb_up and rsi_prev >= 70 and rsi_now < 70 and red:
                side = "SHORT"; entry = close_px
                sl = entry * (1 + RANGE_SL); tp = df.bb_mid.iat[i]

        if side is None or sl is None or tp is None: continue
        # Sanity: TP and SL must be on correct sides of entry
        if side == "LONG"  and not (tp > entry > sl): continue
        if side == "SHORT" and not (tp < entry < sl): continue

        # Position sizing — risk-based, capped at 3× notional
        risk_per_unit = abs(entry - sl)
        if risk_per_unit <= 0: continue
        qty = (equity * RISK_PCT) / risk_per_unit
        qty_cap = (equity * 0.95 * LEVERAGE) / entry
        qty = min(qty, qty_cap)
        if qty <= 0: continue
        # Entry fee
        equity -= entry * qty * COMMISSION
        pos = {"side": side, "regime": regime, "entry": entry, "qty": qty,
               "sl": sl, "tp": tp, "entry_time": df.timestamp.iat[i]}

    return trades, equity, max_dd


def run_monthly(start_year=2024, start_month=1, end_year=2026, end_month=5):
    rows = []
    y, m = start_year, start_month
    while (y, m) <= (end_year, end_month):
        last = monthrange(y, m)[1]
        s = f"{y}-{m:02d}-01 00:00:00"
        e = f"{y}-{m:02d}-{last:02d} 23:55:00"
        df = load_5m(s, e)
        if len(df) < 300:
            rows.append({"month": f"{y}-{m:02d}", "n": 0, "wr": 0, "pf": 0,
                         "dd": 0, "pct": 0})
        else:
            trades, eq, max_dd = run_backtest(df)
            n = len(trades)
            wins = [t for t in trades if t.pnl_pct > 0]
            losses = [t for t in trades if t.pnl_pct <= 0]
            sum_pct = sum(t.pnl_pct for t in trades)
            pf = (sum(t.pnl_pct for t in wins) / -sum(t.pnl_pct for t in losses)) if losses else float("inf")
            wr = (len(wins) / n * 100) if n else 0
            rows.append({"month": f"{y}-{m:02d}", "n": n, "wr": wr, "pf": pf,
                         "dd": max_dd, "pct": sum_pct})
        m += 1
        if m > 12: m = 1; y += 1
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame, label: str):
    total_n = int(df.n.sum())
    pos_months = int((df.pct > 0).sum())
    median_pct = df.pct.median()
    mean_pct = df.pct.mean()
    worst = df.pct.min(); best = df.pct.max()
    sum_pct = df.pct.sum()
    avg_dd = df.dd.mean()
    max_dd = df.dd.max()
    # Compound month-on-month (each month starts fresh $5k, but PnL adds linearly to a "synthetic" account)
    eq = INITIAL_CAPITAL; peak = eq; cum_dd = 0
    for p in df.pct:
        eq *= (1 + p/100)
        if eq > peak: peak = eq
        d = (peak - eq) / peak * 100
        if d > cum_dd: cum_dd = d
    print(f"\n=== {label} ===")
    print(f"  Months:           {len(df)}")
    print(f"  Total trades:     {total_n}  ({total_n/len(df):.1f}/mo)")
    print(f"  Positive months:  {pos_months}/{len(df)} ({pos_months/len(df)*100:.0f}%)")
    print(f"  Median month:     {median_pct:+.2f}%")
    print(f"  Mean month:       {mean_pct:+.2f}%")
    print(f"  Best month:       {best:+.2f}%   Worst: {worst:+.2f}%")
    print(f"  Sum of months:    {sum_pct:+.2f}%")
    print(f"  Avg DD / month:   {avg_dd:.2f}%   Worst single month DD: {max_dd:.2f}%")
    print(f"  Compounded:       ${eq:,.0f} ({(eq/INITIAL_CAPITAL-1)*100:+.1f}%)   Cum DD: {cum_dd:.2f}%")


def main():
    global USE_WIN_COOLDOWN, USE_VOLUME_GATE
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=["baseline", "cooldown", "volume", "both"], default="baseline")
    parser.add_argument("--start", default="2024-01")
    parser.add_argument("--end", default="2026-05")
    args = parser.parse_args()

    USE_WIN_COOLDOWN = args.variant in ("cooldown", "both")
    USE_VOLUME_GATE  = args.variant in ("volume", "both")

    sy, sm = map(int, args.start.split("-"))
    ey, em = map(int, args.end.split("-"))

    label = f"v1 {args.variant.upper()}"
    print(f"Running {label} monthly OOS  ({sy}-{sm:02d} → {ey}-{em:02d})")
    print(f"  win cooldown: {USE_WIN_COOLDOWN}   volume gate: {USE_VOLUME_GATE}")

    df = run_monthly(sy, sm, ey, em)
    print(f"\n{'Month':<10} {'n':>3} {'WR%':>5} {'PF':>6} {'DD%':>6} {'Pct%':>8}")
    print("-" * 50)
    for _, r in df.iterrows():
        pf = f"{r.pf:.2f}" if r.pf != float('inf') else "inf"
        print(f"{r.month:<10} {int(r.n):>3} {r.wr:>4.0f}% {pf:>6} {r.dd:>5.2f}% {r.pct:>+7.2f}%")
    summarize(df, label)


if __name__ == "__main__":
    main()
