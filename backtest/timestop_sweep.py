"""timestop_sweep.py — does a tighter TIME stop fix the R:R? (user 2026-06-12)

User idea: winners are quick, losers linger — so (a) measure hold-time of
profitable vs losing trades in the honest backtest, then (b) close every
position older than T (hard) or close-if-losing after T (smart, the live
mechanism — currently 6h v2.1 / 12h v2.2).

Engine = mtm_guard_ab.py verbatim (live-faithful honest fills, real fees
available, MTM basket stop 4% ON in all variants — that is the deployed
config), plus a parametric time stop:
  time_bars: bars (5m) after entry
  time_smart: True = close only if losing at live price (live semantics)
              False = HARD: close unconditionally at market
"""
import numpy as np
import pandas as pd
from datetime import timedelta
from mtm_guard_ab import (prep, CSV_PATH, INITIAL_BAL, LEVERAGE, RSI_LONG,
                          RSI_SHORT, GAP_MIN, ATR_MAX, DCA_SPACING, TP_SINGLE,
                          SL_FROM_WORST, BE_WAIT_BARS, TRAIL_ARM_PCT,
                          TRAIL_BUF_PCT, COOLDOWN_MIN, DAILY_MAX_LOSS_PCT)

MTM_CAP = 0.04   # deployed 2026-06-12 — part of the live baseline now

CONFIGS = {
    "v2.1": {"tp_dca": 0.0025, "time_sl_bars": 72},
    "v2.2": {"tp_dca": 0.0100, "time_sl_bars": 144},
}
FEE_MODES = {
    "PAPER": {"fee": 0.0, "slip": 0.0, "compound": True},
    "REAL":  {"fee": 0.00055, "slip": 0.0002, "compound": False},
}


def run(bt, tp_dca, fee, slip, compound, time_bars, time_smart):
    balance = INITIAL_BAL
    position = None
    pending = None
    pause_until = None
    daily_loss = {}
    trades = []
    exits = {"TP": 0, "BE-DCA": 0, "L2_TRAIL": 0, "SL": 0,
             "TREND_FLIP": 0, "TIME_SL": 0, "MTM_STOP": 0}

    ts_arr = bt["timestamp"].values
    o_a, h_a, l_a, c_a = (bt["open"].values, bt["high"].values,
                          bt["low"].values, bt["close"].values)
    rsi_a, atr_a = bt["rsi"].values, bt["atr_pct"].values
    tr_a, gap_a = bt["trend"].values, bt["gap"].values
    n = len(bt)

    def sizing_bal():
        return balance if compound else INITIAL_BAL

    def leg_qty(px):
        return (sizing_bal() * 0.95 * LEVERAGE) / px / 2.0

    def close_pos(pos, ts, exit_px, reason, apply_slip):
        nonlocal balance, pause_until
        side = pos["side"]
        eff = exit_px
        if apply_slip:
            eff = exit_px * (1 - slip) if side == "LONG" else exit_px * (1 + slip)
        qty = pos["qty"]; avg = pos["avg"]
        gross = (eff - avg) * qty if side == "LONG" else (avg - eff) * qty
        fee_exit = eff * qty * fee
        net_live = gross - fee_exit
        net_full = net_live - pos["fees_in"]
        balance += gross - fee_exit
        d = pd.Timestamp(ts).date()
        if net_live < 0:
            daily_loss[d] = daily_loss.get(d, 0.0) + net_live
        if net_live < 0 and reason != "BE-DCA":
            pause_until = pd.Timestamp(ts) + timedelta(minutes=COOLDOWN_MIN)
        hold_h = (pd.Timestamp(ts) - pos["open_ts"]).total_seconds() / 3600.0
        trades.append({"net": net_full, "net_live": net_live, "reason": reason,
                       "hold_h": hold_h, "exit_ts": pd.Timestamp(ts)})
        exits[reason] += 1

    for i in range(n):
        ts = ts_arr[i]
        o, h, l, c = o_a[i], h_a[i], l_a[i], c_a[i]
        trend, gap = tr_a[i], gap_a[i]

        if position is None and pending is not None:
            side = pending
            pending = None
            ok = True
            tsp = pd.Timestamp(ts)
            if np.isnan(trend) or np.isnan(gap):        ok = False
            elif gap < GAP_MIN:                          ok = False
            elif pause_until is not None and tsp < pause_until: ok = False
            elif daily_loss.get(tsp.date(), 0.0) <= -DAILY_MAX_LOSS_PCT * balance: ok = False
            if ok:
                eff = o * (1 + slip) if side == "LONG" else o * (1 - slip)
                qty = leg_qty(eff)
                fee_in = eff * qty * fee
                balance -= fee_in
                position = {"side": side, "open_ts": tsp, "open_bar": i,
                            "entries": [(eff, qty)], "qty": qty, "avg": eff,
                            "worst": eff, "legs": 1, "l2_bar": None,
                            "entry_trend": trend, "l2_peak_fav": 0.0,
                            "fees_in": fee_in}

        if position is not None:
            side = position["side"]
            l2_filled_this_bar = False

            if position["legs"] == 1:
                trig = (position["worst"] * (1 - DCA_SPACING) if side == "LONG"
                        else position["worst"] * (1 + DCA_SPACING))
                hit = (side == "LONG" and l <= trig) or (side == "SHORT" and h >= trig)
                if hit:
                    eff = trig * (1 + slip) if side == "LONG" else trig * (1 - slip)
                    qty2 = leg_qty(eff)
                    fee_in = eff * qty2 * fee
                    balance -= fee_in
                    position["fees_in"] += fee_in
                    position["entries"].append((eff, qty2))
                    notional = sum(p * q for p, q in position["entries"])
                    position["qty"] += qty2
                    position["avg"] = notional / position["qty"]
                    position["worst"] = eff if side == "LONG" else max(position["worst"], eff)
                    position["legs"] = 2
                    position["l2_bar"] = i
                    l2_filled_this_bar = True

            avg = position["avg"]

            exited = False
            if not l2_filled_this_bar:
                if position["legs"] == 1:
                    tp_px = avg * (1 + TP_SINGLE) if side == "LONG" else avg * (1 - TP_SINGLE)
                    sl_px = (position["worst"] * (1 - SL_FROM_WORST) if side == "LONG"
                             else position["worst"] * (1 + SL_FROM_WORST))
                    sl_reason = "SL"
                else:
                    tp_px = avg * (1 + tp_dca) if side == "LONG" else avg * (1 - tp_dca)
                    be_armed = (i - position["l2_bar"]) >= BE_WAIT_BARS
                    if be_armed:
                        peak = position["l2_peak_fav"]
                        if peak >= TRAIL_ARM_PCT:
                            tpct = (peak - TRAIL_BUF_PCT) / 100.0
                            sl_px = avg * (1 + tpct) if side == "LONG" else avg * (1 - tpct)
                            sl_reason = "L2_TRAIL"
                        else:
                            sl_px = avg
                            sl_reason = "BE-DCA"
                    else:
                        sl_px = None
                        sl_reason = None

                cap_d = MTM_CAP * sizing_bal()
                mtm_px = (avg - cap_d / position["qty"] if side == "LONG"
                          else avg + cap_d / position["qty"])
                if sl_px is None:
                    sl_px, sl_reason = mtm_px, "MTM_STOP"
                elif side == "LONG" and mtm_px > sl_px:
                    sl_px, sl_reason = mtm_px, "MTM_STOP"
                elif side == "SHORT" and mtm_px < sl_px:
                    sl_px, sl_reason = mtm_px, "MTM_STOP"

                if side == "LONG":
                    tp_hit = h >= tp_px
                    sl_hit = l <= sl_px
                    sl_fill = min(sl_px, o)
                else:
                    tp_hit = l <= tp_px
                    sl_hit = h >= sl_px
                    sl_fill = max(sl_px, o)
                if sl_hit:
                    close_pos(position, ts, sl_fill, sl_reason, apply_slip=True)
                    position = None; exited = True
                elif tp_hit:
                    close_pos(position, ts, tp_px, "TP", apply_slip=True)
                    position = None; exited = True

            if not exited and position is not None and not np.isnan(trend):
                if trend != position["entry_trend"]:
                    unreal = ((c - avg) * position["qty"] if side == "LONG"
                              else (avg - c) * position["qty"])
                    if unreal > 0:
                        close_pos(position, ts, c, "TREND_FLIP", apply_slip=True)
                        position = None; exited = True

            # parametric TIME stop (replaces the fixed smart time-SL)
            if not exited and position is not None and time_bars > 0:
                if (i - position["open_bar"]) >= time_bars:
                    eff = c * (1 - slip) if side == "LONG" else c * (1 + slip)
                    gross = ((eff - avg) * position["qty"] if side == "LONG"
                             else (avg - eff) * position["qty"])
                    losing = gross - eff * position["qty"] * fee < 0
                    if (not time_smart) or losing:
                        close_pos(position, ts, c, "TIME_SL", apply_slip=True)
                        position = None; exited = True

            if not exited and position is not None and position["legs"] == 2:
                fav_close = ((c - avg) / avg * 100) * (1 if side == "LONG" else -1)
                position["l2_peak_fav"] = max(position["l2_peak_fav"], fav_close)

        if position is None and pending is None:
            rsi, atrp = rsi_a[i], atr_a[i]
            if np.isnan(rsi) or np.isnan(atrp): continue
            if atrp > ATR_MAX: continue
            if rsi <= RSI_LONG:
                pending = "LONG"
            elif rsi >= RSI_SHORT:
                pending = "SHORT"

    return {"balance": balance, "trades": trades, "exits": exits}


def hold_profile(trades, label):
    w = [t["hold_h"] for t in trades if t["net"] > 0]
    lo = [t["hold_h"] for t in trades if t["net"] < 0]
    def pct(a, q):
        return float(np.percentile(a, q)) if a else float("nan")
    print(f"  {label}: WINNERS n={len(w)} median {pct(w,50):.2f}h p75 {pct(w,75):.2f}h "
          f"p90 {pct(w,90):.2f}h | LOSERS n={len(lo)} median {pct(lo,50):.2f}h "
          f"p75 {pct(lo,75):.2f}h p90 {pct(lo,90):.2f}h")
    # share of winner profit earned within 1h/2h/4h
    for hh in (1, 2, 4):
        pw = sum(t["net"] for t in trades if t["net"] > 0 and t["hold_h"] <= hh)
        tw = sum(t["net"] for t in trades if t["net"] > 0)
        pl = sum(t["net"] for t in trades if t["net"] < 0 and t["hold_h"] > hh)
        tl = sum(t["net"] for t in trades if t["net"] < 0)
        print(f"    <= {hh}h: {pw/tw*100 if tw else 0:5.1f}% of win-$ earned | "
              f"{pl/tl*100 if tl else 0:5.1f}% of loss-$ comes from trades held LONGER")


def row(name, r):
    tr = r["trades"]
    total = len(tr)
    wins = [t["net"] for t in tr if t["net"] > 0]
    losses = [t["net"] for t in tr if t["net"] < 0]
    profit = r["balance"] - INITIAL_BAL
    pf = (sum(wins) / abs(sum(losses))) if losses else float("inf")
    avg_w = sum(wins) / len(wins) if wins else 0.0
    avg_l = sum(losses) / len(losses) if losses else 0.0
    worst = min((t["net"] for t in tr), default=0.0)
    print(f"{name:<18}{total:>7}{len(wins)/total*100 if total else 0:>6.0f}%"
          f"{profit:>+11,.0f}{pf:>6.2f}{avg_w:>+8.1f}{avg_l:>+8.1f}"
          f"{abs(avg_w/avg_l) if avg_l else 0:>6.2f}{worst:>+9.0f}"
          f"  TIME={r['exits']['TIME_SL']}")


def main():
    bt = prep(pd.read_csv(CSV_PATH, parse_dates=["timestamp"]))
    print(f"Data: {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]}  ({len(bt):,} bars)")
    SWEEP = [("baseline smart", None, True),     # per-config live bars
             ("smart 1h", 12, True), ("smart 2h", 24, True),
             ("smart 4h", 48, True),
             ("HARD 1h", 12, False), ("HARD 2h", 24, False),
             ("HARD 4h", 48, False), ("HARD 12h", 144, False)]
    for cfg_name, cfg in CONFIGS.items():
        for fm_name, fm in FEE_MODES.items():
            print(f"\n══ {cfg_name}  [{fm_name}{' compounded' if fm['compound'] else ' fixed-$5K'}] ══")
            print(f"{'variant':<18}{'trades':>7}{'win%':>7}{'net $':>11}{'PF':>6}"
                  f"{'avgW$':>8}{'avgL$':>8}{'R:R':>6}{'worst$':>9}")
            for vname, bars, smart in SWEEP:
                b = cfg["time_sl_bars"] if bars is None else bars
                r = run(bt, cfg["tp_dca"], fm["fee"], fm["slip"], fm["compound"],
                        time_bars=b, time_smart=smart)
                row(vname, r)
                if vname == "baseline smart":
                    hold_profile(r["trades"], "hold-time profile")


if __name__ == "__main__":
    main()
