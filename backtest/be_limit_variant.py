"""User proposal 2026-06-12: after L2 fills, rest a sell/buy LIMIT at avg.
Exit at breakeven-minus-fees when price returns to avg ("only lose the fee").
No BE stop, no trail, no L2 TP — the avg limit fills before any TP could.
Time-SL kept as the backstop for positions that never return to avg.
1-leg logic unchanged (TP 0.5% limit, SL 0.6% honest fill).

Fees modeled per fill type: maker 0.02% on limits (L2 entry, TP, avg-limit),
taker 0.055% + 0.02% slip on markets (L1 entry, SL, trend-flip, time-SL).
"""
import pandas as pd
import numpy as np
from datetime import timedelta
import live_faithful as lf

FEE_MODES = {
    "PAPER (0 fees, 0 slip)": {"maker": 0.0, "taker": 0.0, "slip": 0.0},
    "REAL maker/taker mix":   {"maker": 0.0002, "taker": 0.00055, "slip": 0.0002},
}
CONFIGS = {"v2.1 (6h time-SL)": 72, "v2.2 (12h time-SL)": 144}


def run(bt, time_sl_bars, maker, taker, slip):
    balance = lf.INITIAL_BAL
    peak_closed = balance; max_dd = 0.0
    position = None; pending = None; pause_until = None
    daily_loss = {}; trades = []
    exits = {"TP": 0, "BE_LIMIT": 0, "SL": 0, "TREND_FLIP": 0, "TIME_SL": 0}

    ts_arr = bt["timestamp"].values
    o_a, h_a, l_a, c_a = (bt["open"].values, bt["high"].values,
                          bt["low"].values, bt["close"].values)
    rsi_a, atr_a = bt["rsi"].values, bt["atr_pct"].values
    tr_a, gap_a = bt["trend"].values, bt["gap"].values

    def leg_qty(px):
        return (lf.INITIAL_BAL * 0.95 * lf.LEVERAGE) / px / 2.0  # fixed-$5K

    def close_pos(pos, ts, exit_px, reason, is_market):
        nonlocal balance, peak_closed, max_dd, pause_until
        side = pos["side"]
        eff = exit_px
        if is_market:
            eff = exit_px * (1 - slip) if side == "LONG" else exit_px * (1 + slip)
        fee = taker if is_market else maker
        qty, avg = pos["qty"], pos["avg"]
        gross = (eff - avg) * qty if side == "LONG" else (avg - eff) * qty
        net_live = gross - eff * qty * fee
        net_full = net_live - pos["fees_in"]
        balance += net_live
        peak_closed = max(peak_closed, balance)
        max_dd = max(max_dd, (peak_closed - balance) / peak_closed)
        d = pd.Timestamp(ts).date()
        if net_live < 0:
            daily_loss[d] = daily_loss.get(d, 0.0) + net_live
        if net_live < 0 and reason != "BE_LIMIT":
            pause_until = pd.Timestamp(ts) + timedelta(minutes=lf.COOLDOWN_MIN)
        trades.append({"net": net_full, "net_live": net_live, "reason": reason,
                       "hold_h": (pd.Timestamp(ts) - pos["open_ts"]).total_seconds() / 3600,
                       "exit_ts": pd.Timestamp(ts), "legs": pos["legs"],
                       "mae_pct": pos["mae_pct"]})
        exits[reason] += 1

    for i in range(len(bt)):
        ts = ts_arr[i]
        o, h, l, c = o_a[i], h_a[i], l_a[i], c_a[i]
        trend, gap = tr_a[i], gap_a[i]

        if position is None and pending is not None:
            side = pending; pending = None
            tsp = pd.Timestamp(ts)
            ok = not (np.isnan(trend) or np.isnan(gap) or gap < lf.GAP_MIN
                      or (pause_until is not None and tsp < pause_until)
                      or daily_loss.get(tsp.date(), 0.0) <= -lf.DAILY_MAX_LOSS_PCT * balance)
            if ok:
                eff = o * (1 + slip) if side == "LONG" else o * (1 - slip)  # L1 market
                qty = leg_qty(eff)
                fee_in = eff * qty * taker
                balance -= fee_in
                position = {"side": side, "open_ts": tsp, "open_bar": i,
                            "qty": qty, "avg": eff, "worst": eff, "legs": 1,
                            "entry_trend": trend, "fees_in": fee_in, "mae_pct": 0.0,
                            "entries": [(eff, qty)]}

        if position is not None:
            side = position["side"]
            l2_filled_this_bar = False
            if position["legs"] == 1:                      # L2 = resting limit
                trig = (position["worst"] * (1 - lf.DCA_SPACING) if side == "LONG"
                        else position["worst"] * (1 + lf.DCA_SPACING))
                if (side == "LONG" and l <= trig) or (side == "SHORT" and h >= trig):
                    qty2 = leg_qty(trig)
                    fee_in = trig * qty2 * maker
                    balance -= fee_in
                    position["fees_in"] += fee_in
                    position["entries"].append((trig, qty2))
                    notional = sum(p * q for p, q in position["entries"])
                    position["qty"] += qty2
                    position["avg"] = notional / position["qty"]
                    position["worst"] = trig
                    position["legs"] = 2
                    l2_filled_this_bar = True

            avg = position["avg"]
            exited = False
            if not l2_filled_this_bar:                     # same-bar deferral kept
                if position["legs"] == 1:
                    tp_px = avg * (1 + lf.TP_SINGLE) if side == "LONG" else avg * (1 - lf.TP_SINGLE)
                    sl_px = (position["worst"] * (1 - lf.SL_FROM_WORST) if side == "LONG"
                             else position["worst"] * (1 + lf.SL_FROM_WORST))
                    if side == "LONG":
                        tp_hit, sl_hit = h >= tp_px, l <= sl_px
                        sl_fill = min(sl_px, o)            # honest resting-stop fill
                    else:
                        tp_hit, sl_hit = l <= tp_px, h >= sl_px
                        sl_fill = max(sl_px, o)
                    if sl_hit:                              # pessimistic on conflict
                        close_pos(position, ts, sl_fill, "SL", is_market=True)
                        position = None; exited = True
                    elif tp_hit:
                        close_pos(position, ts, tp_px, "TP", is_market=False)
                        position = None; exited = True
                else:
                    # USER PROPOSAL: resting limit at avg, fills on touch — real
                    be_hit = (side == "LONG" and h >= avg) or (side == "SHORT" and l <= avg)
                    if be_hit:
                        close_pos(position, ts, avg, "BE_LIMIT", is_market=False)
                        position = None; exited = True

            if not exited and position is not None and not np.isnan(trend):
                if trend != position["entry_trend"]:
                    unreal = ((c - avg) * position["qty"] if side == "LONG"
                              else (avg - c) * position["qty"])
                    if unreal > 0:
                        close_pos(position, ts, c, "TREND_FLIP", is_market=True)
                        position = None; exited = True

            if not exited and position is not None:
                if (i - position["open_bar"]) >= time_sl_bars:
                    unreal = ((c - avg) * position["qty"] if side == "LONG"
                              else (avg - c) * position["qty"])
                    if unreal < 0:
                        close_pos(position, ts, c, "TIME_SL", is_market=True)
                        position = None; exited = True

            if not exited and position is not None:
                adv = l if side == "LONG" else h
                adv_pct = ((adv - avg) / avg * 100) * (1 if side == "LONG" else -1)
                position["mae_pct"] = min(position["mae_pct"], adv_pct)

        if position is None and pending is None:
            rsi, atrp = rsi_a[i], atr_a[i]
            if np.isnan(rsi) or np.isnan(atrp) or atrp > lf.ATR_MAX:
                continue
            if rsi <= lf.RSI_LONG:
                pending = "LONG"
            elif rsi >= lf.RSI_SHORT:
                pending = "SHORT"

    return {"balance": balance, "trades": trades, "exits": exits,
            "max_dd_closed": max_dd * 100, "max_dd_mtm": float("nan"),
            "min_eq_ratio": float("nan"), "liq_events": 0, "ruin_dates": []}


df = pd.read_csv(lf.CSV_PATH, parse_dates=["timestamp"])
bt = lf.prep(df)
print(f"Data: {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]}  ({len(bt):,} bars)")
for cname, bars in CONFIGS.items():
    for fname, fm in FEE_MODES.items():
        r = run(bt, bars, fm["maker"], fm["taker"], fm["slip"])
        lf.report(f"BE-LIMIT proposal {cname}", fname, r)
