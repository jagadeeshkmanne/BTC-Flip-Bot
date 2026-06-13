"""sl_tp_cooldown_sweep.py — single-leg v2.2: best SL x TP x cooldown? (user 2026-06-12)

User spec: remove L2 (no DCA), single position. Sweep:
  - SL  around 0.5%
  - TP
  - COOLING PERIOD after a losing/SL trade (1h / 2h / 4h) before re-entry
Find the best honest cell. Engine = fresh_honest (honest fills, real fees),
use_dca=False, time-SL held at v2.2's 144 bars, fixed $5K sizing.
"""
import numpy as np
import pandas as pd
import fresh_honest as fh

bt = fh.prep()

TP_GRID = [0.003, 0.005, 0.0075, 0.010, 0.015, 0.020]
SL_GRID = [0.003, 0.005, 0.007, 0.010]
COOL_GRID = [3, 12, 24, 48]        # bars: 15m (baseline), 1h, 2h, 4h
COOL_LBL = {3: "15m", 12: "1h", 24: "2h", 48: "4h"}


def one(tp, sl, cool, fee, slip):
    fh.TP_SINGLE = tp
    fh.COOLDOWN_BARS = cool
    r = fh.run(bt, tp_dca=tp, time_sl=144, fill="honest", fee=fee, slip=slip,
               sl_l1=sl, use_dca=False, fixed_cap=True)
    tr = r["trades"]
    if not tr:
        return None
    nets = np.array([t[0] for t in tr])
    wins = nets[nets > 0]; losses = nets[nets <= 0]
    pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
    return dict(n=len(nets), total=nets.sum(), per=nets.mean(),
                wr=(nets > 0).mean()*100, pf=pf)


if __name__ == "__main__":
    print("Single-leg v2.2 (no DCA), honest fills + real fees. SL x TP x cooldown.\n")
    rows = []
    for tp in TP_GRID:
        for sl in SL_GRID:
            for cool in COOL_GRID:
                m = one(tp, sl, cool, 0.00055, 0.0002)
                if m:
                    rows.append((tp, sl, cool, m))
    rows.sort(key=lambda x: x[3]["total"], reverse=True)
    print(f"{'rank':>4} {'TP':>6} {'SL':>6} {'cool':>5} {'trades':>7} {'net$tot':>10} "
          f"{'net$/t':>8} {'WR':>6} {'PF':>5}")
    for k, (tp, sl, cool, m) in enumerate(rows[:12], 1):
        print(f"{k:>4} {tp*100:>5.2f}% {sl*100:>5.2f}% {COOL_LBL[cool]:>5} {m['n']:>7} "
              f"{m['total']:>+10,.0f} {m['per']:>+8.2f} {m['wr']:>5.1f}% {m['pf']:>5.2f}")
    print("\n  ... worst 3:")
    for tp, sl, cool, m in rows[-3:]:
        print(f"     {tp*100:.2f}%/{sl*100:.2f}%/{COOL_LBL[cool]:>3}  net ${m['total']:+,.0f}  PF {m['pf']:.2f}")

    # gross check on the single best cell (does it have any edge at zero cost?)
    tp, sl, cool, _ = rows[0]
    g = one(tp, sl, cool, 0.0, 0.0)
    print(f"\nBEST cell TP{tp*100:.2f}/SL{sl*100:.2f}/cool{COOL_LBL[cool]}:")
    print(f"  NET (real fees):  ${rows[0][3]['total']:+,.0f}  ({rows[0][3]['per']:+.2f}/trade, PF {rows[0][3]['pf']:.2f})")
    print(f"  GROSS (0 fees):   ${g['total']:+,.0f}  ({g['per']:+.2f}/trade, PF {g['pf']:.2f})")
