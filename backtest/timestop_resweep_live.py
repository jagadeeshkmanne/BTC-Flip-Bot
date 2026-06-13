"""timestop_resweep_live.py — re-sweep the v2.2 time stop on the HONEST engine,
at the EXACT live config, with a fine grid + TIME_SL-exit breakdown.

User concern (2026-06-12): the deployed 12h time stop was chosen on 2026-06-10
from the FICTIONAL (pre-bug-fix) backtest — does 12h actually lose more now
that fills are honest? Show how often the time stop fires and how big those
exits are.

Live v2.2 config (scripts/run_v2.2.sh): 5x, RSI9 35/65, GAP 0.20%, ATR 0.80%,
BE wait 6, TP_DCA 1.00%, smart time-SL 144 bars (12h). Engine =
timestop_sweep.run (live-faithful honest fills).

USER 2026-06-12: DISABLE all basket-level protection so the time stop is
isolated on the raw strategy — no MTM cap (3%/4%), no daily-loss cap. Both
thresholds set unreachable (MTM_CAP=0 would wrongly fire at breakeven during
the BE-wait window, so we push it far instead). The per-leg 0.6%-from-worst
SL, DCA, BE-DCA/L2-trail and TP all remain (those are entry/exit logic, not
the basket caps the user asked to drop).
"""
import pandas as pd
import numpy as np
import timestop_sweep as T

T.MTM_CAP = 1e9              # DISABLED — basket MTM stop never binds
T.DAILY_MAX_LOSS_PCT = 1e9  # DISABLED — daily-loss circuit breaker never binds

TP_DCA = 0.0100
GRID_SMART = [("off (no time stop)", 0), ("smart 1h", 12), ("smart 2h", 24),
              ("smart 3h", 36), ("smart 4h", 48), ("smart 6h", 72),
              ("smart 8h", 96), ("smart 12h (LIVE)", 144),
              ("smart 18h", 216), ("smart 24h", 288)]
GRID_HARD = [("HARD 1h", 12), ("HARD 4h", 48), ("HARD 12h", 144)]


def line(name, r):
    tr = r["trades"]
    n = len(tr)
    wins = [t["net"] for t in tr if t["net"] > 0]
    losses = [t["net"] for t in tr if t["net"] < 0]
    profit = r["balance"] - T.INITIAL_BAL
    pf = (sum(wins) / abs(sum(losses))) if losses else float("inf")
    worst = min((t["net"] for t in tr), default=0.0)
    # TIME_SL-specific damage
    tstops = [t["net"] for t in tr if t["reason"] == "TIME_SL"]
    tsum = sum(tstops); tn = len(tstops)
    tavg = tsum / tn if tn else 0.0
    print(f"{name:<20}{n:>6}{(len(wins)/n*100 if n else 0):>6.0f}%{profit:>+10,.0f}"
          f"{pf:>6.2f}{worst:>+8.0f}   {tn:>5}{tsum:>+10,.0f}{tavg:>+9.1f}")


def main():
    bt = T.prep(pd.read_csv(T.CSV_PATH, parse_dates=["timestamp"]))
    print(f"Data {bt['timestamp'].iloc[0].date()} -> {bt['timestamp'].iloc[-1].date()}"
          f"  ({len(bt):,} bars)   live v2.2 cfg, MTM cap {T.MTM_CAP*100:.0f}%\n")
    for fm_name, fm in [("REAL (honest fills+fees, fixed $5K)", T.FEE_MODES["REAL"]),
                        ("PAPER (fictional booking, 0 fees)", T.FEE_MODES["PAPER"])]:
        print(f"══ v2.2  [{fm_name}] ══")
        print(f"{'time stop':<20}{'trades':>6}{'win%':>7}{'net $':>10}{'PF':>6}{'worst$':>8}"
              f"   {'#TIME':>5}{'TIME $':>10}{'avgTIME$':>9}")
        for vname, bars in GRID_SMART:
            r = T.run(bt, TP_DCA, fm["fee"], fm["slip"], fm["compound"],
                      time_bars=bars, time_smart=True)
            line(vname, r)
        for vname, bars in GRID_HARD:
            r = T.run(bt, TP_DCA, fm["fee"], fm["slip"], fm["compound"],
                      time_bars=bars, time_smart=False)
            line(vname, r)
        print()


if __name__ == "__main__":
    main()
