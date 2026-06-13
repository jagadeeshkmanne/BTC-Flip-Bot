"""gap_on_off_v22.py — what does the gap filter actually do to full v2.2?
Run the real v2.2 (DCA on) with the gap filter ON (0.20%) vs OFF (0.0),
honest fills + real fees. Shows trades removed, loss avoided, and PF.
"""
import numpy as np
import fresh_honest as fh

bt = fh.prep()


def go(gap_min, fee, slip):
    r = fh.run(bt, tp_dca=0.0100, time_sl=144, fill="honest", fee=fee, slip=slip,
               sl_l1=0.006, use_dca=True, fixed_cap=True, gap_min=gap_min)
    nets = np.array([t[0] for t in r["trades"]])
    w, l = nets[nets > 0], nets[nets <= 0]
    pf = w.sum()/abs(l.sum()) if l.sum() else float("inf")
    return len(nets), nets.sum(), nets.mean(), (nets > 0).mean()*100, pf


print("FULL v2.2 (DCA on), honest fills.  gap filter ON (0.20%) vs OFF (0.0)\n")
for tag, fee, slip in [("NET (real fees)", 0.00055, 0.0002), ("GROSS (0 fees)", 0.0, 0.0)]:
    print(f"== {tag} ==")
    print(f"  {'':14}{'trades':>8}{'net $tot':>12}{'$/trade':>9}{'WR':>7}{'PF':>6}")
    for label, gm in [("gap ON  0.20%", 0.0020), ("gap OFF 0.00%", 0.0)]:
        n, tot, per, wr, pf = go(gm, fee, slip)
        print(f"  {label:14}{n:>8}{tot:>+12,.0f}{per:>+9.2f}{wr:>6.1f}%{pf:>6.2f}")
    print()
