"""measure_post_exit.py — does v2.2 close winners too soon, leaving profit behind?

User (2026-06-12): "check the trades... are we closing sooner and losing more
profit on those?" For every v2.2 exit, measure how much further price ran in
the trade's favor in the 2h / 4h AFTER we got out.
"""
import numpy as np
import pandas as pd
import fresh_honest as fh

bt = fh.prep()
ts = bt["timestamp"].values
hi = bt["high"].values; loo = bt["low"].values; cl = bt["close"].values
idx_of = {pd.Timestamp(t): i for i, t in enumerate(ts)}

r = fh.run(bt, tp_dca=0.0100, time_sl=144, fill="honest", fee=0.00055,
           slip=0.0002, sl_l1=0.006, fixed_cap=True)
tr = r["trades"]

WINS = {24: [], 48: []}     # post-exit favorable continuation %, by window (bars)
for net, reason, t_exit, open_i, side, legs in tr:
    if net <= 0:
        continue                          # only winners — "did we leave profit?"
    e = idx_of.get(pd.Timestamp(t_exit))
    if e is None:
        continue
    px = cl[e]
    for W in (24, 48):
        a, b = e + 1, min(e + 1 + W, len(cl))
        if b <= a:
            continue
        if side == 1:                     # LONG: how much higher did it go after we sold
            cont = (hi[a:b].max() - px) / px * 100
        else:                             # SHORT: how much lower did it go after we covered
            cont = (px - loo[a:b].min()) / px * 100
        WINS[W].append(cont)

print(f"v2.2 honest winners analysed: {len(WINS[24]):,}\n")
print("After we EXIT a winner, how much further did price run in our favor?")
for W in (24, 48):
    a = np.array(WINS[W])
    print(f"\n  window = {W} bars ({W*5//60}h after exit):")
    print(f"    median further move: {np.median(a):.2f}%   mean: {a.mean():.2f}%")
    for thr in (0.5, 1.0, 2.0):
        print(f"    ran >= {thr:.1f}% further: {(a>=thr).mean()*100:4.1f}% of winners")
print("\n(Context: v2.2 TP is 0.5% single / 1.0% after DCA. 'Further move' is the")
print(" max favorable excursion AFTER exit — an UPPER bound; a wider TP would also")
print(" turn some current winners into SL losses before reaching it.)")
