"""dca_on_entry_demo.py — the DCA bot on OUR entry (v2.2), as a real $5,000 account.
v2.2 IS a 2-leg DCA bot on the RSI9 35/65 entry, long AND short. Run it compounding
from $5,000 the way the dashboard books it (fictional) vs honest fills + real fees.
"""
import numpy as np
import fresh_honest as fh

bt = fh.prep()
INIT = 5000.0


def show(label, fill, fee, slip):
    r = fh.run(bt, tp_dca=0.0100, time_sl=144, fill=fill, fee=fee, slip=slip,
               sl_l1=0.006, use_dca=True, compound=True, gap_min=0.0020)
    tr = r["trades"]
    nets = np.array([t[0] for t in tr]) if tr else np.array([0.0])
    longs = sum(1 for t in tr if t[4] == 1); shorts = sum(1 for t in tr if t[4] == -1)
    wr = (nets > 0).mean()*100
    bal = r["balance"]
    # equity low (max drawdown proxy): reconstruct running balance
    run_bal = INIT + np.cumsum(nets)
    peak = np.maximum.accumulate(np.concatenate([[INIT], run_bal]))
    dd = ((peak[1:] - run_bal) / peak[1:]).max()*100 if len(run_bal) else 0
    print(f"  {label}")
    print(f"     trades {len(tr):,}  ({longs:,} long / {shorts:,} short)  win% {wr:.1f}")
    print(f"     $5,000  ->  ${bal:,.0f}   worst trade ${nets.min():+,.0f}   max drawdown {dd:.0f}%\n")


print("DCA bot on our v2.2 entry (RSI9 35/65, long+short, L1+L2 DCA, TP on avg).")
print(f"BUY & HOLD over the same period: $5,000 -> ${INIT*(bt['close'].iloc[-1]/bt['close'].iloc[0]):,.0f}\n")
show("PAPER  (how the dashboard books it — stops filled at the stop price)", "parity", 0.0, 0.0)
show("REAL   (honest fills at the real market price + taker fees + slip)", "honest", 0.00055, 0.0002)
