#!/usr/bin/env python3
"""validate_fresh_honest.py — ground-truth audit of fresh_honest.run()
(user goal 2026-06-12: 'identify bugs in backtest script').

T1 single-leg TP: synthetic path, net must equal closed-form to 1e-9.
T2 DCA -> basket TP (v2.2 1%): hand-computed ladder avg/qty/fees/net.
T3 GRID mode: L2 fill, then both leg-TPs in one bar — two hand-computed nets.
T4 GRID rung recycling: dip-bank-dip-bank must book exactly 2 TP_L2LEG.
T5 BE-DCA honest fill on gap: bar OPENS below avg -> exit must fill at open
   (the artifact case — must NOT book at avg).
T6 conservation on real data: balance == INITIAL + sum(trade nets), baseline
   and grid, real fees.
T7 random-signal null on real data, zero fees: PF must be ~1 (no phantom
   edge or phantom bleed in the engine itself).
"""
import numpy as np
import pandas as pd
import fresh_honest as fh

PASS = []
def check(name, ok, detail=""):
    PASS.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")

FEE, SLIP = 0.00055, 0.0002
N0 = fh.PER_LEG_NOTIONAL

def mkbt(rows):
    """rows: list of (o,h,l,c,rsi). Adds constant trend/gap/atr columns."""
    t0 = pd.Timestamp("2024-01-01")
    df = pd.DataFrame([{"timestamp": t0 + pd.Timedelta(minutes=5*k),
                        "open": o, "high": h, "low": l, "close": c,
                        "rsi": rsi, "atr_pct": 0.10, "trend": 1.0, "gap": 0.01}
                       for k, (o, h, l, c, rsi) in enumerate(rows)])
    return df

# ── T1: single-leg TP, hand-computed ──
bt = mkbt([(100,100,100,100, 20),      # signal bar (RSI<=35 -> LONG)
           (100,100,100,100, 50),      # entry at open 100
           (100,100.8,100,100.6, 50),  # TP 0.5% hit
           (100.6,100.6,100.6,100.6, 50)])
r = fh.run(bt, tp_dca=0.01, time_sl=10**6, fill="honest", fee=FEE, slip=SLIP)
avg = 100*(1+SLIP); qty = N0/avg
tp = avg*1.005; eff = max(tp,100)*(1-SLIP)
net_hand = (eff-avg)*qty - eff*qty*FEE - N0*FEE
got = r["trades"][0][0] if r["trades"] else None
check("T1 single-leg TP == closed form", got is not None and abs(got-net_hand) < 1e-9,
      f"engine {got:.6f} vs hand {net_hand:.6f}")

# ── T2: DCA -> basket TP(1%), hand-computed ──
bt = mkbt([(100,100,100,100, 20),
           (100,100,100,100, 50),          # entry 100
           (100,100,99.0,99.2, 50),        # L2 fills (trig), exits deferred
           (99.2,101.0,99.2,100.9, 50),    # basket TP at avg*1.01
           (100.9,)*4 + (50,)])
r = fh.run(bt, tp_dca=0.01, time_sl=10**6, fill="honest", fee=FEE, slip=SLIP)
l1 = 100*(1+SLIP); q1 = N0/l1
trig = l1*0.995; raw = min(trig, 100.0); l2 = raw*(1+SLIP); q2 = N0/l2
avg2 = (l1*q1 + l2*q2)/(q1+q2)
tp = avg2*1.01; eff = max(tp, 99.2)*(1-SLIP)
net_hand = (eff-avg2)*(q1+q2) - eff*(q1+q2)*FEE - (l1*q1+l2*q2)*FEE
got = r["trades"][0][0] if r["trades"] else None
check("T2 DCA basket TP == hand ladder", got is not None and abs(got-net_hand) < 1e-9,
      f"engine {got:.6f} vs hand {net_hand:.6f}")

# ── T3: GRID both legs bank in one bar, hand-computed ──
bt = mkbt([(100,100,100,100, 20),
           (100,100,100,100, 50),          # entry 100
           (100,100,99.0,99.2, 50),        # L2 fills, deferred
           (99.2,100.8,99.2,100.7, 50),    # tp2 (~l2*1.005) AND tp1 (l1*1.005)
           (100.7,)*4 + (50,)])
r = fh.run(bt, tp_dca=0.01, time_sl=10**6, fill="honest", fee=FEE, slip=SLIP,
           per_leg_tp=True)
tp2 = l2*1.005; f2 = max(tp2, 99.2)*(1-SLIP)
net2_hand = (f2-l2)*q2 - f2*q2*FEE - l2*q2*FEE
tp1 = l1*1.005; f1 = max(tp1, 99.2)*(1-SLIP)
net1_hand = (f1-l1)*q1 - f1*q1*FEE - l1*q1*FEE
nets = sorted(t[0] for t in r["trades"])
hand = sorted([net2_hand, net1_hand])
ok = len(nets) == 2 and all(abs(a-b) < 1e-9 for a, b in zip(nets, hand))
check("T3 grid leg TPs == hand calc (2 closes)", ok,
      f"engine {[f'{x:.4f}' for x in nets]} vs hand {[f'{x:.4f}' for x in hand]}")

# ── T4: GRID rung recycling: dip-bank-dip-bank = exactly 2 TP_L2LEG ──
bt = mkbt([(100,100,100,100, 20),
           (100,100,100,100, 50),            # entry 100
           (100,100,99.0,99.3, 50),          # L2 #1 fills
           (99.3,100.2,99.3,100.1, 50),      # tp2#1 (~100.04) banks; tp1 not (100.52)
           (100.1,100.1,99.0,99.3, 50),      # L2 #2 refills
           (99.3,100.2,99.3,100.1, 50),      # tp2#2 banks
           (100.1,)*4 + (50,)])
r = fh.run(bt, tp_dca=0.01, time_sl=10**6, fill="honest", fee=FEE, slip=SLIP,
           per_leg_tp=True)
check("T4 grid rung recycles (2x TP_L2LEG, L1 still open)",
      r["exits"]["TP_L2LEG"] == 2 and r["exits"]["TP"] == 0,
      f"exits {dict((k,v) for k,v in r['exits'].items() if v)}")

# ── T5: BE-DCA honest fill on adverse gap: must fill at OPEN, not avg ──
bt = mkbt([(100,100,100,100, 20),
           (100,100,100,100, 50),            # entry 100
           (100,100,99.0,99.2, 50),          # L2 fills
           *[(99.2,99.3,99.1,99.2, 50)]*5,   # 5 bars BELOW avg (BE not yet armed)
           (98.5,99.9,98.4,99.0, 50),        # 6th bar after L2: BE arms NOW; opens BELOW avg
           (99.0,)*4 + (50,)])
r = fh.run(bt, tp_dca=0.01, time_sl=10**6, fill="honest", fee=FEE, slip=SLIP)
# sl_px = avg2; bar opens 98.5 < avg2 -> honest fill at open
eff = 98.5*(1-SLIP)
net_hand = (eff-avg2)*(q1+q2) - eff*(q1+q2)*FEE - (l1*q1+l2*q2)*FEE
got = next((t[0] for t in r["trades"] if t[1] == "BE-DCA"), None)
check("T5 BE-DCA gap fills at OPEN (artifact-proof)",
      got is not None and abs(got-net_hand) < 1e-9 and net_hand < 0,
      f"engine {got if got is not None else 'none'} vs hand {net_hand:.4f}")

# ── T6: conservation on real data ──
bt_real = fh.prep()
for label, kw in (("baseline", {}), ("grid", dict(per_leg_tp=True))):
    r = fh.run(bt_real, tp_dca=0.01, time_sl=144, fill="honest", fee=FEE, slip=SLIP,
               sl_l1=0.006, fixed_cap=True, **kw)
    tot = sum(t[0] for t in r["trades"])
    ok = abs(r["balance"] - (fh.INITIAL_BAL + tot)) < 1e-6
    check(f"T6 conservation ({label}): balance == initial + Σnets", ok,
          f"balance {r['balance']:.2f} vs {fh.INITIAL_BAL + tot:.2f}")

# ── T7: random-signal null at zero fees: PF ~ 1 ──
rng = np.random.default_rng(11)
sides = np.zeros(len(bt_real), dtype=np.int8)
idx = rng.choice(len(bt_real), 20000, replace=False)
sides[idx] = rng.choice([1, -1], len(idx))
r = fh.run(bt_real, tp_dca=0.01, time_sl=144, fill="honest", fee=0.0, slip=0.0,
           sl_l1=0.006, fixed_cap=True, signal_sides=sides)
nets = np.array([t[0] for t in r["trades"]])
gw, gl = nets[nets > 0].sum(), nets[nets < 0].sum()
pf = abs(gw/gl)
check("T7 random-signal null PF in [0.93, 1.07]", 0.93 <= pf <= 1.07,
      f"PF {pf:.3f} over {len(nets):,} trades")

print(f"\n{sum(PASS)}/{len(PASS)} checks passed" + ("" if all(PASS) else "  — FAILURES ABOVE"))
