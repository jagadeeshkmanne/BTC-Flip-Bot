#!/usr/bin/env python3
"""coinrule_ma_stack.py — honest test of the user's Coinrule strategy, 1h vs 5m.

ENTRY (all on the same closed bar):
  SMA9 crosses above SMA50  AND  SMA50 < SMA100  AND  SMA100 < SMA200
  -> BUY $15 spot (0x leverage), fill next bar open, fee+slip charged.

EXIT (any, checked on closed bars, market exits fill NEXT bar open):
  - SMA9 crosses below SMA200
  - SMA50 > SMA100          (trend-repair exit)
  - SMA100 > SMA200         (trend-repair exit)
  - trailing -5% from peak close since entry
  - +15% from entry: resting LIMIT — fills intrabar when high >= tp
    (limit fills at limit-or-better; if open > tp, fill at open)
  Same-bar TP + any market-exit signal -> take the market exit (pessimistic).

Constraints: max 5 concurrent positions, >= 1h between buys (as written).
Fees: 0.10% spot taker + 0.02% slip per side. Also reports zero-fee gross PF
so signal quality and fee drag are separated.
"""
import pandas as pd

FEE, SLIP = 0.0010, 0.0002
POS_USD, MAX_POS, COOLDOWN_MS = 15.0, 5, 3600 * 1000
TRAIL, TP = 0.05, 0.15

def run(csv, tf_minutes, label):
    df = pd.read_csv(csv)
    c = df["close"]
    for n in (9, 50, 100, 200):
        df[f"ma{n}"] = c.rolling(n).mean()
    df["ts"] = pd.to_datetime(df["timestamp"]).astype("int64") // 10**6

    cross_up = (df["ma9"] > df["ma50"]) & (df["ma9"].shift(1) <= df["ma50"].shift(1))
    entry_sig = cross_up & (df["ma50"] < df["ma100"]) & (df["ma100"] < df["ma200"])
    cross_dn200 = (df["ma9"] < df["ma200"]) & (df["ma9"].shift(1) >= df["ma200"].shift(1))
    repair = (df["ma50"] > df["ma100"]) | (df["ma100"] > df["ma200"])

    o, h, lo, cl = df["open"].values, df["high"].values, df["low"].values, c.values
    esig, x200, xrep = entry_sig.values, cross_dn200.values, repair.values
    ts = df["ts"].values

    open_pos = []   # dicts: entry, peak, qty
    pend_entry = False
    pend_exits = []  # positions to market-exit at this bar's open
    last_buy = -10**18
    trades = []      # (net_pct, gross_pct, reason)

    for i in range(200, len(df)):
        # 1) fills at this bar's open from last bar's signals
        if pend_exits:
            for p, reason in pend_exits:
                fill = o[i] * (1 - SLIP)
                gross = fill / p["entry"] - 1
                net = (fill * (1 - FEE)) / (p["entry"] * (1 + FEE)) - 1
                trades.append((net, gross, reason))
                if p in open_pos:
                    open_pos.remove(p)
            pend_exits = []
        if pend_entry:
            if len(open_pos) < MAX_POS and ts[i] - last_buy >= COOLDOWN_MS:
                fill = o[i] * (1 + SLIP)
                open_pos.append({"entry": fill, "peak": fill})
                last_buy = ts[i]
            pend_entry = False

        # 2) intrabar TP limits + closed-bar exit signals
        for p in list(open_pos):
            tp_px = p["entry"] * (1 + TP)
            tp_hit = h[i] >= tp_px
            p["peak"] = max(p["peak"], cl[i])
            stop_sig = (x200[i] or xrep[i] or cl[i] <= p["peak"] * (1 - TRAIL))
            if stop_sig:               # pessimistic: market exit beats TP
                reason = ("MA9<MA200" if x200[i] else
                          "TREND_REPAIR" if xrep[i] else "TRAIL-5%")
                pend_exits.append((p, reason))
                open_pos.remove(p)     # reserve; books at next open
            elif tp_hit:
                fill = max(tp_px, o[i]) * (1 - SLIP)
                gross = fill / p["entry"] - 1
                net = (fill * (1 - FEE)) / (p["entry"] * (1 + FEE)) - 1
                trades.append((net, gross, "TP+15%"))
                open_pos.remove(p)

        # 3) entry signal on this closed bar -> fill next open
        if esig[i]:
            pend_entry = True

    if not trades:
        print(f"{label}: no trades")
        return
    nets = [t[0] for t in trades]
    gw = sum(t[1] for t in trades if t[1] > 0)
    gl = -sum(t[1] for t in trades if t[1] < 0)
    nw = sum(n for n in nets if n > 0)
    nl = -sum(n for n in nets if n < 0)
    pf_g = gw / gl if gl else float("inf")
    pf_n = nw / nl if nl else float("inf")
    win = sum(1 for n in nets if n > 0) / len(nets) * 100
    pnl = sum(n * POS_USD for n in nets)
    reasons = {}
    for _, _, r in trades:
        reasons[r] = reasons.get(r, 0) + 1
    yrs = (ts[-1] - ts[200]) / (365.25 * 86400 * 1000)
    print(f"{label:>14}: {len(trades):4d} trades ({len(trades)/yrs:5.1f}/yr) | "
          f"win {win:4.1f}% | avg net {sum(nets)/len(nets)*100:+.3f}% | "
          f"PF net {pf_n:4.2f} / zero-fee {pf_g:4.2f} | "
          f"P&L@$15 ${pnl:+8.2f} | {reasons}")

for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"]:
    for tf, mins in [("1h", 60), ("5m", 5)]:
        run(f"/Users/jags/Desktop/BTC-Flip-Bot/data/cache/{sym}_{tf}.csv", mins, f"{sym} {tf}")
