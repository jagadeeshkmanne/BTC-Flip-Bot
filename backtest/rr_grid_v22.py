"""rr_grid_v22.py — TP x SL R:R grid on the EXACT v2.2 entry. (user 2026-06-12)
Faithful entry: fresh_honest rsi/gap/atr/trend, gap filter ON (0.20%), ATR<=0.80%,
counter-trend, single position. Honest fills, pessimistic stop-before-TP.
Reports gross (0 fee) and net (taker 0.055% + slip 0.02%). Finds the best cell.
"""
import numpy as np
import fresh_honest as fh

bt = fh.prep()
O, H, L, C = bt["open"].values, bt["high"].values, bt["low"].values, bt["close"].values
RSI, ATR, GAP, TR = bt["rsi"].values, bt["atr_pct"].values, bt["gap"].values, bt["trend"].values
n = len(bt)
RT = 2*(0.00055+0.0002)
GRID = [0.003, 0.005, 0.006, 0.010, 0.015, 0.020]


def sim(tp, sl):
    rets = []
    i = 1
    while i < n-1:
        if (np.isnan(RSI[i]) or np.isnan(ATR[i]) or np.isnan(GAP[i]) or np.isnan(TR[i])
                or ATR[i] > 0.80 or GAP[i] < 0.0020):
            i += 1; continue
        side = 1 if RSI[i] <= 35 else (-1 if RSI[i] >= 65 else 0)
        if side == 0:
            i += 1; continue
        entry = O[i+1]; tp_px = entry*(1+tp*side); sl_px = entry*(1-sl*side)
        ret = None; j = i+1
        while j < n:
            if (side == 1 and L[j] <= sl_px) or (side == -1 and H[j] >= sl_px):
                ret = -sl; break
            if (side == 1 and H[j] >= tp_px) or (side == -1 and L[j] <= tp_px):
                ret = tp; break
            j += 1
        if ret is None:
            break
        rets.append(ret); i = j+1
    return np.array(rets)


print("v2.2 entry, single position. TP x SL grid. gross (0 fee) | net (taker+slip)\n")
print(f"  {'TP':>5} {'SL':>5} {'R:R':>5} {'N':>6} {'WR':>6} {'WR*':>6} {'gross%/t':>9} {'net%/t':>9}")
best = None
for tp in GRID:
    for sl in GRID:
        r = sim(tp, sl)
        if len(r) == 0:
            continue
        wr = (r > 0).mean()*100
        wstar = sl/(tp+sl)*100
        g = r.mean()*100; netv = g - RT*100
        tag = ""
        if best is None or netv > best[0]:
            best = (netv, tp, sl, len(r), wr, g)
        if (tp, sl) in [(0.010, 0.010), (0.006, 0.006)]:
            tag = "  <-- you asked"
        print(f"  {tp*100:>4.1f}% {sl*100:>4.1f}% {tp/sl:>5.2f} {len(r):>6} {wr:>5.1f}% "
              f"{wstar:>5.1f}% {g:>+8.4f}% {netv:>+8.4f}%{tag}")
print(f"\nBEST net cell: TP {best[1]*100:.1f}% / SL {best[2]*100:.1f}%  "
      f"-> net {best[0]:+.4f}%/trade  (gross {best[5]:+.4f}%, WR {best[4]:.1f}%, N={best[3]})")
print("Reminder: net positive = profitable. net negative = loses every trade on average.")
