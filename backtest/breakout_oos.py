"""BB-breakout strategy OOS backtest.

Tests whether a simple Bollinger-band breakout strategy on 5m BTC has any
edge — before committing it as v2 Engine B.

═══════════════════════════════════════════════════════════════
Strategy (intentionally simple — no parameter tuning)
═══════════════════════════════════════════════════════════════

Entry (LONG):
  - close[i] > bb_upper[i]                 (price breaks above upper band)
  - volume[i] > VOL_MULT × SMA(20, vol)    (volume confirms)
  - bar i closed in upper half of its range (h - close < (h - l) / 2)
  - No position currently open

Entry (SHORT): mirror

Exit:
  - SL: opposite extreme of breakout candle
        LONG: bar i's low - SL_BUFFER     (low of breakout bar minus 0.05%)
        SHORT: bar i's high + SL_BUFFER
  - TP: 1:2 risk-reward fixed bracket
        LONG: entry + 2 × (entry - sl)
        SHORT: entry - 2 × (sl - entry)
  - Worst-case intra-bar: if SL and TP both touched in same bar, SL wins

Sizing:
  - Risk 0.5% of balance per trade (same as rsiscalp_trend)
  - Capped at 3× notional (leverage cap)

NO DCA, NO trailing, NO cooldown, NO regime filter.
Just the pure breakout idea.

═══════════════════════════════════════════════════════════════
Validation built in
═══════════════════════════════════════════════════════════════
  - Conservative entry: bar's close (not open of next bar) — most realistic
  - SL-wins-ties: intra-bar conflicts → SL wins (industry standard pessimism)
  - 0.04% commission per side
  - No slippage modeled (slight overestimate but parity with rsiscalp_backtest)
  - Sanity prints: WR + trade count expected ~30-45% / ~100-500/yr for breakout
"""
from __future__ import annotations
import argparse
import os
from calendar import monthrange
from dataclasses import dataclass

import numpy as np
import pandas as pd

CACHE_5M = os.path.join(os.path.dirname(__file__), "..", "data", "cache", "BTCUSDT_5m.csv")

# Params
INITIAL    = 5000.0
RISK_PCT   = 0.005       # 0.5% per trade
LEVERAGE   = 3.0
COMMISSION = 0.0004      # 0.04% per side
VOL_MULT   = 1.5         # require vol > 1.5× SMA(20, vol)
RR_RATIO   = 2.0         # 1:2 risk-reward
SL_BUFFER  = 0.0005      # 0.05% past breakout candle extreme


def bollinger(closes, n=20, k=2.0):
    s = pd.Series(closes)
    m  = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)
    return (m - k*sd).to_numpy(), m.to_numpy(), (m + k*sd).to_numpy()


def load_5m(start_iso, end_iso):
    df = pd.read_csv(CACHE_5M, parse_dates=["timestamp"])
    df = df[(df.timestamp >= start_iso) & (df.timestamp <= end_iso)].reset_index(drop=True)
    return df


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time:  pd.Timestamp
    side: str
    entry: float
    exit: float
    qty: float
    pnl_pct: float
    pnl_usd: float
    reason: str
    bars_held: int


def run_backtest(df: pd.DataFrame):
    """Run BB-breakout on a window. Returns (trades, final_eq, max_dd_pct)."""
    closes = df.close.to_numpy(); opens = df.open.to_numpy()
    highs  = df.high.to_numpy();  lows  = df.low.to_numpy()
    vols   = df.volume.to_numpy()

    bb_low, bb_mid, bb_up = bollinger(closes, 20, 2.0)
    vol_sma = pd.Series(vols).rolling(20).mean().to_numpy()

    equity = INITIAL
    peak = equity
    max_dd = 0.0
    trades = []
    pos = None
    n = len(df)

    for i in range(max(25, 20), n):
        # ── Manage open position FIRST (using bar i's H/L) ──
        if pos is not None:
            h = highs[i]; l = lows[i]
            sl_hit = (pos["side"] == "LONG"  and l <= pos["sl"]) or \
                     (pos["side"] == "SHORT" and h >= pos["sl"])
            tp_hit = (pos["side"] == "LONG"  and h >= pos["tp"]) or \
                     (pos["side"] == "SHORT" and l <= pos["tp"])
            if sl_hit:                      # SL wins ties (conservative)
                exit_px = pos["sl"]; reason = "SL"
            elif tp_hit:
                exit_px = pos["tp"]; reason = "TP"
            else:
                continue
            gross = (exit_px - pos["entry"]) * pos["qty"] if pos["side"] == "LONG" \
                    else (pos["entry"] - exit_px) * pos["qty"]
            fees = (pos["entry"] + exit_px) * pos["qty"] * COMMISSION
            net  = gross - fees
            pnl_pct = net / equity * 100
            equity += net
            trades.append(Trade(
                entry_time=pos["entry_time"], exit_time=df.timestamp.iat[i],
                side=pos["side"], entry=pos["entry"], exit=exit_px,
                qty=pos["qty"], pnl_pct=pnl_pct, pnl_usd=net, reason=reason,
                bars_held=i - pos["entry_i"]))
            if equity > peak: peak = equity
            dd = (peak - equity) / peak * 100
            if dd > max_dd: max_dd = dd
            pos = None
            continue

        # ── Entry check (no position) ──
        if np.isnan(bb_up[i]) or np.isnan(vol_sma[i]):
            continue
        c = closes[i]; o = opens[i]; h = highs[i]; l = lows[i]; v = vols[i]
        if vol_sma[i] <= 0: continue

        # LONG breakout: close above upper BB + volume confirm + strong close
        if c > bb_up[i] and v > vol_sma[i] * VOL_MULT:
            bar_range = h - l
            close_in_upper_half = bar_range > 0 and (h - c) <= bar_range / 2
            if close_in_upper_half:
                entry = c
                sl_raw = l * (1 - SL_BUFFER)
                if sl_raw >= entry: continue   # malformed (rare)
                risk = entry - sl_raw
                # Cap risk at 1% to avoid huge SL distances on volatile bars
                if risk / entry > 0.01: continue
                tp = entry + risk * RR_RATIO
                qty_risk = (equity * RISK_PCT) / risk
                qty_cap  = (equity * 0.95 * LEVERAGE) / entry
                qty = min(qty_risk, qty_cap)
                if qty <= 0: continue
                equity -= entry * qty * COMMISSION
                pos = {"side": "LONG", "entry": entry, "qty": qty, "sl": sl_raw,
                       "tp": tp, "entry_time": df.timestamp.iat[i], "entry_i": i}
                continue

        # SHORT breakout: close below lower BB + volume confirm + strong close
        if c < bb_low[i] and v > vol_sma[i] * VOL_MULT:
            bar_range = h - l
            close_in_lower_half = bar_range > 0 and (c - l) <= bar_range / 2
            if close_in_lower_half:
                entry = c
                sl_raw = h * (1 + SL_BUFFER)
                if sl_raw <= entry: continue
                risk = sl_raw - entry
                if risk / entry > 0.01: continue
                tp = entry - risk * RR_RATIO
                qty_risk = (equity * RISK_PCT) / risk
                qty_cap  = (equity * 0.95 * LEVERAGE) / entry
                qty = min(qty_risk, qty_cap)
                if qty <= 0: continue
                equity -= entry * qty * COMMISSION
                pos = {"side": "SHORT", "entry": entry, "qty": qty, "sl": sl_raw,
                       "tp": tp, "entry_time": df.timestamp.iat[i], "entry_i": i}

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
            rows.append({"month": f"{y}-{m:02d}", "n": 0, "wr": 0, "pf": 0, "dd": 0, "pct": 0})
        else:
            tr, eq, dd = run_backtest(df)
            n = len(tr)
            wins = [t for t in tr if t.pnl_usd > 0]
            losses = [t for t in tr if t.pnl_usd <= 0]
            pf = (sum(t.pnl_usd for t in wins) / -sum(t.pnl_usd for t in losses)) if losses else float("inf")
            wr = (len(wins) / n * 100) if n else 0
            net_pct = (eq - INITIAL) / INITIAL * 100
            rows.append({"month": f"{y}-{m:02d}", "n": n, "wr": wr, "pf": pf, "dd": dd, "pct": net_pct})
        m += 1
        if m > 12: m = 1; y += 1
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-01")
    ap.add_argument("--end",   default="2026-05")
    args = ap.parse_args()
    sy, sm = map(int, args.start.split("-"))
    ey, em = map(int, args.end.split("-"))

    df = run_monthly(sy, sm, ey, em)
    print(f"\n{'Month':<10} {'n':>3} {'WR%':>5} {'PF':>6} {'DD%':>6} {'Pct%':>8}")
    print("-" * 50)
    for _, r in df.iterrows():
        pf = f"{r.pf:.2f}" if r.pf != float('inf') else "inf"
        print(f"{r.month:<10} {int(r.n):>3} {r.wr:>4.0f}% {pf:>6} {r.dd:>5.2f}% {r.pct:>+7.2f}%")

    # Compounded
    eq = INITIAL; peak = INITIAL; cum_dd = 0
    for p in df.pct:
        eq *= (1 + p/100)
        if eq > peak: peak = eq
        d = (peak - eq) / peak * 100
        if d > cum_dd: cum_dd = d

    total_n = int(df.n.sum())
    pos_months = int((df.pct > 0).sum())
    print(f"\n=== BB-Breakout OOS Summary ({len(df)} months) ===")
    print(f"  Trades:           {total_n}  ({total_n/len(df):.1f}/mo)")
    print(f"  Positive months:  {pos_months}/{len(df)} ({pos_months/len(df)*100:.0f}%)")
    print(f"  Median month:     {df.pct.median():+.2f}%")
    print(f"  Mean month:       {df.pct.mean():+.2f}%")
    print(f"  Best month:       {df.pct.max():+.2f}%   Worst: {df.pct.min():+.2f}%")
    print(f"  Avg DD / month:   {df.dd.mean():.2f}%   Worst single month DD: {df.dd.max():.2f}%")
    print(f"  Compounded:       ${eq:,.0f} ({(eq/INITIAL-1)*100:+.1f}%)   Cum DD: {cum_dd:.2f}%")
    print()
    print("  Sanity checks (alarms):")
    if total_n / len(df) > 100:
        print(f"    ⚠ Trade frequency {total_n/len(df):.0f}/mo seems HIGH for breakout strategy (expected 5-50/mo)")
    if total_n / len(df) < 2:
        print(f"    ⚠ Trade frequency {total_n/len(df):.0f}/mo seems LOW — filters may be too strict")
    overall_wr = sum(1 for _ in df.iterrows()) and df.wr.mean()
    print(f"    Avg monthly WR: {overall_wr:.0f}%  (expected 30-50% for breakout)")


if __name__ == "__main__":
    main()
