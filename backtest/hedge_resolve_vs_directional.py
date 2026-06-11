"""User 2026-06-11: hedge-open + indicator resolves which leg survives.

Proof-by-simulation that the hedge wrapper adds only fees:

  A) DIRECTIONAL: EMA20/50 cross on 15m. Cross up -> long $300; cross down ->
     flip to short $300. (The indicator's exposure path, implemented directly.)
  B) HEDGE-RESOLVE: hold L+S ($300 each). Indicator up -> close S (long
     survives). On opposite signal -> re-open the hedge, then close L (short
     survives). I.e., same exposure path as A, reached via hedge states.

Same data, same signals, same fills model (signal at close -> fill next open,
taker 0.055% + 0.02% slip per fill). Equity tracked identically.
"""
import pandas as pd
import numpy as np

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BYBIT_BTCUSDT_15m.csv"
NOTIONAL = 300.0
FEE, SLIP = 0.00055, 0.0002

df = pd.read_csv(CSV, parse_dates=["timestamp"])
df = df[df["timestamp"] >= "2024-01-01"].reset_index(drop=True)
o, c = df["open"].values, df["close"].values
e20 = pd.Series(c).ewm(span=20, adjust=False).mean().values
e50 = pd.Series(c).ewm(span=50, adjust=False).mean().values

# signal: +1 / -1 from last CLOSED bar, acted on at next bar open
sig = np.where(e20 > e50, 1, -1)

def fill(px, side_buy):
    return px * (1 + SLIP) if side_buy else px * (1 - SLIP)

def run(mode):
    bal = 1000.0
    legs = {}            # side -> entry
    fills = 0
    fees = 0.0

    def open_leg(side, px):
        nonlocal bal, fills, fees
        eff = fill(px, side == "L")
        f = NOTIONAL * FEE
        bal -= f; fees += f; fills += 1
        legs[side] = eff

    def close_leg(side, px):
        nonlocal bal, fills, fees
        e = legs.pop(side)
        eff = fill(px, side != "L")
        q = NOTIONAL / e
        pnl = (eff - e) * q if side == "L" else (e - eff) * q
        f = NOTIONAL * FEE
        bal += pnl - f; fees += f; fills += 1

    last = 0
    for i in range(201, len(c)):
        s = sig[i - 1]
        if s == last:
            continue
        px = o[i]
        if mode == "directional":
            if "L" in legs and s == -1: close_leg("L", px)
            if "S" in legs and s == 1:  close_leg("S", px)
            if s == 1 and "L" not in legs: open_leg("L", px)
            if s == -1 and "S" not in legs: open_leg("S", px)
        else:  # hedge-resolve: restore hedge, then drop the wrong leg
            if "L" not in legs: open_leg("L", px)
            if "S" not in legs: open_leg("S", px)
            close_leg("S" if s == 1 else "L", px)
        last = s

    # mark remaining legs at final close
    px = c[-1]
    unreal = sum((px - e) * NOTIONAL / e if s == "L" else (e - px) * NOTIONAL / e
                 for s, e in legs.items())
    return bal + unreal, fills, fees

for mode in ("directional", "hedge-resolve"):
    eq, fills, fees = run(mode)
    print(f"{mode:>14}: final equity ${eq:,.2f}  (net {eq-1000:+,.2f})  "
          f"fills: {fills}  fees+slip drag: ${fees:,.2f}")
print(f"\nWindow: {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}, "
      f"15m EMA20/50, $300 notional/leg")
