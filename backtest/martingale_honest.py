"""User proposal 2026-06-12: classic 2x martingale on the current bot's entries.

  Entry: deployed v2.x signal (RSI9 35/65 + 15m gap gate + ATR gate)
  Adds:  every 0.5% adverse from last fill, size doubles ($100,200,400,...)
  Cap:   cumulative notional <= balance * leverage
  Exit:  TP 0.5% from basket avg, resting limit (maker, honest fill).
         Exit-and-wait: flat until next fresh signal. NO stop loss (martingale).
  Risk:  cross-margin equity tracked at bar adverse extremes;
         liquidation when equity <= 0.5% maintenance of notional.

Honest fills: adds are resting limits (fill at trigger). TP deferred on any
bar where a new level fills (no wick-order lookahead). If liquidation and TP
are both possible in one bar -> liquidation (pessimistic).
"""
import pandas as pd
import numpy as np
import live_faithful as lf

BASE_NOTIONAL = 100.0
MULT = 2.0
SPACING = 0.005
TP_PCT = 0.005
MAINT = 0.005
INITIAL = 5000.0

RUNS = [
    ("5x lev, 0 fees",   5.0, 0.0,    0.0,     0.0),
    ("5x lev, REAL fees", 5.0, 0.0002, 0.00055, 0.0002),
    ("1x lev, 0 fees",   1.0, 0.0,    0.0,     0.0),
]


def run(bt, lev, maker, taker, slip):
    balance = INITIAL
    peak = INITIAL; max_dd = 0.0
    pos = None; pending = None
    trades = []; ruin = None
    max_legs_seen = 0

    o_a, h_a, l_a = bt["open"].values, bt["high"].values, bt["low"].values
    rsi_a, atr_a = bt["rsi"].values, bt["atr_pct"].values
    tr_a, gap_a = bt["trend"].values, bt["gap"].values
    ts_arr = bt["timestamp"].values

    for i in range(len(bt)):
        if balance <= 10:           # account effectively dead
            if ruin is None:
                ruin = pd.Timestamp(ts_arr[i])
            break
        o, h, l = o_a[i], h_a[i], l_a[i]

        if pos is None and pending is not None:
            side = pending; pending = None
            if not (np.isnan(tr_a[i]) or np.isnan(gap_a[i]) or gap_a[i] < lf.GAP_MIN):
                eff = o * (1 + slip) if side == "LONG" else o * (1 - slip)
                qty = BASE_NOTIONAL / eff
                balance -= eff * qty * taker
                pos = {"side": side, "qty": qty, "avg": eff, "last": eff,
                       "legs": 1, "fees_in": eff * qty * taker, "notional": BASE_NOTIONAL}

        if pos is not None:
            side = pos["side"]
            filled_this_bar = False
            # fill as many doubling levels as this bar reaches (resting limits)
            while True:
                trig = pos["last"] * (1 - SPACING) if side == "LONG" else pos["last"] * (1 + SPACING)
                next_notional = BASE_NOTIONAL * (MULT ** pos["legs"])
                if pos["notional"] + next_notional > balance * lev:
                    break
                hit = (side == "LONG" and l <= trig) or (side == "SHORT" and h >= trig)
                if not hit:
                    break
                qty2 = next_notional / trig
                fee = trig * qty2 * maker
                balance -= fee
                pos["fees_in"] += fee
                pos["avg"] = (pos["avg"] * pos["qty"] + trig * qty2) / (pos["qty"] + qty2)
                pos["qty"] += qty2
                pos["last"] = trig
                pos["legs"] += 1
                pos["notional"] += next_notional
                filled_this_bar = True
            max_legs_seen = max(max_legs_seen, pos["legs"])

            # liquidation check at bar adverse extreme (cross margin)
            adv = l if side == "LONG" else h
            unreal = (adv - pos["avg"]) * pos["qty"] if side == "LONG" else (pos["avg"] - adv) * pos["qty"]
            if balance + unreal <= MAINT * pos["notional"]:
                trades.append({"net": -(balance), "net_live": -(balance), "reason": "LIQ",
                               "hold_h": 0, "exit_ts": pd.Timestamp(ts_arr[i]),
                               "legs": pos["legs"], "mae_pct": 0})
                balance = 0.0
                ruin = pd.Timestamp(ts_arr[i])
                pos = None
                break

            if pos is not None and not filled_this_bar:
                tp = pos["avg"] * (1 + TP_PCT) if side == "LONG" else pos["avg"] * (1 - TP_PCT)
                if (side == "LONG" and h >= tp) or (side == "SHORT" and l <= tp):
                    gross = (tp - pos["avg"]) * pos["qty"] if side == "LONG" else (pos["avg"] - tp) * pos["qty"]
                    net = gross - tp * pos["qty"] * maker
                    balance += net
                    peak = max(peak, balance)
                    max_dd = max(max_dd, (peak - balance) / peak)
                    trades.append({"net": net - pos["fees_in"], "net_live": net, "reason": "TP",
                                   "hold_h": 0, "exit_ts": pd.Timestamp(ts_arr[i]),
                                   "legs": pos["legs"], "mae_pct": 0})
                    pos = None

        if pos is None and pending is None:
            rsi, atrp = rsi_a[i], atr_a[i]
            if np.isnan(rsi) or np.isnan(atrp) or atrp > lf.ATR_MAX:
                continue
            if rsi <= lf.RSI_LONG:
                pending = "LONG"
            elif rsi >= lf.RSI_SHORT:
                pending = "SHORT"

    wins = sum(1 for t in trades if t["net_live"] > 0)
    pf_n = sum(t["net"] for t in trades if t["net"] > 0)
    pf_d = sum(t["net"] for t in trades if t["net"] < 0)
    return {"balance": balance, "trades": len(trades), "wins": wins,
            "max_legs": max_legs_seen, "ruin": ruin, "max_dd": max_dd * 100,
            "pf": abs(pf_n / pf_d) if pf_d < 0 else float("inf")}


df = pd.read_csv(lf.CSV_PATH, parse_dates=["timestamp"])
bt = lf.prep(df)
print(f"Data: {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]}  ({len(bt):,} bars)")
for label, lev, maker, taker, slip in RUNS:
    r = run(bt, lev, maker, taker, slip)
    wr = r["wins"] / r["trades"] * 100 if r["trades"] else 0
    print(f"\n── Martingale 2x [{label}] ──")
    print(f"  Final: ${r['balance']:,.0f}  trades: {r['trades']}  WR: {wr:.1f}%  "
          f"PF: {r['pf']:.2f}  maxDD: {r['max_dd']:.1f}%  deepest basket: {r['max_legs']} legs")
    if r["ruin"] is not None:
        print(f"  *** ACCOUNT DEAD: {r['ruin']} ***")
