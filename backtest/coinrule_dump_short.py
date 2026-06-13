#!/usr/bin/env python3
"""coinrule_dump_short.py — honest test of the dump-continuation short rule.

ENTRY:  price decrease <= -4% over the last 2 hours (close vs close 24 x 5m
        bars ago, closed bars) -> market SELL $15 notional, fill next bar
        open, isolated margin at leverage L (margin = $15/L).
EXIT:   resting BUY limit at entry * 0.97 (-3% from sold price). No stop.
LIQ:    short liquidated when high >= entry * (1 + 1/L - 0.5% MMR);
        margin lost. Checked before the TP on the same bar (pessimistic).
LIMITS: max 10 open positions, >= 5 min between entries (rule as written;
        the 10-execution campaign cap is ignored to measure the edge).
FEES:   taker 0.055% + 0.02% slip on entry; maker 0.02% on TP limit.
        Funding ignored (shorts usually RECEIVE ~0.01%/8h — noted, would
        add roughly +8% APR on notional while open, but flips against
        crowded shorts in bears).
Open positions at data end are marked to market at the last close.
"""
import pandas as pd

ENTRY_FEE, SLIP, EXIT_FEE = 0.00055, 0.0002, 0.0002
NOTIONAL, MAX_POS, COOLDOWN_BARS = 15.0, 10, 1
DROP, TP, MMR, WINDOW = 0.04, 0.03, 0.005, 24  # 24 x 5m = 2h

def run(csv, label, L):
    df = pd.read_csv(csv)
    o, h, lo, cl = (df[k].values for k in ("open", "high", "low", "close"))
    c = df["close"]
    sig = (c / c.shift(WINDOW) - 1 <= -DROP).values

    open_pos, trades = [], []   # trades: (net_usd, reason, bars_held)
    pend = False
    last_entry_i = -10**9
    eq_real = 0.0               # realized P&L
    worst_book = 0.0            # worst MTM of book (realized + unrealized)
    max_conc = 0

    for i in range(WINDOW + 1, len(df)):
        if pend and len(open_pos) < MAX_POS and i - last_entry_i > COOLDOWN_BARS:
            entry = o[i] * (1 - SLIP)            # short fill, adverse slip
            open_pos.append({"e": entry, "i": i,
                             "liq": entry * (1 + 1 / L - MMR),
                             "tp": entry * (1 - TP)})
            eq_real -= NOTIONAL * ENTRY_FEE
            last_entry_i = i
        pend = False
        for p in list(open_pos):
            if h[i] >= p["liq"]:                 # liquidation first (pessimistic)
                trades.append((-NOTIONAL / L, "LIQ", i - p["i"]))
                eq_real += -NOTIONAL / L
                open_pos.remove(p)
            elif lo[i] <= p["tp"]:
                fill = min(o[i], p["tp"])
                gross = (p["e"] - fill) / p["e"] * NOTIONAL
                net = gross - NOTIONAL * EXIT_FEE
                trades.append((net, "TP", i - p["i"]))
                eq_real += net
                open_pos.remove(p)
        if sig[i]:
            pend = True
        max_conc = max(max_conc, len(open_pos))
        unreal = sum((p["e"] - cl[i]) / p["e"] * NOTIONAL for p in open_pos)
        worst_book = min(worst_book, eq_real + unreal)

    unreal = sum((p["e"] - cl[-1]) / p["e"] * NOTIONAL for p in open_pos)
    tp_n = sum(1 for t in trades if t[1] == "TP")
    liq_n = sum(1 for t in trades if t[1] == "LIQ")
    closed = len(trades)
    yrs = len(df) / (288 * 365.25)
    holds = sorted(t[2] for t in trades if t[1] == "TP")
    med_hold = holds[len(holds) // 2] * 5 / 60 if holds else 0
    margin_book = NOTIONAL / L * MAX_POS
    print(f"{label:>11} L={L:<2}: {closed:4d} closed ({closed/yrs:5.0f}/yr) | "
          f"TP {tp_n} ({tp_n/max(closed,1)*100:4.1f}%) LIQ {liq_n:3d} | "
          f"realized ${eq_real:+8.2f} | open {len(open_pos)} (unreal ${unreal:+7.2f}) | "
          f"TOTAL ${eq_real+unreal:+8.2f} on ${margin_book:.0f} margin | "
          f"worst book ${worst_book:+8.2f} | med hold {med_hold:.1f}h")

for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
    for L in [1, 2, 3, 5, 10]:
        run(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_5m.csv", sym, L)
    print()
