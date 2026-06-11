"""Tafabot-style hedge-cycle bot, honest equity accounting.

User spec 2026-06-11: open LONG + SHORT simultaneously ($100 margin x 3x =
$300 notional per leg). When a leg reaches +$1.50 profit (0.5% move), close it
(realize the small win) and re-open it at market to restore the hedge.
Taker fees 0.055% per fill. No SL on legs (hedge "protects").

Two ledgers reported:
  REALIZED  — what the trade history shows (the Tafabot dashboard view)
  EQUITY    — balance + unrealized P&L of open legs (the truth)
"""
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"
NOTIONAL = 300.0      # $100 margin x 3x per leg
TP_USD = 1.50         # close leg at +$1.50 (0.5% move on $300)
FEE = 0.00055
START_BAL = 1000.0    # account holding the two $100 margins + buffer

df = pd.read_csv(CSV, parse_dates=["timestamp"])
df = df[df["timestamp"] >= "2024-01-01"].reset_index(drop=True)
c = df["close"].values
ts = df["timestamp"].values

bal = START_BAL
legs = {}  # side -> entry price
realized_wins = 0
realized_pnl = 0.0
fees_paid = 0.0
worst_equity = START_BAL
worst_unreal = 0.0

def open_leg(side, px):
    global bal, fees_paid
    fee = NOTIONAL * FEE
    bal -= fee
    fees_paid += fee
    legs[side] = px

def qty(px):
    return NOTIONAL / px

# open initial hedge
open_leg("L", c[0]); open_leg("S", c[0])

for i in range(1, len(c)):
    px = c[i]
    for side in ("L", "S"):
        if side not in legs:
            continue
        e = legs[side]
        pnl = (px - e) * qty(e) if side == "L" else (e - px) * qty(e)
        if pnl >= TP_USD:
            fee = NOTIONAL * FEE
            bal += pnl - fee
            fees_paid += fee
            realized_pnl += pnl - fee
            realized_wins += 1
            del legs[side]
            open_leg(side, px)   # immediately re-hedge at market

    unreal = 0.0
    for side, e in legs.items():
        unreal += (px - e) * qty(e) if side == "L" else (e - px) * qty(e)
    eq = bal + unreal
    worst_equity = min(worst_equity, eq)
    worst_unreal = min(worst_unreal, unreal)

px = c[-1]
unreal = sum((px - e) * qty(e) if s == "L" else (e - px) * qty(e) for s, e in legs.items())
eq = bal + unreal

print(f"Window: {pd.Timestamp(ts[0]).date()} -> {pd.Timestamp(ts[-1]).date()}  "
      f"(BTC {c[0]:,.0f} -> {c[-1]:,.0f})")
print(f"\n── REALIZED ledger (what the dashboard shows) ──")
print(f"  Closed trades: {realized_wins}   WIN RATE: 100.0%   realized P&L: ${realized_pnl:+,.2f}")
print(f"\n── EQUITY ledger (the truth) ──")
print(f"  Balance: ${bal:,.2f}   open-leg unrealized: ${unreal:+,.2f}")
print(f"  EQUITY: ${eq:,.2f}  (started ${START_BAL:,.0f}, net {eq-START_BAL:+,.2f})")
print(f"  Worst equity: ${worst_equity:,.2f}   worst open-leg drawdown: ${worst_unreal:+,.2f}")
print(f"  Total fees paid: ${fees_paid:,.2f}")
