"""dynamic_dca.py — DYNAMIC DCA: spacing + TP scaled by ATR, variable levels.
(user 2026-06-12) Instead of fixed 0.5% spacing, size everything off volatility
(the 3Commas-style approach). On v2.2 RSI entry. Honest fills, fees, liquidation.
Compared to fixed DCA and buy & hold.
"""
import numpy as np
import fresh_honest as fh

bt = fh.prep()
O, H, L, C = bt["open"].values, bt["high"].values, bt["low"].values, bt["close"].values
RSI, ATR, GAP, TR = bt["rsi"].values, bt["atr_pct"].values, bt["gap"].values, bt["trend"].values
n = len(bt); INIT = 5000.0; FEE = 0.00055


def run(N, space_mult, tp_mult, LEV, long_only, fixed_space=None, fixed_tp=None):
    bal = INIT; cyc = wins = liqs = 0; peak = INIT; maxdd = 0.0
    i = 1
    while i < n - 1:
        if (np.isnan(RSI[i]) or np.isnan(GAP[i]) or np.isnan(TR[i]) or np.isnan(ATR[i])
                or ATR[i] > 0.80 or GAP[i] < 0.0020):
            i += 1; continue
        side = 1 if RSI[i] <= 35 else (-1 if RSI[i] >= 65 else 0)
        if side == 0 or (long_only and side == -1):
            i += 1; continue
        atr = ATR[i] / 100.0                                   # ATR as fraction
        S = fixed_space if fixed_space is not None else space_mult * atr
        TP = fixed_tp if fixed_tp is not None else tp_mult * atr
        e0 = O[i + 1]
        levels = [e0 * (1 - side * S * k) for k in range(N)]
        leg = (bal * LEV) / N
        filled = 0; qty = cost = fees = 0.0
        out = None; j = i + 1
        while j < n:
            bl, bh = L[j], H[j]; new = False
            while filled < N:
                lp = e0 if filled == 0 else levels[filled]
                if filled != 0 and not ((bl <= lp) if side == 1 else (bh >= lp)):
                    break
                q = leg / lp; qty += q; cost += lp * q; fees += lp * q * FEE
                filled += 1; new = True
            avg = cost / qty; adv = bl if side == 1 else bh
            if bal + (adv - avg) * qty * side <= 0:
                bal = 0.0; out = "LIQ"; liqs += 1; break
            if filled and not new:
                tp = avg * (1 + side * TP)
                if (bh >= tp) if side == 1 else (bl <= tp):
                    bal += (tp - avg) * qty * side - fees - tp * qty * FEE
                    wins += 1; out = "TP"; break
            j += 1
        if out is None:
            bal += (C[-1] - avg) * qty * side - fees; break
        cyc += 1; peak = max(peak, bal); maxdd = max(maxdd, (peak - bal)/peak)
        if bal <= 1:
            break
        i = j + 1
    return bal, cyc, wins, liqs, maxdd


bh = INIT * (C[-1]/C[0])
print(f"DYNAMIC DCA (ATR-scaled) on v2.2 entry.  BUY & HOLD: $5,000 -> ${bh:,.0f}\n")
print(f"  {'mode':>10} {'N':>2} {'spacing':>12} {'TP':>10} {'lev':>4} {'cyc':>5} {'win%':>6} {'LIQ':>4} {'final$':>11} {'DD':>5}")


def line(lbl, N, sm, tm, lev, lo, fs=None, ft=None):
    bal, cyc, w, liq, dd = run(N, sm, tm, lev, lo, fs, ft)
    wr = w/cyc*100 if cyc else 0
    sp = f"{fs*100:.1f}% fixed" if fs is not None else f"{sm:.1f}xATR"
    tp = f"{ft*100:.1f}% fixed" if ft is not None else f"{tm:.1f}xATR"
    print(f"  {lbl:>10} {N:>2} {sp:>12} {tp:>10} {lev:>3.0f}x {cyc:>5} {wr:>5.1f}% {liq:>4} {bal:>+11,.0f} {dd*100:>4.0f}%")


print("  -- DYNAMIC (ATR-scaled spacing & TP), long-only spot --")
for N in (2, 3, 4):
    for sm in (1.0, 1.5):
        line("dyn-LO", N, sm, 1.0, 1.0, True)
print("  -- DYNAMIC, long+short, leverage (the deployable-style setup) --")
for lev in (2.0, 5.0):
    line("dyn-LS", 3, 1.5, 1.0, lev, False)
print("  -- FIXED DCA baseline (for comparison) --")
line("fixed-LO", 3, 0, 0, 1.0, True, fs=0.005, ft=0.005)
line("fixed-LS", 3, 0, 0, 5.0, False, fs=0.005, ft=0.005)
