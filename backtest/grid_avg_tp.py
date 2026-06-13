"""grid_avg_tp.py — multi-level grid (ladder in, take profit on AVG price). (user 2026-06-12)

User idea: instead of L1+L2 once, make a GRID of levels; accumulate as price
moves against us, exit when price returns to the average + small profit.

Honest model: on RSI signal, open a basket with N equal-notional levels spaced
S% apart. Fill a level when price trades down to it (long) / up to it (short).
TP when price returns to avg*(1+TP). Pessimistic intrabar: fills (adverse)
before TP; TP cannot fire on a bar that just filled a deeper level. Real fees
per fill. Leverage like v2.2; LIQUIDATE if basket equity <= 0 (margin gone).
Compound. Compare to buy & hold.
"""
import numpy as np
import fresh_honest as fh

bt = fh.prep()
O, H, L, C = bt["open"].values, bt["high"].values, bt["low"].values, bt["close"].values
RSI, ATR, GAP, TR = bt["rsi"].values, bt["atr_pct"].values, bt["gap"].values, bt["trend"].values
n = len(bt)
INIT = 5000.0
FEE, SLIP = 0.00055, 0.0002


def run(N, S, TP, LEV):
    bal = INIT
    cycles = wins = liqs = 0
    peak = INIT; maxdd = 0.0
    i = 1
    while i < n - 1:
        if (np.isnan(RSI[i]) or np.isnan(GAP[i]) or np.isnan(TR[i]) or np.isnan(ATR[i])
                or ATR[i] > 0.80 or GAP[i] < 0.0020):
            i += 1; continue
        side = 1 if RSI[i] <= 35 else (-1 if RSI[i] >= 65 else 0)
        if side == 0:
            i += 1; continue
        e0 = O[i + 1]
        levels = [e0 * (1 - side * S * k) for k in range(N)]   # level 0 = market
        lev_notional = (bal * LEV) / N                          # all N = full leverage
        filled = 0; qty = 0.0; cost = 0.0; fees = 0.0
        outcome = None; j = i + 1
        while j < n:
            bo, bh, bl = O[j], H[j], L[j]
            # 1) fill any levels reached this bar (adverse first)
            new_fill = False
            while filled < N:
                lp = levels[filled]
                reached = (bl <= lp) if side == 1 else (bh >= lp)
                if filled == 0:                      # market leg fills at open
                    lp = e0; reached = True
                if not reached:
                    break
                q = lev_notional / lp
                qty += q; cost += lp * q; fees += lp * q * FEE
                filled += 1; new_fill = True
            avg = cost / qty if qty else e0
            # 2) liquidation: basket equity <= 0
            adv = bl if side == 1 else bh
            unreal = (adv - avg) * qty * side
            if bal + unreal <= 0:
                bal = 0.0; outcome = "LIQ"; liqs += 1; break
            # 3) TP on avg (not on a bar that just added a deeper level)
            if filled > 0 and not new_fill:
                tp_px = avg * (1 + side * TP)
                hit = (bh >= tp_px) if side == 1 else (bl <= tp_px)
                if hit:
                    pnl = (tp_px - avg) * qty * side
                    bal += pnl - fees - tp_px * qty * FEE
                    outcome = "TP"; wins += 1; break
            j += 1
        if outcome is None:           # ran off data with open basket — mark to last close
            pnl = (C[-1] - avg) * qty * side
            bal += pnl - fees
            break
        cycles += 1
        peak = max(peak, bal); maxdd = max(maxdd, (peak - bal) / peak if peak else 0)
        if bal <= 1:
            break
        i = j + 1
    return dict(bal=bal, cycles=cycles, wins=wins, liqs=liqs, maxdd=maxdd)


bh = C[-1] / C[0] - 1
print(f"Grid: ladder in N levels, TP on avg. {bt['timestamp'].iloc[0].date()}->{bt['timestamp'].iloc[-1].date()}")
print(f"BUY & HOLD over same period: {bh*100:+.0f}%   (start $5,000 -> ${INIT*(1+bh):,.0f})\n")
print(f"  {'N':>2} {'spc':>5} {'TP':>5} {'lev':>4} {'cycles':>7} {'win%':>6} {'LIQs':>5} "
      f"{'final$':>12} {'maxDD':>7}")
for LEV in (5.0, 2.0, 1.0):
    for N, S, TP in [(3, 0.005, 0.003), (4, 0.005, 0.003), (5, 0.005, 0.003), (4, 0.010, 0.005)]:
        r = run(N, S, TP, LEV)
        wr = r["wins"] / r["cycles"] * 100 if r["cycles"] else 0
        print(f"  {N:>2} {S*100:>4.1f}% {TP*100:>4.1f}% {LEV:>3.0f}x {r['cycles']:>7} {wr:>5.1f}% "
              f"{r['liqs']:>5} {r['bal']:>+12,.0f} {r['maxdd']*100:>6.0f}%")
