"""engine_validation.py — prove the honest engine can (a) detect a planted
edge and (b) shows no phantom edge/penalty on random entries.

Tests:
  RANDOM  - enter long/short on a deterministic pseudo-random schedule.
            Expectation: PF ~ 1.0 and final equity ~ 1.0 at ZERO cost;
            with real costs, loss ~= n_trades * cost. If random shows PF
            far from 1 at zero cost, the engine is biased and all sweep
            results are invalid.
  ORACLE  - deliberate lookahead: go long iff close[i+12] > close[i]*1.002.
            Expectation: massive profit even after costs. If the engine
            can't surface a planted edge, its negatives are meaningless.
"""
import numpy as np
import pandas as pd
from scalp_1pct_search import load, run_trades, evaluate, COST_RT

df = load()
n = len(df)
C = df["close"].values

# RANDOM: deterministic hash-based schedule, ~1 signal per 60 bars, 50/50 side
h = (np.arange(n) * 2654435761 % 2**32)
r = (h % 1000) / 1000.0
long_sig = r < 0.0085
short_sig = (r >= 0.0085) & (r < 0.017)

# ORACLE: lookahead 12 bars (1 hour)
fwd = np.full(n, np.nan)
fwd[:-12] = C[12:] / C[:-12] - 1
o_long = fwd > 0.002
o_short = fwd < -0.002

print(f"bars: {n:,}")
for name, ls, ss in [("RANDOM", long_sig, short_sig), ("ORACLE", o_long, o_short)]:
    for sl, tp in [(0.006, 0.006), (0.003, 0.012)]:
        trades = run_trades(df, ls, ss, sl, tp)
        for cost, tag in [(0.0, "0fee"), (COST_RT, "real")]:
            r3 = evaluate(trades, 3, cost)
            print(f"{name} sl{sl*100:.1f} tp{tp*100:.1f} {tag}: trades {r3['n']:,}  "
                  f"WR {r3['wr']*100:.1f}%  PF {r3['pf']:.3f}  final_eq {r3['final']:.4g}"
                  + ("  BLOWN" if r3['blown'] else ""))
    print()
