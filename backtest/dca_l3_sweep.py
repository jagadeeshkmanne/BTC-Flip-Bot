"""dca_l3_sweep.py — best L1/L2 config, does L3 help, best TP? (user 2026-06-12)

DCA basket on v2.2 RSI entries: N legs (2 = L1+L2, 3 = +L3) spaced S% apart,
equal notional, exit (TP) on avg*(1+TP). Honest fills, real fees, pessimistic
intrabar (deeper fills before TP), liquidation if basket equity<=0. Sweeps
levels, spacing, TP, leverage, and long-only vs long+short. Compared to buy&hold.
"""
import numpy as np
import fresh_honest as fh

bt = fh.prep()
O, H, L, C = bt["open"].values, bt["high"].values, bt["low"].values, bt["close"].values
RSI, ATR, GAP, TR = bt["rsi"].values, bt["atr_pct"].values, bt["gap"].values, bt["trend"].values
n = len(bt); INIT = 5000.0; FEE = 0.00055


def run(N, S, TP, LEV, long_only):
    bal = INIT; cycles = wins = liqs = 0; peak = INIT; maxdd = 0.0
    i = 1
    while i < n - 1:
        if (np.isnan(RSI[i]) or np.isnan(GAP[i]) or np.isnan(TR[i]) or np.isnan(ATR[i])
                or ATR[i] > 0.80 or GAP[i] < 0.0020):
            i += 1; continue
        side = 1 if RSI[i] <= 35 else (-1 if RSI[i] >= 65 else 0)
        if side == 0 or (long_only and side == -1):
            i += 1; continue
        e0 = O[i + 1]
        levels = [e0 * (1 - side * S * k) for k in range(N)]
        leg = (bal * LEV) / N
        filled = qty = cost = fees = 0.0; filled = 0
        out = None; j = i + 1
        while j < n:
            bl, bh = L[j], H[j]
            new = False
            while filled < N:
                lp = e0 if filled == 0 else levels[filled]
                if filled != 0 and not ((bl <= lp) if side == 1 else (bh >= lp)):
                    break
                q = leg / lp; qty += q; cost += lp * q; fees += lp * q * FEE
                filled += 1; new = True
            avg = cost / qty
            adv = bl if side == 1 else bh
            if bal + (adv - avg) * qty * side <= 0:
                bal = 0.0; out = "LIQ"; liqs += 1; break
            if filled and not new:
                tp = avg * (1 + side * TP)
                if (bh >= tp) if side == 1 else (bl <= tp):
                    bal += (tp - avg) * qty * side - fees - tp * qty * FEE
                    out = "TP"; wins += 1; break
            j += 1
        if out is None:
            bal += (C[-1] - avg) * qty * side - fees; break
        cycles += 1; peak = max(peak, bal); maxdd = max(maxdd, (peak - bal)/peak)
        if bal <= 1:
            break
        i = j + 1
    return bal, cycles, wins, liqs, maxdd


bh = C[-1]/C[0] - 1
print(f"BUY & HOLD: {bh*100:+.0f}%  ($5,000 -> ${INIT*(1+bh):,.0f})  over {bt['timestamp'].iloc[0].date()}..{bt['timestamp'].iloc[-1].date()}\n")
print(f"  {'mode':>10} {'N':>2} {'spc':>5} {'TP':>5} {'lev':>4} {'cyc':>5} {'win%':>6} {'LIQ':>4} {'final$':>11} {'maxDD':>6}")
rows = []
for mode, lo in [("long+short", False), ("long-only", True)]:
    for LEV in (5.0, 2.0, 1.0):
        for N in (2, 3):
            for S in (0.005, 0.010):
                for TP in (0.003, 0.005, 0.010):
                    bal, cyc, w, liq, dd = run(N, S, TP, LEV, lo)
                    wr = w/cyc*100 if cyc else 0
                    rows.append((mode, N, S, TP, LEV, cyc, wr, liq, bal, dd))
# print survivors (no liq, positive) sorted by final$, then a few liq examples
surv = [r for r in rows if r[7] == 0 and r[8] > INIT]
surv.sort(key=lambda r: -r[8])
print("  -- configs that SURVIVED and grew (no liquidation, final > $5,000): --")
if not surv:
    print("     NONE.")
for r in surv[:8]:
    print(f"  {r[0]:>10} {r[1]:>2} {r[2]*100:>4.1f}% {r[3]*100:>4.1f}% {r[4]:>3.0f}x {r[5]:>5} {r[6]:>5.1f}% {r[7]:>4} {r[8]:>+11,.0f} {r[9]*100:>5.0f}%")
print("\n  -- best by final$ in EACH mode (incl. liquidated): --")
for mode in ("long+short", "long-only"):
    best = max((r for r in rows if r[0] == mode), key=lambda r: r[8])
    r = best
    print(f"  {r[0]:>10} {r[1]:>2} {r[2]*100:>4.1f}% {r[3]*100:>4.1f}% {r[4]:>3.0f}x {r[5]:>5} {r[6]:>5.1f}% {r[7]:>4} {r[8]:>+11,.0f} {r[9]*100:>5.0f}%")
print(f"\n  L3 vs L2 check (long-only 1x, S=0.5%, TP=0.3%):")
for N in (2, 3):
    bal, cyc, w, liq, dd = run(N, 0.005, 0.003, 1.0, True)
    print(f"     N={N} ({'L1+L2' if N==2 else 'L1+L2+L3'}): final ${bal:+,.0f}  win% {w/cyc*100 if cyc else 0:.1f}  maxDD {dd*100:.0f}%")
