"""Was the bot 'spoiled by improvements'? — honest test of the PRE-2026-06-06
exit scheme (before BE-DCA / trail / trend-flip / time-SL were added):

  exits: adaptive TP (0.50% 1-leg, 0.25% or 1.00% 2-leg) as resting limit,
         plain SL 0.6% from worst entry on ALL legs as resting stop.

This stop is placeable at the exchange from the moment of entry (always on the
correct side of price), so honest fill = worse(stop, bar open) is what a real
resting stop-market delivers. Entries/gates identical to deployed v2.x.
"""
import pandas as pd
import numpy as np
from datetime import timedelta
import live_faithful as lf

FEE_MODES = {
    "PAPER (0 fees)": {"maker": 0.0, "taker": 0.0, "slip": 0.0},
    "REAL maker/taker": {"maker": 0.0002, "taker": 0.00055, "slip": 0.0002},
}
CONFIGS = {"TP_DCA 0.25% (orig/v2.1)": 0.0025, "TP_DCA 1.00% (v2.2)": 0.0100}


def run(bt, tp_dca, maker, taker, slip):
    balance = lf.INITIAL_BAL
    peak = balance; max_dd = 0.0
    position = None; pending = None; pause_until = None
    daily_loss = {}; trades = []
    exits = {"TP": 0, "SL": 0}

    ts_arr = bt["timestamp"].values
    o_a, h_a, l_a = bt["open"].values, bt["high"].values, bt["low"].values
    rsi_a, atr_a = bt["rsi"].values, bt["atr_pct"].values
    tr_a, gap_a = bt["trend"].values, bt["gap"].values

    def leg_qty(px):
        return (lf.INITIAL_BAL * 0.95 * lf.LEVERAGE) / px / 2.0  # fixed-$5K

    def close_pos(pos, ts, exit_px, reason, is_market):
        nonlocal balance, peak, max_dd, pause_until
        side = pos["side"]
        eff = exit_px * (1 - slip) if (is_market and side == "LONG") else \
              exit_px * (1 + slip) if is_market else exit_px
        fee = taker if is_market else maker
        gross = (eff - pos["avg"]) * pos["qty"] if side == "LONG" else (pos["avg"] - eff) * pos["qty"]
        net_live = gross - eff * pos["qty"] * fee
        balance += net_live
        peak = max(peak, balance)
        max_dd = max(max_dd, (peak - balance) / peak)
        d = pd.Timestamp(ts).date()
        if net_live < 0:
            daily_loss[d] = daily_loss.get(d, 0.0) + net_live
            pause_until = pd.Timestamp(ts) + timedelta(minutes=lf.COOLDOWN_MIN)
        trades.append({"net": net_live - pos["fees_in"], "net_live": net_live,
                       "reason": reason, "hold_h": 0.0, "exit_ts": pd.Timestamp(ts),
                       "legs": pos["legs"], "mae_pct": pos["mae_pct"]})
        exits[reason] += 1

    for i in range(len(bt)):
        ts = ts_arr[i]
        o, h, l = o_a[i], h_a[i], l_a[i]
        trend, gap = tr_a[i], gap_a[i]

        if position is None and pending is not None:
            side = pending; pending = None
            tsp = pd.Timestamp(ts)
            ok = not (np.isnan(trend) or np.isnan(gap) or gap < lf.GAP_MIN
                      or (pause_until is not None and tsp < pause_until)
                      or daily_loss.get(tsp.date(), 0.0) <= -lf.DAILY_MAX_LOSS_PCT * balance)
            if ok:
                eff = o * (1 + slip) if side == "LONG" else o * (1 - slip)
                qty = leg_qty(eff)
                fee_in = eff * qty * taker
                balance -= fee_in
                position = {"side": side, "qty": qty, "avg": eff, "worst": eff,
                            "legs": 1, "fees_in": fee_in, "mae_pct": 0.0,
                            "entries": [(eff, qty)]}

        if position is not None:
            side = position["side"]
            l2_filled = False
            if position["legs"] == 1:
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
                    l2_filled = True

            if not l2_filled:
                avg = position["avg"]
                tp_pct = lf.TP_SINGLE if position["legs"] == 1 else tp_dca
                tp_px = avg * (1 + tp_pct) if side == "LONG" else avg * (1 - tp_pct)
                sl_px = (position["worst"] * (1 - lf.SL_FROM_WORST) if side == "LONG"
                         else position["worst"] * (1 + lf.SL_FROM_WORST))
                if side == "LONG":
                    tp_hit, sl_hit = h >= tp_px, l <= sl_px
                    sl_fill = min(sl_px, o)           # honest resting-stop fill
                else:
                    tp_hit, sl_hit = l <= tp_px, h >= sl_px
                    sl_fill = max(sl_px, o)
                if sl_hit:                            # pessimistic on conflict
                    close_pos(position, ts, sl_fill, "SL", is_market=True)
                    position = None
                elif tp_hit:
                    close_pos(position, ts, tp_px, "TP", is_market=False)
                    position = None

            if position is not None:
                adv = l if side == "LONG" else h
                adv_pct = ((adv - position["avg"]) / position["avg"] * 100) * (1 if side == "LONG" else -1)
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
for cname, tp in CONFIGS.items():
    for fname, fm in FEE_MODES.items():
        r = run(bt, tp, fm["maker"], fm["taker"], fm["slip"])
        lf.report(f"PRE-BE exits, {cname}", fname, r)
