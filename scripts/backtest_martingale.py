#!/usr/bin/env python3
"""backtest_martingale.py — faithful reconstruction of the user's "BTC Production Martingale".

Rules (from the Pine script): RSI(14)<30 -> long base; RSI>70 -> short base. Add a safety order
(size x2) each time price moves 0.6% against the average, up to 6 steps. TP at 0.8% above avg
(decays 0.12%/step, min 0.15%). Hard stop 5% from avg. Sizing: geometric so 6 orders sum to
total_capital ($2000). Tests recent ~20 days (to match the 77% claim) AND the full history +
year-by-year + worst drawdown — because martingales look great until a trend blows through them.
Honest taker fees (0.055%/side + 0.05% slip) — the strategy itself ignores fees.
"""
from __future__ import annotations
import os, sys
import numpy as np
import pandas as pd
pd.set_option('future.no_silent_downcasting', True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bt_helpers as bt

FEE = bt.FEE_PCT; SLIP = bt.SLIP_PCT; COST = FEE + SLIP
CAP = 2000.0; MAXSTEP = 6; MULT = 2.0; STEP = 0.006
BASE_TP = 0.008; TP_DECAY = 0.0012; MIN_TP = 0.0015; HARD = 0.05
BASE = CAP * (MULT - 1) / (MULT**MAXSTEP - 1)


def run(df):
    o = df["open"].values; c = df["close"].values
    rsi = bt.rsi(df["close"], 14).values
    n = len(df)
    bal = CAP
    side = 0; qty = 0.0; notional = 0.0; fees = 0.0; step = 0; alloc = BASE
    eq = np.full(n, CAP)
    deals = wins = blowups = 0; worst_deal = 0.0

    def avg():
        return notional / qty if qty > 0 else 0.0

    for i in range(20, n - 1):
        fill = o[i + 1]
        px = c[i]
        if side == 0:
            if rsi[i] < 30:
                side = 1
            elif rsi[i] > 70:
                side = -1
            if side != 0:
                qty = (alloc * (1 - COST)) / fill; notional = alloc; fees = alloc * COST; step = 1; alloc = BASE * MULT
        else:
            a = avg()
            tp = max(MIN_TP, BASE_TP - (step - 1) * TP_DECAY)
            adverse = (px < a * (1 - STEP)) if side == 1 else (px > a * (1 + STEP))
            tp_hit = (px > a * (1 + tp)) if side == 1 else (px < a * (1 - tp))
            sl_hit = (px < a * (1 - HARD)) if side == 1 else (px > a * (1 + HARD))
            if adverse and step < MAXSTEP:
                q = (alloc * (1 - COST)) / fill
                qty += q if side == 1 else q; notional += alloc; fees += alloc * COST
                step += 1; alloc *= MULT
            elif tp_hit or sl_hit:
                exitn = qty * fill
                pnl = (exitn - notional if side == 1 else notional - exitn) - fees - exitn * COST
                bal += pnl; deals += 1; wins += int(pnl > 0); worst_deal = min(worst_deal, pnl)
                if sl_hit: blowups += 1
                side = 0; qty = notional = fees = 0.0; step = 0; alloc = BASE
        # mark equity
        if side != 0:
            mark = qty * c[i + 1]
            unreal = (mark - notional) if side == 1 else (notional - mark)
            eq[i + 1] = bal + unreal
        else:
            eq[i + 1] = bal
    s = pd.Series(eq, index=pd.to_datetime(df["timestamp"])).iloc[20:]
    return s, deals, wins, blowups, worst_deal


def report(label, s, deals, wins, blow, worst):
    tot = (s.iloc[-1] / CAP - 1) * 100
    dd = (s / s.cummax() - 1).min() * 100
    wr = wins / deals * 100 if deals else 0
    print(f"  {label}")
    print(f"    return {tot:+.0f}%  | equity ${s.iloc[-1]:,.0f} (from ${CAP:,.0f})  | maxDD {dd:.0f}%")
    print(f"    deals {deals}  win% {wr:.0f}  | HARD-STOP blowups {blow}  worst single deal ${worst:,.0f}")


def main():
    df = bt.load("BTCUSDT", "4h")
    print("=" * 78)
    print("MARTINGALE RECONSTRUCTION — BTC 4h (honest fees). Beware: martingales look great")
    print("until a trend blows through the safety ladder.")
    print("=" * 78)
    # recent ~20 days (to check the 77% claim)
    recent = df[df["timestamp"] >= df["timestamp"].iloc[-1] - pd.Timedelta(days=22)].reset_index(drop=True)
    sr, d, w, b, wo = run(recent); report("RECENT ~20 DAYS:", sr, d, w, b, wo)
    print()
    sf, d, w, b, wo = run(df); report("FULL HISTORY (2020-2026):", sf, d, w, b, wo)
    print("\n  YEAR-BY-YEAR return%:")
    yr = pd.to_datetime(df["timestamp"]).dt.year
    for y in sorted(set(yr)):
        seg = df[yr == y].reset_index(drop=True)
        if len(seg) < 100: continue
        sy, dd2, ww, bb, _ = run(seg)
        print(f"    {y}: {(sy.iloc[-1]/CAP-1)*100:+7.0f}%   (deals {dd2}, blowups {bb}, maxDD {(sy/sy.cummax()-1).min()*100:.0f}%)")


if __name__ == "__main__":
    main()
