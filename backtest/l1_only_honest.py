"""User hypothesis 2026-06-12: 'the bot was good with just L1 entry + fixed TP.'
Single entry, NO DCA, TP 0.5% (resting limit), SL 0.6% (resting stop, honest
fill = worse of stop and bar open). Same entries/gates as deployed v2.x.
Also sweeps a few TP/SL combos to make sure the verdict isn't knife-edged.
"""
import pandas as pd
import numpy as np
from datetime import timedelta
import live_faithful as lf

FEE_MODES = {
    "PAPER (0 fees)": {"maker": 0.0, "taker": 0.0, "slip": 0.0},
    "REAL maker/taker": {"maker": 0.0002, "taker": 0.00055, "slip": 0.0002},
}
COMBOS = [(0.005, 0.006), (0.005, 0.005), (0.0075, 0.005), (0.01, 0.01), (0.003, 0.003)]


def run(bt, tp_pct, sl_pct, maker, taker, slip):
    balance = lf.INITIAL_BAL
    peak = balance; max_dd = 0.0
    position = None; pending = None; pause_until = None
    daily_loss = {}; wins = losses = 0
    gw = gl = 0.0

    o_a, h_a, l_a = bt["open"].values, bt["high"].values, bt["low"].values
    rsi_a, atr_a = bt["rsi"].values, bt["atr_pct"].values
    tr_a, gap_a = bt["trend"].values, bt["gap"].values
    ts_arr = bt["timestamp"].values

    def qty_for(px):
        return (lf.INITIAL_BAL * 0.95 * lf.LEVERAGE) / px  # fixed-$5K, full size (no DCA reserve)

    for i in range(len(bt)):
        o, h, l = o_a[i], h_a[i], l_a[i]
        if position is None and pending is not None:
            side = pending; pending = None
            tsp = pd.Timestamp(ts_arr[i])
            ok = not (np.isnan(tr_a[i]) or np.isnan(gap_a[i]) or gap_a[i] < lf.GAP_MIN
                      or (pause_until is not None and tsp < pause_until)
                      or daily_loss.get(tsp.date(), 0.0) <= -lf.DAILY_MAX_LOSS_PCT * balance)
            if ok:
                eff = o * (1 + slip) if side == "LONG" else o * (1 - slip)
                qty = qty_for(eff)
                balance -= eff * qty * taker
                position = {"side": side, "qty": qty, "avg": eff}

        if position is not None:
            side = position["side"]; avg = position["avg"]
            tp = avg * (1 + tp_pct) if side == "LONG" else avg * (1 - tp_pct)
            sl = avg * (1 - sl_pct) if side == "LONG" else avg * (1 + sl_pct)
            if side == "LONG":
                tp_hit, sl_hit = h >= tp, l <= sl
                sl_fill = min(sl, o)
            else:
                tp_hit, sl_hit = l <= tp, h >= sl
                sl_fill = max(sl, o)
            if sl_hit:                          # pessimistic on conflict
                eff = sl_fill * (1 - slip) if side == "LONG" else sl_fill * (1 + slip)
                gross = (eff - avg) * position["qty"] if side == "LONG" else (avg - eff) * position["qty"]
                net = gross - eff * position["qty"] * taker
                balance += net; losses += 1; gl += net
                d = pd.Timestamp(ts_arr[i]).date()
                daily_loss[d] = daily_loss.get(d, 0.0) + net
                pause_until = pd.Timestamp(ts_arr[i]) + timedelta(minutes=lf.COOLDOWN_MIN)
                position = None
            elif tp_hit:
                gross = (tp - avg) * position["qty"] if side == "LONG" else (avg - tp) * position["qty"]
                net = gross - tp * position["qty"] * maker
                balance += net; wins += 1; gw += net
                position = None
            peak = max(peak, balance)
            max_dd = max(max_dd, (peak - balance) / peak)

        if position is None and pending is None:
            rsi, atrp = rsi_a[i], atr_a[i]
            if np.isnan(rsi) or np.isnan(atrp) or atrp > lf.ATR_MAX:
                continue
            if rsi <= lf.RSI_LONG:
                pending = "LONG"
            elif rsi >= lf.RSI_SHORT:
                pending = "SHORT"

    total = wins + losses
    return {"final": balance, "trades": total, "wr": wins / total * 100 if total else 0,
            "pf": abs(gw / gl) if gl < 0 else float("inf"), "dd": max_dd * 100}


df = pd.read_csv(lf.CSV_PATH, parse_dates=["timestamp"])
bt = lf.prep(df)
print(f"Data: {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]}  ({len(bt):,} bars)")
for fname, fm in FEE_MODES.items():
    print(f"\n══ {fname} ══")
    print(f"  {'TP':>6} {'SL':>6} {'trades':>7} {'WR':>7} {'PF':>6} {'final $':>12} {'DD%':>7}")
    for tp, sl in COMBOS:
        r = run(bt, tp, sl, fm["maker"], fm["taker"], fm["slip"])
        print(f"  {tp*100:>5.2f}% {sl*100:>5.2f}% {r['trades']:>7} {r['wr']:>6.1f}% "
              f"{r['pf']:>6.2f} {r['final']:>12,.0f} {r['dd']:>6.1f}%")
