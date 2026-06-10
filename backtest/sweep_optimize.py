"""
Comprehensive parameter optimization for BOTH v1.1 (with-trend) and v3 (counter-trend).
Find best config maximizing: profit + WR, minimizing: DD.
5-year window for full validation.
"""
import sys
sys.path.insert(0, '/Users/jags/Desktop/BTC-Flip-Bot/backtest')
from sweep_3month import prep_data, run
import pandas as pd
import itertools

CSV = '/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv'
df = pd.read_csv(CSV, parse_dates=["timestamp"])
df = df.sort_values("timestamp").reset_index(drop=True)
bt5y = prep_data(df, 1825)
print(f"5-year window: {len(bt5y)} bars\n")

# Parameter ranges to sweep
rsi_pairs = [(25, 75), (28, 72), (30, 70), (33, 67), (35, 65)]
gap_mins = [0.0015, 0.0020, 0.0025, 0.0030]
be_waits = [0, 3, 6, 12]
atr_maxes = [0.004, 0.006, 0.008]

def score(r):
    """Risk-adjusted score: profit × WR^2 ÷ (1 + DD%)."""
    if r["trades"] < 100:
        return -999999
    return r["profit"] * (r["wr"]/100) ** 2 / (1 + r["max_dd"])

for variant_name, counter_trend in [("v1.1 WITH-TREND", False), ("v3 COUNTER-TREND", True)]:
    print("═" * 110)
    print(f"  {variant_name} — 5-year optimization sweep")
    print("═" * 110)
    print(f"\n  Testing {len(rsi_pairs)*len(gap_mins)*len(be_waits)*len(atr_maxes)} configs...")

    results = []
    for rsi_long, rsi_short in rsi_pairs:
        for gap in gap_mins:
            for be_wait in be_waits:
                for atr in atr_maxes:
                    r = run(bt5y, rsi_long=rsi_long, rsi_short=rsi_short,
                            gap_min=gap, atr_max=atr,
                            be_dca_wait_bars=be_wait,
                            allow_counter_trend=counter_trend,
                            use_time_sl=True)
                    label = f"RSI{rsi_long}/{rsi_short} GAP{gap*100:.2f}% ATR{atr*100:.1f}% BE{be_wait}"
                    results.append((label, r, score(r)))

    # Rank by score
    results.sort(key=lambda x: -x[2])
    print(f"\n  TOP 10 by risk-adjusted score:")
    print(f"  {'CONFIG':<48} {'Trades':>7} {'Win%':>6} {'Profit':>10} {'DD%':>7} {'PF':>6}")
    print("  " + "─" * 95)
    for label, r, s in results[:10]:
        sign = "+" if r["profit"] >= 0 else "-"
        print(f"  {label:<48} {r['trades']:>7} {r['wr']:>5.1f}% {sign}${abs(r['profit']):>7,.0f} {r['max_dd']:>6.2f}% {r['pf']:>5.2f}")

    # Also rank by absolute profit
    print(f"\n  TOP 5 by ABSOLUTE PROFIT:")
    by_profit = sorted(results, key=lambda x: -x[1]["profit"])[:5]
    for label, r, _ in by_profit:
        sign = "+" if r["profit"] >= 0 else "-"
        print(f"    {sign}${abs(r['profit']):>8,.0f}  WR {r['wr']:>4.1f}%  DD {r['max_dd']:>5.2f}%  PF {r['pf']:>4.2f}  ← {label}")

    # Also rank by PF
    print(f"\n  TOP 5 by PROFIT FACTOR (>500 trades):")
    by_pf = sorted([r for r in results if r[1]["trades"] >= 500], key=lambda x: -x[1]["pf"])[:5]
    for label, r, _ in by_pf:
        sign = "+" if r["profit"] >= 0 else "-"
        print(f"    PF {r['pf']:>4.2f}  {sign}${abs(r['profit']):>8,.0f}  WR {r['wr']:>4.1f}%  DD {r['max_dd']:>5.2f}%  ← {label}")

    print()

print("\n═══ Done — pick a config from each variant ═══")
