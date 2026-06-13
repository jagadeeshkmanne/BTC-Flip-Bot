"""analyze_gap_filter.py — is the 15m gap filter actually SAVING bad trades,
or just making v2.2 trade less? And can a gap-like filter ever turn it positive?

User (2026-06-12): the ±0.20% 15m-gap filter "is saving a lot of bad trades —
find another filter like it." This tests the premise directly:
  - Run honest v2.2 with the gap filter OFF (gap_min=0) so we capture the
    knife-edge trades it normally blocks.
  - For every trade, read the 15m gap at its signal bar, plus its outcome.
  - GROSS mode (0 fees) = does gap predict the trade's PRICE outcome at all?
  - NET mode (real fees) = is ANY gap region profitable after costs?
  - Compare BLOCKED (gap<0.20%) vs KEPT (gap>=0.20%): if both ~0 gross, the
    filter selects nothing — it only reduces trade count (the confirmation-bias
    trap: you SEE it block a loss, you don't see the wins it blocks too).
"""
import numpy as np
import pandas as pd
import fresh_honest as fh

bt = fh.prep()
GAP = bt["gap"].values
TREND = bt["trend"].values


def trades_with_gap(fee, slip):
    r = fh.run(bt, tp_dca=0.0100, time_sl=144, fill="honest", fee=fee, slip=slip,
               sl_l1=0.006, fixed_cap=True, gap_min=0.0)   # gap filter OFF
    out = []
    for net, reason, t_exit, open_i, side, legs in r["trades"]:
        g = GAP[open_i - 1] * 100.0           # signal-bar 15m gap, in %
        if np.isnan(g):
            continue
        out.append((side, g, net))
    return pd.DataFrame(out, columns=["side", "gap", "net"])


def show(df, label):
    n = len(df)
    if n == 0:
        print(f"  {label}: none"); return
    wr = (df["net"] > 0).mean() * 100
    print(f"  {label:24s} N={n:6d}  WR={wr:4.1f}%  avg=${df['net'].mean():+7.2f}/trade  "
          f"total=${df['net'].sum():+10,.0f}")


def analyze(df, mode):
    print(f"\n========== {mode} ==========")
    for name, sub in [("ALL", df), ("LONG only", df[df.side == 1]),
                      ("SHORT only", df[df.side == -1])]:
        print(f"\n[{name}]")
        show(sub[sub.gap < 0.20], "BLOCKED (gap<0.20%)")
        show(sub[sub.gap >= 0.20], "KEPT    (gap>=0.20%)")
        # quintiles of gap -> outcome (does more gap = better?)
        q = pd.qcut(sub["gap"], 5, duplicates="drop")
        g = sub.groupby(q, observed=True)["net"].agg(["count", "mean",
                                                      lambda s: (s > 0).mean()*100])
        g.columns = ["N", "avg$/trade", "WR%"]
        print("   gap quintile         " + "  ".join(f"{c:>11}" for c in g.columns))
        for idx, row in g.iterrows():
            print(f"   {str(idx):20s} {row['N']:>11.0f} {row['avg$/trade']:>+11.2f} {row['WR%']:>10.1f}%")


if __name__ == "__main__":
    print("GROSS = honest fills, ZERO fees (does gap predict price outcome?)")
    analyze(trades_with_gap(0.0, 0.0), "GROSS (0 fees)")
    print("\n\nNET = honest fills + real taker fees + slip")
    analyze(trades_with_gap(0.00055, 0.0002), "NET (taker+slip)")
