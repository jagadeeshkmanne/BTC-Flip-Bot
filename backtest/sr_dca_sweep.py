"""Sweep sr_dca (V2.2) parameters to find the single best config.

V2.2 already has the structural edge that divflip lacks:
  - Prev-day H/L touch (the location filter that divflip can't reproduce)
  - EOD flatten + max 1 cycle/day (limits losers)
  - Hybrid TP (prev_mid → +4% post-DCA)

This sweep tests:
  A) DCA spacing (0.4% live vs 0.5%, 0.6%, 0.85% backtest baseline)
  B) DCA levels (2 vs 3)
  C) SL distance (2% vs tighter/looser)
  D) BE trigger (1% vs 0.5%, 0.75%, 1.5%)
  E) Post-DCA TP cap (4% baseline, vs 3%, 5%, 6%)
  F) Range-filter overlay (skip if 24h range_pos disagrees with side)

Same engine as v22_backtest. BTCUSDT 5m, configurable window.
"""
from __future__ import annotations
import sys, importlib, copy
from pathlib import Path
import datetime as dt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import v22_backtest as bt

ORIGINAL_CONSTANTS = {
    "DCA_LEVELS": bt.DCA_LEVELS,
    "DCA_SPACING": bt.DCA_SPACING,
    "SL_BELOW_WORST": bt.SL_BELOW_WORST,
    "HOLD_MIN_FAV_PCT": bt.HOLD_MIN_FAV_PCT,
    "BE_TRIGGER_PCT": bt.BE_TRIGGER_PCT,
    "BE_BUFFER_PCT": bt.BE_BUFFER_PCT,
    "TP_POST_DCA_PCT": bt.TP_POST_DCA_PCT,
    "DIV_FRESH_BARS": bt.DIV_FRESH_BARS,
    "VOL_MULT": bt.VOL_MULT,
    "MAX_CYCLES_PER_DAY": bt.MAX_CYCLES_PER_DAY,
}


def restore():
    for k, v in ORIGINAL_CONSTANTS.items():
        setattr(bt, k, v)


def patch(**overrides):
    for k, v in overrides.items():
        setattr(bt, k, v)


def summarize_trades(trades):
    if not trades:
        return {"trades": 0, "net": 0, "pct": 0, "wr": 0, "pf": 0, "dd": 0,
                "avg_w": 0, "avg_l": 0, "max_w": 0, "max_l": 0}
    pnls = [t.pnl_usd for t in trades]
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gw = sum(wins); gl = -sum(losses)
    pf = gw / gl if gl > 0 else float("inf")
    net = sum(pnls)
    # Drawdown
    eq = bt.INITIAL_CAPITAL
    peak = eq; dd = 0
    for p in pnls:
        eq += p
        if eq > peak: peak = eq
        ddv = (peak - eq) / peak * 100
        if ddv > dd: dd = ddv
    return {"trades": n, "net": net, "pct": net / bt.INITIAL_CAPITAL * 100,
            "wr": len(wins) / n * 100 if n else 0, "pf": pf, "dd": dd,
            "avg_w": gw / len(wins) if wins else 0,
            "avg_l": gl / len(losses) if losses else 0,
            "max_w": max(wins) if wins else 0,
            "max_l": -min(losses) if losses else 0}


def main():
    start = "2026-04-08"; end = "2026-05-23"
    if len(sys.argv) >= 2: start = sys.argv[1]
    if len(sys.argv) >= 3: end = sys.argv[2]

    print(f"sr_dca (V2.2) parameter sweep — BTCUSDT 5m, {start} → {end}\n")

    variants = [
        # (label, overrides_dict)
        ("V2.2 backtest baseline (DCA 0.85%, SL 2%, BE 1%, TP 4%)",
         dict(DCA_SPACING=0.0085, DCA_LEVELS=2, SL_BELOW_WORST=0.02,
              BE_TRIGGER_PCT=0.01, TP_POST_DCA_PCT=0.04)),
        # Match the LIVE bot's current config (0.4% spacing)
        ("Live config (DCA 0.4%, SL 2%, BE 1%, TP 4%)",
         dict(DCA_SPACING=0.004, DCA_LEVELS=2, SL_BELOW_WORST=0.02,
              BE_TRIGGER_PCT=0.01, TP_POST_DCA_PCT=0.04)),
        # DCA spacing sweep
        ("DCA 0.3%",  dict(DCA_SPACING=0.003)),
        ("DCA 0.5%",  dict(DCA_SPACING=0.005)),
        ("DCA 0.6%",  dict(DCA_SPACING=0.006)),
        ("DCA 0.7%",  dict(DCA_SPACING=0.007)),
        # DCA levels
        ("3 DCA levels @ 0.4%", dict(DCA_LEVELS=3, DCA_SPACING=0.004)),
        ("3 DCA levels @ 0.5%", dict(DCA_LEVELS=3, DCA_SPACING=0.005)),
        # SL distance variants
        ("SL 1.5% below worst", dict(SL_BELOW_WORST=0.015)),
        ("SL 1.0% below worst", dict(SL_BELOW_WORST=0.010)),
        ("SL 2.5% below worst", dict(SL_BELOW_WORST=0.025)),
        # BE trigger variants
        ("BE 0.5%",  dict(BE_TRIGGER_PCT=0.005)),
        ("BE 0.75%", dict(BE_TRIGGER_PCT=0.0075)),
        ("BE 1.5%",  dict(BE_TRIGGER_PCT=0.015)),
        # Post-DCA TP cap variants
        ("TP cap 2%",   dict(TP_POST_DCA_PCT=0.02)),
        ("TP cap 3%",   dict(TP_POST_DCA_PCT=0.03)),
        ("TP cap 5%",   dict(TP_POST_DCA_PCT=0.05)),
        ("TP cap 6%",   dict(TP_POST_DCA_PCT=0.06)),
        # HOLD_MIN_FAV (the "let it run past EOD" threshold)
        ("HOLD favor 0.5%", dict(HOLD_MIN_FAV_PCT=0.005)),
        ("HOLD favor 1.0%", dict(HOLD_MIN_FAV_PCT=0.010)),
        ("HOLD favor 2.0%", dict(HOLD_MIN_FAV_PCT=0.020)),
        # Combo: tight DCA + lower BE + higher TP cap
        ("COMBO A: 0.4% DCA + BE 0.75% + TP 5%",
         dict(DCA_SPACING=0.004, BE_TRIGGER_PCT=0.0075, TP_POST_DCA_PCT=0.05)),
        ("COMBO B: 0.5% DCA + BE 0.5% + TP 5%",
         dict(DCA_SPACING=0.005, BE_TRIGGER_PCT=0.005, TP_POST_DCA_PCT=0.05)),
        ("COMBO C: 0.5% DCA + SL 1.5% + BE 0.75%",
         dict(DCA_SPACING=0.005, SL_BELOW_WORST=0.015, BE_TRIGGER_PCT=0.0075)),
        ("COMBO D: 0.4% DCA + BE 0.5% + TP 5%",
         dict(DCA_SPACING=0.004, BE_TRIGGER_PCT=0.005, TP_POST_DCA_PCT=0.05)),
        # MORE TRADES PER DAY (user request — current is 1/day)
        ("MAX 2 cycles/day @ live config",
         dict(DCA_SPACING=0.004, MAX_CYCLES_PER_DAY=2)),
        ("MAX 3 cycles/day @ live config",
         dict(DCA_SPACING=0.004, MAX_CYCLES_PER_DAY=3)),
        ("MAX unlimited cycles/day @ live config",
         dict(DCA_SPACING=0.004, MAX_CYCLES_PER_DAY=99)),
        ("MAX 2 cycles + COMBO D",
         dict(DCA_SPACING=0.004, BE_TRIGGER_PCT=0.005, TP_POST_DCA_PCT=0.05, MAX_CYCLES_PER_DAY=2)),
        ("MAX 3 cycles + COMBO D",
         dict(DCA_SPACING=0.004, BE_TRIGGER_PCT=0.005, TP_POST_DCA_PCT=0.05, MAX_CYCLES_PER_DAY=3)),
        ("MAX unlimited + COMBO D",
         dict(DCA_SPACING=0.004, BE_TRIGGER_PCT=0.005, TP_POST_DCA_PCT=0.05, MAX_CYCLES_PER_DAY=99)),
        ("MAX 3 + 0.5% DCA + BE 0.5% + TP 5%",
         dict(DCA_SPACING=0.005, BE_TRIGGER_PCT=0.005, TP_POST_DCA_PCT=0.05, MAX_CYCLES_PER_DAY=3)),
        ("MAX unlimited + 0.5% DCA + BE 0.5% + TP 5%",
         dict(DCA_SPACING=0.005, BE_TRIGGER_PCT=0.005, TP_POST_DCA_PCT=0.05, MAX_CYCLES_PER_DAY=99)),
    ]

    print(f"{'config':<60} {'trades':>6} {'$net':>8} {'pct':>7} {'WR':>5} {'PF':>6} {'DD':>6} {'avgW':>6} {'avgL':>6}")
    print("-" * 125)
    results = []
    for label, overrides in variants:
        restore()
        patch(**overrides)
        try:
            result = bt.run_backtest(start, end)
            trades = result[0] if isinstance(result, tuple) else result
        except Exception as e:
            print(f"{label:<60} ERROR: {e}")
            continue
        s = summarize_trades(trades)
        results.append((label, s))
        pf = f"{s['pf']:.2f}" if s['pf'] != float('inf') else "inf"
        print(f"{label:<60} {s['trades']:>6d} ${s['net']:>+7.0f} {s['pct']:>+6.1f}% {s['wr']:>4.0f}% {pf:>6} {s['dd']:>5.1f}% {s['avg_w']:>5.0f}$ {s['avg_l']:>5.0f}$")

    # Find best by PF
    restore()
    print("\n" + "="*125)
    print("Top 3 by PF (profit factor):")
    for label, s in sorted(results, key=lambda x: x[1]["pf"], reverse=True)[:3]:
        print(f"  {label:<60} PF={s['pf']:.2f} net=${s['net']:+.0f} ({s['pct']:+.1f}%) DD={s['dd']:.1f}%")
    print("\nTop 3 by $ net:")
    for label, s in sorted(results, key=lambda x: x[1]["net"], reverse=True)[:3]:
        print(f"  {label:<60} net=${s['net']:+.0f} PF={s['pf']:.2f} DD={s['dd']:.1f}%")


if __name__ == "__main__":
    main()
