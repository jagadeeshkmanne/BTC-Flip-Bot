"""live_faithful.py — backtest that mirrors the DEPLOYED bots exactly.

Replicates bot_rsiscalp_v3.py tick logic with the env configs from
scripts/run_v2.1.sh and scripts/run_v2.2.sh:

  Common (both bots):
    5x leverage, weekend mult 1.0 (disabled), RSI9 35/65 counter-trend,
    15m EMA20/50 gap >= 0.20% (defensive: no trend data -> no entry),
    ATR = SMA-14 of TR on 5m (NOT Wilder) <= 0.80% of price,
    DCA L2 at 0.50% adverse from worst, TP 0.50% (1 leg),
    SL 0.6% from worst (1-leg only),
    BE-after-DCA with 6-bar wait, then L2 trail (arm +0.05%, buffer 0.025%),
    trend-flip exit PROFIT-ONLY, SMART time-SL (fires only if losing),
    15-min cooldown after losses EXCEPT BE-DCA exits,
    daily max loss 4% of balance (loss-only accumulator, live semantics),
    compounding (qty sized from current balance).
  v2.1: TP_DCA 0.25%, time-SL 72 bars (6h)
  v2.2: TP_DCA 1.00%, time-SL 144 bars (12h)

Fee modes:
  PAPER: 0 fee, 0 slip  -> predicts what the live paper dashboards show
  REAL:  0.055%/side taker + 0.02% slip per fill -> honest economics

Extras the old backtests never had:
  - mark-to-market equity drawdown (intra-trade, at bar adverse extremes)
  - liquidation check at 5x (equity_mtm <= 0.5% maintenance of notional)
  - same-bar L2-fill exit deferral + pessimistic TP/SL conflict (kept)
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
ATR_MAX = 0.008          # 0.80% (RSISCALP_ATR_MAX_PCT=0.80)
DCA_SPACING = 0.005
TP_SINGLE = 0.005
SL_FROM_WORST = 0.006    # 1-leg only
BE_WAIT_BARS = 6
TRAIL_ARM_PCT = 0.05     # percent units (live code: peak_fav >= 0.05)
TRAIL_BUF_PCT = 0.025
COOLDOWN_MIN = 15
DAILY_MAX_LOSS_PCT = 0.04
MAINT_MARGIN = 0.005     # Bybit BTCUSDT maintenance ~0.5% of notional

CONFIGS = {
    "v2.1": {"tp_dca": 0.0025, "time_sl_bars": 72},
    "v2.2": {"tp_dca": 0.0100, "time_sl_bars": 144},
}
FEE_MODES = {
    "PAPER": {"fee": 0.0, "slip": 0.0},
    "REAL":  {"fee": 0.00055, "slip": 0.0002},
}

# Run matrix: (label, fee_mode, compound, tp_needs_close_through, stop_fill)
# stop_fill:
#   "legacy" = stop exits fill at the exact stop price even when the market is
#              already beyond it (what the live paper bot BOOKS, and what the
#              old backtests assumed — BE-DCA "breakeven at avg" fiction)
#   "open"   = stop exits fill at the worse of stop price and bar open
#              (first poll tick — what a real market exit would get)
RUNS = [
    ("PAPER legacy-fill compounded", "PAPER", True,  False, "legacy"),
    ("PAPER real-fill compounded",   "PAPER", True,  False, "open"),
    ("REAL  real-fill fixed-$5K",    "REAL",  False, False, "open"),
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
    # live bot: ATR = rolling(14).MEAN of TR (simple MA, bot_rsiscalp_v3.py:398)
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


def run(bt, tp_dca, time_sl_bars, fee, slip, compound=True, tp_close_through=False,
        stop_fill="open"):
    balance = INITIAL_BAL
    peak_closed = INITIAL_BAL; max_dd_closed = 0.0
    peak_mtm = INITIAL_BAL; max_dd_mtm = 0.0
    min_equity_ratio = 1.0      # equity_mtm / balance, worst intra-trade
    liq_events = 0
    ruin_dates = []             # bars where intra-trade equity <= maintenance (account dead IRL)
    position = None
    pending = None              # side queued from prior bar's signal
    pause_until = None
    daily_loss = {}             # date -> sum of negative net_live only
    trades = []
    exits = {"TP": 0, "BE-DCA": 0, "L2_TRAIL": 0, "SL": 0,
             "TREND_FLIP": 0, "TIME_SL": 0}

    ts_arr = bt["timestamp"].values
    o_a, h_a, l_a, c_a = (bt["open"].values, bt["high"].values,
                          bt["low"].values, bt["close"].values)
    rsi_a, atr_a = bt["rsi"].values, bt["atr_pct"].values
    tr_a, gap_a = bt["trend"].values, bt["gap"].values
    n = len(bt)

    def leg_qty(bal, px):
        sizing_bal = bal if compound else INITIAL_BAL
        return (sizing_bal * 0.95 * LEVERAGE) / px / 2.0

    def close_pos(pos, ts, exit_px, reason, apply_slip):
        nonlocal balance, peak_closed, max_dd_closed, pause_until
        side = pos["side"]
        eff = exit_px
        if apply_slip:
            eff = exit_px * (1 - slip) if side == "LONG" else exit_px * (1 + slip)
        qty = pos["qty"]
        avg = pos["avg"]
        gross = (eff - avg) * qty if side == "LONG" else (avg - eff) * qty
        fee_exit = eff * qty * fee
        net_live = gross - fee_exit          # what live calls `net` (gates/stats)
        net_full = net_live - pos["fees_in"]  # honest per-trade $ incl entry fees
        balance += gross - fee_exit           # entry fees already deducted at fill
        if balance > peak_closed: peak_closed = balance
        ddc = (peak_closed - balance) / peak_closed
        if ddc > max_dd_closed: max_dd_closed = ddc
        d = pd.Timestamp(ts).date()
        if net_live < 0:
            daily_loss[d] = daily_loss.get(d, 0.0) + net_live
        if net_live < 0 and reason != "BE-DCA":   # live skips BE-DCA cooldown
            pause_until = pd.Timestamp(ts) + timedelta(minutes=COOLDOWN_MIN)
        hold_h = (pd.Timestamp(ts) - pos["open_ts"]).total_seconds() / 3600.0
        trades.append({"net": net_full, "net_live": net_live, "reason": reason,
                       "hold_h": hold_h, "exit_ts": pd.Timestamp(ts),
                       "legs": pos["legs"], "mae_pct": pos["mae_pct"]})
        exits[reason] += 1

    for i in range(n):
        ts = ts_arr[i]
        o, h, l, c = o_a[i], h_a[i], l_a[i], c_a[i]
        trend, gap = tr_a[i], gap_a[i]

        # ── pending entry fills at this bar's open (gates use THIS bar's
        #    15m view = what the live tick sees at entry time) ──
        if position is None and pending is not None:
            side = pending
            pending = None
            ok = True
            tsp = pd.Timestamp(ts)
            if np.isnan(trend) or np.isnan(gap):        ok = False  # defensive block
            elif gap < GAP_MIN:                          ok = False
            elif pause_until is not None and tsp < pause_until: ok = False
            elif daily_loss.get(tsp.date(), 0.0) <= -DAILY_MAX_LOSS_PCT * balance: ok = False
            if ok:
                eff = o * (1 + slip) if side == "LONG" else o * (1 - slip)
                qty = leg_qty(balance, eff)
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

            # 1) DCA first (live: maybe_dca before exit checks)
            if position["legs"] == 1:
                trig = (position["worst"] * (1 - DCA_SPACING) if side == "LONG"
                        else position["worst"] * (1 + DCA_SPACING))
                hit = (side == "LONG" and l <= trig) or (side == "SHORT" and h >= trig)
                if hit:
                    eff = trig * (1 + slip) if side == "LONG" else trig * (1 - slip)
                    qty2 = leg_qty(balance, eff)
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

            # 2) TP / SL-BE-trail (deferred on the L2 fill bar — no wick-order lookahead)
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

                if side == "LONG":
                    tp_hit = (c >= tp_px) if tp_close_through else (h >= tp_px)
                    sl_hit = sl_px is not None and l <= sl_px
                    # stop exit fill: legacy books the stop price itself;
                    # "open" fills at the first poll tick (bar open) when the
                    # market is already beyond the stop
                    if sl_px is not None:
                        sl_fill = sl_px if stop_fill == "legacy" else min(sl_px, o)
                    else:
                        sl_fill = None
                else:
                    tp_hit = (c <= tp_px) if tp_close_through else (l <= tp_px)
                    sl_hit = sl_px is not None and h >= sl_px
                    if sl_px is not None:
                        sl_fill = sl_px if stop_fill == "legacy" else max(sl_px, o)
                    else:
                        sl_fill = None
                if tp_hit and sl_hit:      # pessimistic
                    close_pos(position, ts, sl_fill, sl_reason, apply_slip=True)
                    position = None; exited = True
                elif sl_hit:
                    close_pos(position, ts, sl_fill, sl_reason, apply_slip=True)
                    position = None; exited = True
                elif tp_hit:
                    close_pos(position, ts, tp_px, "TP", apply_slip=True)
                    position = None; exited = True

            # 3) trend-flip, PROFIT-ONLY (live 2026-06-10 gate)
            if not exited and position is not None and not np.isnan(trend):
                if trend != position["entry_trend"]:
                    unreal = ((c - avg) * position["qty"] if side == "LONG"
                              else (avg - c) * position["qty"])
                    if unreal > 0:
                        close_pos(position, ts, c, "TREND_FLIP", apply_slip=True)
                        position = None; exited = True

            # 4) SMART time-SL (both deployed bots set RSISCALP_SMART_TIME_SL=1)
            if not exited and position is not None:
                if (i - position["open_bar"]) >= time_sl_bars:
                    eff = c * (1 - slip) if side == "LONG" else c * (1 + slip)
                    gross = ((eff - avg) * position["qty"] if side == "LONG"
                             else (avg - eff) * position["qty"])
                    if gross - eff * position["qty"] * fee < 0:
                        close_pos(position, ts, c, "TIME_SL", apply_slip=True)
                        position = None; exited = True

            # 5) still open: excursion tracking, trail peak, MTM DD, liquidation
            if not exited and position is not None:
                if side == "LONG":
                    fav_ext, adv_ext = h, l
                else:
                    fav_ext, adv_ext = l, h
                fav_pct = ((fav_ext - avg) / avg * 100) * (1 if side == "LONG" else -1)
                adv_pct = ((adv_ext - avg) / avg * 100) * (1 if side == "LONG" else -1)
                position["mae_pct"] = min(position["mae_pct"], adv_pct)
                if position["legs"] == 2:
                    # live bot samples price once per minute — it can NOT see wick
                    # peaks. Bar CLOSE is the honest proxy for the trail peak.
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
                if balance > 0:
                    min_equity_ratio = min(min_equity_ratio, eq_low / balance)
                notional = position["avg"] * position["qty"]
                if eq_low <= MAINT_MARGIN * notional:
                    liq_events += 1
                    ruin_dates.append(pd.Timestamp(ts))
            else:
                if balance > peak_mtm: peak_mtm = balance

        # ── signal at this bar's close (RSI + ATR are last-CLOSED-bar values
        #    at the next live tick); entry executes next bar ──
        if position is None and pending is None:
            rsi, atrp = rsi_a[i], atr_a[i]
            if np.isnan(rsi) or np.isnan(atrp): continue
            if atrp > ATR_MAX: continue
            if rsi <= RSI_LONG:
                pending = "LONG"
            elif rsi >= RSI_SHORT:
                pending = "SHORT"

    return {"balance": balance, "trades": trades, "exits": exits,
            "max_dd_closed": max_dd_closed * 100, "max_dd_mtm": max_dd_mtm * 100,
            "min_eq_ratio": min_equity_ratio, "liq_events": liq_events,
            "ruin_dates": ruin_dates}


def report(name, mode, r):
    tr = r["trades"]
    total = len(tr)
    wins = sum(1 for t in tr if t["net_live"] > 0)
    losses = sum(1 for t in tr if t["net_live"] < 0)
    neutrals = total - wins - losses
    profit = r["balance"] - INITIAL_BAL
    gw = sum(t["net"] for t in tr if t["net"] > 0)
    gl = sum(t["net"] for t in tr if t["net"] < 0)
    pf = abs(gw / gl) if gl < 0 else float("inf")
    worst_mae = min((t["mae_pct"] for t in tr), default=0)
    avg_trade = sum(t["net"] for t in tr) / total if total else 0
    print(f"\n  ── {name} [{mode}] ──")
    print(f"  Final: ${r['balance']:,.0f}  (profit ${profit:+,.0f}, {profit/INITIAL_BAL*100:+.1f}%)")
    print(f"  Trades: {total}  W/L/N: {wins}/{losses}/{neutrals}  "
          f"WR(of total): {wins/total*100 if total else 0:.1f}%  PF: {pf:.2f}  "
          f"avg/trade: ${avg_trade:+.2f}")
    print(f"  DD closed-balance: {r['max_dd_closed']:.2f}%   "
          f"DD mark-to-market: {r['max_dd_mtm']:.2f}%")
    print(f"  Worst intra-trade equity: {r['min_eq_ratio']*100:.1f}% of balance   "
          f"liq-events(bars): {r['liq_events']}   worst MAE: {worst_mae:.2f}%")
    if r["ruin_dates"]:
        uniq_days = sorted({d.date() for d in r["ruin_dates"]})
        print(f"  *** ACCOUNT WOULD BE LIQUIDATED: first {uniq_days[0]} "
              f"({len(uniq_days)} distinct day(s): {', '.join(str(d) for d in uniq_days[:6])}) ***")
    print(f"  Exits: " + ", ".join(f"{k}={v}" for k, v in r["exits"].items() if v))
    # year-wise
    by_year = {}
    for t in tr:
        y = t["exit_ts"].year
        by_year.setdefault(y, []).append(t)
    print(f"  {'Year':<6}{'Trades':>7}{'Win%':>7}{'Net $':>12}")
    for y in sorted(by_year):
        ys = by_year[y]
        w = sum(1 for t in ys if t["net_live"] > 0)
        print(f"  {y:<6}{len(ys):>7}{w/len(ys)*100:>6.1f}%{sum(t['net'] for t in ys):>12,.0f}")
    return {"profit": profit, "pf": pf, "trades": total}


def main():
    df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    bt = prep(df)
    print(f"Data: {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]}  ({len(bt):,} bars)")
    for name, cfg in CONFIGS.items():
        for label, fee_mode, compound, tp_ct, sfill in RUNS:
            fm = FEE_MODES[fee_mode]
            r = run(bt, cfg["tp_dca"], cfg["time_sl_bars"], fm["fee"], fm["slip"],
                    compound=compound, tp_close_through=tp_ct, stop_fill=sfill)
            report(name, label, r)


if __name__ == "__main__":
    main()
