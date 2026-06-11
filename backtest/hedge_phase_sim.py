"""User spec 2026-06-11: hedge phase simulation.

Phase 1 (hedged): L + S, $2,500 notional each (1x combined on $5K).
  Winner closed at +1% (resting limit, maker).
Phase 2 (naked): loser held with NO stop, three give-up policies:
  a) reverse_1pct : wait until the loser leg itself is +1% (2% reversal)
  b) breakeven    : wait until price returns to entry (loser exits at 0%)
  c) cut_half     : exit when loser recovers to -0.5% (accept half the loss)
All exits are resting limits (maker). Entries taker + slip.
Funding while naked: 0.01%/8h — naked LONG pays, naked SHORT receives.
While hedged, funding nets ~zero. After flat, re-open hedge next bar.
No entry signal — isolates the structure itself (the user's point: any edge
must come from WHEN we open, which this baseline deliberately lacks).
"""
import pandas as pd
import numpy as np

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"
NOTIONAL = 2500.0
TP = 0.01
FEE_T, FEE_M, SLIP = 0.00055, 0.0002, 0.0002
FUND_8H = 0.0001          # 0.01% per 8h, standard BTC average
FUND_BAR = FUND_8H / 96   # per 5m bar

df = pd.read_csv(CSV, parse_dates=["timestamp"])
df = df[df["timestamp"] >= "2024-01-01"].reset_index(drop=True)
o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
n = len(df)

def run(policy):
    bal = 5000.0
    state = "FLAT"        # FLAT / HEDGED / NAKED
    entry = None          # hedge entry price
    naked_side = None
    naked_bars = []
    cur_naked = 0
    worst_unreal = 0.0
    cycles = 0
    funding_net = 0.0

    i = 1
    while i < n:
        if state == "FLAT":
            eff_l = o[i] * (1 + SLIP); eff_s = o[i] * (1 - SLIP)
            bal -= NOTIONAL * FEE_T * 2
            entry = o[i]                       # track at mid; legs symmetric
            state = "HEDGED"
        elif state == "HEDGED":
            up = h[i] >= entry * (1 + TP)
            dn = l[i] <= entry * (1 - TP)
            if up or dn:
                # winner closes at +1% limit (maker); pessimistic: if both,
                # close the SHORT winner first (leaves naked LONG paying funding)
                if dn and not up:
                    winner = "S"
                elif up and not dn:
                    winner = "L"
                else:
                    winner = "S"
                bal += NOTIONAL * TP - NOTIONAL * (1 + TP) * FEE_M
                naked_side = "L" if winner == "S" else "S"
                state = "NAKED"
                cur_naked = 0
        else:  # NAKED — loser leg open, entry at `entry`, currently ~-1%
            cur_naked += 1
            # funding
            f = NOTIONAL * FUND_BAR
            if naked_side == "L":
                bal -= f; funding_net -= f
            else:
                bal += f; funding_net += f
            # target recovery level for the loser
            tgt = {"reverse_1pct": TP, "breakeven": 0.0, "cut_half": -0.005}[policy]
            if naked_side == "L":
                tgt_px = entry * (1 + tgt)
                hit = h[i] >= tgt_px
                unreal = (l[i] / entry - 1) * NOTIONAL
            else:
                tgt_px = entry * (1 - tgt)
                hit = l[i] <= tgt_px
                unreal = (1 - h[i] / entry) * NOTIONAL
            worst_unreal = min(worst_unreal, unreal)
            if hit:
                bal += NOTIONAL * tgt - tgt_px / entry * NOTIONAL * FEE_M
                naked_bars.append(cur_naked)
                state = "FLAT"
                cycles += 1
        i += 1

    # mark open position at end
    open_mark = 0.0
    if state == "HEDGED":
        open_mark = 0.0
    elif state == "NAKED":
        open_mark = ((c[-1] / entry - 1) if naked_side == "L" else (1 - c[-1] / entry)) * NOTIONAL
        naked_bars.append(cur_naked)
    eq = bal + open_mark
    nb = pd.Series(naked_bars) if naked_bars else pd.Series([0])
    return {"eq": eq, "cycles": cycles, "worst": worst_unreal,
            "naked_med_h": nb.median() / 12, "naked_max_d": nb.max() / 288,
            "funding": funding_net, "end_state": state, "open_mark": open_mark}

print(f"Window: {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}  "
      f"($2,500/leg, winner TP +1%, maker exits, funding while naked)\n")
print(f"{'loser policy':>14} {'cycles':>7} {'final equity':>13} {'net':>9} "
      f"{'worst leg':>10} {'naked med(h)':>13} {'naked max(d)':>13} {'funding':>9}")
for p in ("reverse_1pct", "breakeven", "cut_half"):
    r = run(p)
    print(f"{p:>14} {r['cycles']:>7} {r['eq']:>13,.2f} {r['eq']-5000:>+9.2f} "
          f"{r['worst']:>10.2f} {r['naked_med_h']:>13.1f} {r['naked_max_d']:>13.1f} "
          f"{r['funding']:>+9.2f}")
    if r["end_state"] == "NAKED":
        print(f"{'':>14} (still naked at end, open leg marked {r['open_mark']:+,.2f})")
