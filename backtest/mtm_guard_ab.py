"""mtm_guard_ab.py — A/B test of user-proposed account-equity guards (2026-06-12).

Question (user): R:R is bad — winners small, one bad basket loses a lot.
Proposal: close the basket when UNREALIZED loss hits ~$200 / 4% of balance,
and bank profit once unrealized gain hits ~2%.

Engine = live_faithful.py (git 5cad355) verbatim, honest mode only
(stop fills at worse(stop, open), same-bar L2 deferral, pessimistic TP/SL),
plus two optional overlays:

  MTM_STOP  cap%  : stop order at the price where unrealized = -cap% of
                    balance, active for BOTH legs incl. the BE-wait window
                    (which has no stop in the live bot). Fires like any stop:
                    trigger on bar extreme, fill at worse(trigger, open).
  PROFIT_LOCK p%  : market exit when unrealized at bar CLOSE >= +p% of
                    balance (live bot ticks 1/min — close is the honest
                    proxy; wick peaks are not catchable).

Fee modes: REAL (0.055%+0.02% slip, fixed $5K sizing — honest economics) and
PAPER (zero-fee, compounded — predicts the live paper dashboard).
"""
import pandas as pd
import numpy as np
from datetime import timedelta

CSV_PATH = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"

INITIAL_BAL = 5000.0
LEVERAGE = 5.0
RSI_LEN = 9
RSI_LONG, RSI_SHORT = 35, 65
GAP_MIN = 0.0020
ATR_MAX = 0.008
DCA_SPACING = 0.005
TP_SINGLE = 0.005
SL_FROM_WORST = 0.006
BE_WAIT_BARS = 6
TRAIL_ARM_PCT = 0.05
TRAIL_BUF_PCT = 0.025
COOLDOWN_MIN = 15
DAILY_MAX_LOSS_PCT = 0.04

CONFIGS = {
    "v2.1": {"tp_dca": 0.0025, "time_sl_bars": 72},
    "v2.2": {"tp_dca": 0.0100, "time_sl_bars": 144},
}
FEE_MODES = {
    "PAPER": {"fee": 0.0, "slip": 0.0, "compound": True},
    "REAL":  {"fee": 0.00055, "slip": 0.0002, "compound": False},
}
VARIANTS = [
    ("baseline",          None,  None),
    ("mtm-stop 4%",       0.04,  None),
    ("mtm-stop 2%",       0.02,  None),
    ("lock +2%",          None,  0.02),
    ("mtm4% + lock2%",    0.04,  0.02),
]


def wilder_rsi(close, length):
    d = close.diff()
    gain = d.clip(lower=0.0); loss = (-d).clip(lower=0.0)
    ag = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    al = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    return 100 - (100 / (1 + ag / al))


def prep(df):
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["rsi"] = wilder_rsi(df["close"], RSI_LEN)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.rolling(14).mean() / df["close"]
    dfix = df.set_index("timestamp")
    df15 = dfix[["open", "high", "low", "close"]].resample(
        "15min", label="left", closed="left"
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    e20 = df15["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    e50 = df15["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    df15["trend"] = np.where(e20 > e50, 1.0, -1.0)
    df15.loc[e50.isna() | e20.isna(), "trend"] = np.nan
    df15["gap"] = (e20 - e50).abs() / e50
    df15 = df15.reset_index().rename(columns={"timestamp": "ts15"})
    df15["closed_at"] = df15["ts15"] + pd.Timedelta(minutes=15)
    merged = pd.merge_asof(
        df.sort_values("timestamp"),
        df15[["closed_at", "trend", "gap"]].sort_values("closed_at"),
        left_on="timestamp", right_on="closed_at", direction="backward")
    return merged


def run(bt, tp_dca, time_sl_bars, fee, slip, compound,
        mtm_cap_pct=None, lock_pct=None):
    balance = INITIAL_BAL
    peak_mtm = INITIAL_BAL; max_dd_mtm = 0.0
    position = None
    pending = None
    pause_until = None
    daily_loss = {}
    trades = []
    exits = {"TP": 0, "BE-DCA": 0, "L2_TRAIL": 0, "SL": 0,
             "TREND_FLIP": 0, "TIME_SL": 0, "MTM_STOP": 0, "PROFIT_LOCK": 0}

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
        trades.append({"net": net_full, "net_live": net_live, "reason": reason,
                       "exit_ts": pd.Timestamp(ts), "mae_pct": pos["mae_pct"]})
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
                            "fees_in": fee_in, "mae_pct": 0.0}

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

                # ── user overlay 1: MTM basket stop at -cap% of balance.
                # Active for both legs, INCLUDING the BE-wait window. Nearer
                # of (existing stop, MTM stop) fires first, like real orders.
                if mtm_cap_pct is not None:
                    cap_d = mtm_cap_pct * sizing_bal()
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
                    sl_hit = sl_px is not None and l <= sl_px
                    sl_fill = min(sl_px, o) if sl_px is not None else None
                else:
                    tp_hit = l <= tp_px
                    sl_hit = sl_px is not None and h >= sl_px
                    sl_fill = max(sl_px, o) if sl_px is not None else None
                if tp_hit and sl_hit:
                    close_pos(position, ts, sl_fill, sl_reason, apply_slip=True)
                    position = None; exited = True
                elif sl_hit:
                    close_pos(position, ts, sl_fill, sl_reason, apply_slip=True)
                    position = None; exited = True
                elif tp_hit:
                    close_pos(position, ts, tp_px, "TP", apply_slip=True)
                    position = None; exited = True

                # ── user overlay 2: profit lock at +p% of balance (close-based,
                # live 1-min tick proxy; checked after TP/SL — a resting TP wick
                # fill beats a tick-sampled market exit) ──
                if not exited and lock_pct is not None:
                    unreal_c = ((c - avg) * position["qty"] if side == "LONG"
                                else (avg - c) * position["qty"])
                    if unreal_c >= lock_pct * sizing_bal():
                        close_pos(position, ts, c, "PROFIT_LOCK", apply_slip=True)
                        position = None; exited = True

            if not exited and position is not None and not np.isnan(trend):
                if trend != position["entry_trend"]:
                    unreal = ((c - avg) * position["qty"] if side == "LONG"
                              else (avg - c) * position["qty"])
                    if unreal > 0:
                        close_pos(position, ts, c, "TREND_FLIP", apply_slip=True)
                        position = None; exited = True

            if not exited and position is not None:
                if (i - position["open_bar"]) >= time_sl_bars:
                    eff = c * (1 - slip) if side == "LONG" else c * (1 + slip)
                    gross = ((eff - avg) * position["qty"] if side == "LONG"
                             else (avg - eff) * position["qty"])
                    if gross - eff * position["qty"] * fee < 0:
                        close_pos(position, ts, c, "TIME_SL", apply_slip=True)
                        position = None; exited = True

            if not exited and position is not None:
                if side == "LONG":
                    fav_ext, adv_ext = h, l
                else:
                    fav_ext, adv_ext = l, h
                adv_pct = ((adv_ext - avg) / avg * 100) * (1 if side == "LONG" else -1)
                position["mae_pct"] = min(position["mae_pct"], adv_pct)
                if position["legs"] == 2:
                    fav_close = ((c - avg) / avg * 100) * (1 if side == "LONG" else -1)
                    position["l2_peak_fav"] = max(position["l2_peak_fav"], fav_close)
                unreal_worst = ((adv_ext - avg) * position["qty"] if side == "LONG"
                                else (avg - adv_ext) * position["qty"])
                unreal_best = ((fav_ext - avg) * position["qty"] if side == "LONG"
                               else (avg - fav_ext) * position["qty"])
                eq_low = balance + unreal_worst
                eq_high = balance + unreal_best
                if eq_high > peak_mtm: peak_mtm = eq_high
                ddm = (peak_mtm - eq_low) / peak_mtm
                if ddm > max_dd_mtm: max_dd_mtm = ddm
            else:
                if balance > peak_mtm: peak_mtm = balance

        if position is None and pending is None:
            rsi, atrp = rsi_a[i], atr_a[i]
            if np.isnan(rsi) or np.isnan(atrp): continue
            if atrp > ATR_MAX: continue
            if rsi <= RSI_LONG:
                pending = "LONG"
            elif rsi >= RSI_SHORT:
                pending = "SHORT"

    return {"balance": balance, "trades": trades, "exits": exits,
            "max_dd_mtm": max_dd_mtm * 100}


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
    rr = abs(avg_w / avg_l) if avg_l else float("inf")
    ex = r["exits"]
    print(f"{name:<16}{total:>7}{len(wins)/total*100 if total else 0:>6.0f}%"
          f"{profit:>+11,.0f}{pf:>6.2f}{avg_w:>+8.1f}{avg_l:>+8.1f}{rr:>6.2f}"
          f"{worst:>+9.0f}{r['max_dd_mtm']:>7.1f}%"
          f"  MTM={ex['MTM_STOP']} LOCK={ex['PROFIT_LOCK']}")


def main():
    df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    bt = prep(df)
    print(f"Data: {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]}  ({len(bt):,} bars)")
    for cfg_name, cfg in CONFIGS.items():
        for fm_name, fm in FEE_MODES.items():
            print(f"\n══ {cfg_name}  [{fm_name}{' compounded' if fm['compound'] else ' fixed-$5K'}] ══")
            print(f"{'variant':<16}{'trades':>7}{'win%':>7}{'net $':>11}{'PF':>6}"
                  f"{'avgW$':>8}{'avgL$':>8}{'R:R':>6}{'worst$':>9}{'mtmDD':>8}")
            for vname, cap, lock in VARIANTS:
                r = run(bt, cfg["tp_dca"], cfg["time_sl_bars"], fm["fee"], fm["slip"],
                        fm["compound"], mtm_cap_pct=cap, lock_pct=lock)
                row(vname, r)


if __name__ == "__main__":
    main()
