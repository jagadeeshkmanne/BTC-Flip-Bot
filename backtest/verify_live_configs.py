"""
verify_live_configs.py — Independent re-verification of the deployed v2.1 / v2.2 configs.

Faithfully mirrors bot/bot_rsiscalp_v3.py as launched by scripts/run_v2.1.sh and
run_v2.2.sh (2026-06-11 deployed state), on the 6.8y extended dataset.

Parity choices (matched to the LIVE BOT, not the older sweep engines):
  - RSI9 Wilder on 5m closes; signal on last CLOSED bar, entry next bar OPEN
  - ATR(14) = simple rolling mean of true range (bot formula), filter ≤ 0.80% of price
  - 15m EMA20/50 resampled from 5m; trend visible once the 15m bar has CLOSED
    by the 5m decision time (bar close), trend strictly binary UP/DOWN
  - GAP filter |ema20-ema50|/ema50 ≥ 0.20%
  - Counter-trend: RSI 35/65 fires both directions; entry_trend snapshotted
  - DCA L2 at 0.50% adverse from L1, equal size, fill at trigger,
    NO same-bar exit after L2 fill (anti-lookahead)
  - TP from avg: 0.50% (L1-only), TP_DCA after L2 (0.25% v2.1 / 1.00% v2.2)
  - L1 hard SL from worst entry (dead code in practice — DCA at 0.5% precedes);
    run at core's actual 1.0% AND the documented 0.6% to show equivalence
  - BE-DCA: SL=avg armed 6 bars after L2 fill; L2 trail: close-based peak fav%
    tracked from L2 fill (incl. during wait), trail active only once armed,
    arm at peak ≥ 0.05%, SL = avg ± (peak - 0.025%)
  - Pessimistic intra-bar conflict: SL before TP
  - TREND_FLIP exit: profit-only, vs entry trend, at bar close
  - SMART time-SL: 72 bars (v2.1) / 144 (v2.2), only if losing, at bar close
  - 15-min cooldown after real losses only (BE-DCA $0 exits skip)
  - Daily loss cap 4% of current balance, pauses entries until next UTC day
  - LINEAR sizing: per-leg notional = $5,000 × 0.95 × 5 / 2 = $11,875 fixed
  - Paper parity: fees 0, slippage 0 (primary) + a fees-on secondary run
"""
import sys
import numpy as np
import pandas as pd
from datetime import timedelta

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
INITIAL_BAL = 5000.0
PER_LEG_NOTIONAL = 5000.0 * 0.95 * 5.0 / 2.0   # 11,875 — linear, fixed base
DCA_TRIGGER = 0.005
TP_L1 = 0.005
RSI_LONG, RSI_SHORT = 35, 65
GAP_MIN = 0.0020
ATR_MAX = 0.008          # 0.80% of price
BE_WAIT_BARS = 6
TRAIL_ARM_PCT = 0.05     # % above avg
TRAIL_BUF_PCT = 0.025    # % buffer behind peak
COOLDOWN_MIN = 15
DAILY_CAP_PCT = 0.04


def wilder_rsi(close, length=9):
    d = close.diff()
    g = d.clip(lower=0.0)
    l = (-d).clip(upper=None, lower=0.0)
    ag = g.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    al = l.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def prep(df):
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["rsi"] = wilder_rsi(df["close"], 9)
    prev_c = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"],
                    (df["high"] - prev_c).abs(),
                    (df["low"] - prev_c).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.rolling(14).mean() / df["close"]          # bot formula

    dfx = df.set_index("timestamp")
    h15 = dfx["close"].resample("15min", label="left", closed="left").last().dropna().to_frame()
    h15["ema20"] = h15["close"].ewm(span=20, adjust=False).mean()
    h15["ema50"] = h15["close"].ewm(span=50, adjust=False).mean()
    h15["trend"] = np.where(h15["ema20"] > h15["ema50"], 1, -1)  # bot: strictly binary
    h15["gap"] = (h15["ema20"] - h15["ema50"]).abs() / h15["ema50"]
    h15 = h15.reset_index()
    h15["closed_at"] = h15["timestamp"] + pd.Timedelta(minutes=15)

    df["decision_ts"] = df["timestamp"] + pd.Timedelta(minutes=5)  # 5m bar CLOSE
    m = pd.merge_asof(df.sort_values("decision_ts"),
                      h15[["closed_at", "trend", "gap"]].sort_values("closed_at"),
                      left_on="decision_ts", right_on="closed_at",
                      direction="backward", allow_exact_matches=True)
    # drop EMA warmup (first 50 15m bars) so trend/gap are meaningful
    warm = h15["closed_at"].iloc[55]
    return m[m["decision_ts"] >= warm].reset_index(drop=True)


def run(bt, tp_dca, time_sl_bars, sl_from_worst, fee_rate=0.0, slippage=0.0,
        stop_fill="legacy"):
    """stop_fill:
    "legacy" — stop exits (BE-DCA / L2_TRAIL / SL) fill AT the stop price even
               when the bar already opened beyond it (what the live paper bot
               books, and what the claimed backtests assumed)
    "open"   — stop exits fill at the worse of (stop price, bar open): a real
               market exit at the first poll tick cannot do better than that
    """
    balance = INITIAL_BAL
    peak_bal, max_dd = balance, 0.0
    position = None
    pending = None
    cooldown_until = None
    daily_loss = {}
    trades = []
    exits = {"TP": 0, "BE-DCA": 0, "SL": 0, "L2_TRAIL": 0, "TREND_FLIP": 0, "TIME_SL": 0}
    monthly = {}
    be_gaps = []   # per BE-DCA exit: (avg - open)/avg for LONG (signed; >0 = fiction)

    ts_a = bt["timestamp"].values
    o_a, h_a, l_a, c_a = bt["open"].values, bt["high"].values, bt["low"].values, bt["close"].values
    rsi_a, atr_a = bt["rsi"].values, bt["atr_pct"].values
    tr_a, gap_a = bt["trend"].values, bt["gap"].values
    n = len(bt)

    def pnl(side, avg, px, qty):
        eff = px * (1 - slippage) if side == 1 else px * (1 + slippage)
        gross = (eff - avg) * qty if side == 1 else (avg - eff) * qty
        return gross - (avg + eff) * qty * fee_rate

    def close(pos, ts, px, reason):
        nonlocal balance, peak_bal, max_dd, cooldown_until
        net = pnl(pos["side"], pos["avg"], px, pos["qty"])
        balance += net
        peak_bal = max(peak_bal, balance)
        max_dd = max(max_dd, (peak_bal - balance) / peak_bal)
        t = pd.Timestamp(ts)
        d = t.date()
        if net < 0:
            daily_loss[d] = daily_loss.get(d, 0.0) + net
        mk = (t.year, t.month)
        monthly[mk] = monthly.get(mk, 0.0) + net
        if net < 0 and reason != "BE-DCA":            # bot skips cooldown on BE-DCA
            cooldown_until = t + timedelta(minutes=COOLDOWN_MIN)
        trades.append((net, reason))
        exits[reason] += 1

    for i in range(n):
        ts = ts_a[i]
        o, h, l, c = o_a[i], h_a[i], l_a[i], c_a[i]
        rsi, atrp, trend, gap = rsi_a[i], atr_a[i], tr_a[i], gap_a[i]

        if position is None and pending is not None:
            side, et = pending
            pending = None
            eff = o * (1 + slippage) if side == 1 else o * (1 - slippage)
            qty = PER_LEG_NOTIONAL / eff
            balance -= eff * qty * fee_rate
            position = {"side": side, "avg": eff, "l1": eff, "qty": qty,
                        "legs": 1, "open_i": i, "l2_i": None,
                        "entry_trend": et, "peak_fav": 0.0}

        if position is not None:
            side = position["side"]
            l2_now = False
            if position["legs"] == 1:
                trig = position["l1"] * (1 - DCA_TRIGGER) if side == 1 else position["l1"] * (1 + DCA_TRIGGER)
                if (side == 1 and l <= trig) or (side == -1 and h >= trig):
                    eff = trig * (1 + slippage) if side == 1 else trig * (1 - slippage)
                    q2 = PER_LEG_NOTIONAL / eff
                    balance -= eff * q2 * fee_rate
                    position["avg"] = (position["avg"] * position["qty"] + eff * q2) / (position["qty"] + q2)
                    position["qty"] += q2
                    position["legs"] = 2
                    position["l2_i"] = i
                    l2_now = True

            avg = position["avg"]
            exited = False
            if not l2_now:
                if position["legs"] == 1:
                    worst = position["l1"]
                    tp_px = avg * (1 + TP_L1) if side == 1 else avg * (1 - TP_L1)
                    sl_px = worst * (1 - sl_from_worst) if side == 1 else worst * (1 + sl_from_worst)
                    sl_reason = "SL"
                else:
                    tp_px = avg * (1 + tp_dca) if side == 1 else avg * (1 - tp_dca)
                    be_armed = (i - position["l2_i"]) >= BE_WAIT_BARS
                    if be_armed:
                        pk = position["peak_fav"]
                        if pk >= TRAIL_ARM_PCT:
                            tpct = (pk - TRAIL_BUF_PCT) / 100.0
                            sl_px = avg * (1 + tpct) if side == 1 else avg * (1 - tpct)
                            sl_reason = "L2_TRAIL"
                        else:
                            sl_px = avg
                            sl_reason = "BE-DCA"
                    else:
                        sl_px = -np.inf if side == 1 else np.inf
                        sl_reason = "BE-DCA"

                tp_hit = (h >= tp_px) if side == 1 else (l <= tp_px)
                if (stop_fill == "limit_be" and sl_reason == "BE-DCA"
                        and np.isfinite(sl_px)):
                    # recovery-limit variant: a real resting order at avg only
                    # fills when the bar actually TOUCHES avg. Underwater bars
                    # (entirely beyond avg) keep holding until time-SL.
                    sl_hit = (l <= sl_px <= h)
                else:
                    sl_hit = (l <= sl_px) if side == 1 else (h >= sl_px)
                if sl_hit:                                   # pessimistic: SL first
                    # "open_be" corrects only BE-DCA fills (decomposition mode)
                    correct = (stop_fill == "open" or
                               (stop_fill == "open_be" and sl_reason == "BE-DCA"))
                    if stop_fill == "limit_be":
                        if sl_reason == "BE-DCA":
                            sl_fill = sl_px              # legit limit touch at avg
                        else:                            # trail/SL: real stop fill
                            sl_fill = min(sl_px, o) if side == 1 else max(sl_px, o)
                    elif correct:                        # real market exit
                        sl_fill = min(sl_px, o) if side == 1 else max(sl_px, o)
                    else:
                        sl_fill = sl_px
                    if sl_reason == "BE-DCA":
                        gap = (avg - o) / avg * side         # >0 = open adverse of avg
                        be_gaps.append(max(gap, 0.0))
                    close(position, ts, sl_fill, sl_reason)
                    position = None
                    exited = True
                elif tp_hit:
                    close(position, ts, tp_px, "TP")
                    position = None
                    exited = True

            if not exited and position is not None and not np.isnan(trend):
                if trend != position["entry_trend"]:
                    if pnl(side, avg, c, position["qty"]) > 0:   # profit-only flip
                        close(position, ts, c, "TREND_FLIP")
                        position = None
                        exited = True

            if not exited and position is not None:
                if (i - position["open_i"]) >= time_sl_bars:
                    if pnl(side, avg, c, position["qty"]) < 0:   # smart: losers only
                        close(position, ts, c, "TIME_SL")
                        position = None
                        exited = True

            if not exited and position is not None and position["legs"] == 2:
                fav = ((c - avg) / avg * 100.0) * position["side"]   # close-based peak
                position["peak_fav"] = max(position["peak_fav"], fav)

        if position is None and pending is None:
            t = pd.Timestamp(ts)
            if cooldown_until is not None and t < cooldown_until:
                continue
            if daily_loss.get(t.date(), 0.0) <= -balance * DAILY_CAP_PCT:
                continue
            if np.isnan(rsi) or np.isnan(atrp) or np.isnan(trend) or np.isnan(gap):
                continue
            if atrp > ATR_MAX or gap < GAP_MIN:
                continue
            if rsi <= RSI_LONG:
                pending = (1, int(trend))
            elif rsi >= RSI_SHORT:
                pending = (-1, int(trend))

    nets = np.array([t[0] for t in trades]) if trades else np.array([0.0])
    wins = int((nets > 0).sum())
    losses = int((nets < 0).sum())
    neutrals = int((nets == 0).sum())
    months_pos = sum(1 for v in monthly.values() if v > 0)
    gw = float(nets[nets > 0].sum())
    gl = float(nets[nets < 0].sum())
    return {
        "trades": len(trades), "wins": wins, "losses": losses, "neutrals": neutrals,
        "wr_excl_neutral": wins / (wins + losses) * 100 if wins + losses else 0,
        "wr_all": wins / len(trades) * 100 if trades else 0,
        "profit": balance - INITIAL_BAL, "max_dd": max_dd * 100,
        "worst_trade": float(nets.min()), "best_trade": float(nets.max()),
        "pf": abs(gw / gl) if gl < 0 else float("inf"),
        "avg_trade": float(nets.mean()),
        "exits": exits, "months": len(monthly), "months_pos": months_pos,
        "monthly": monthly,
        "be_gaps": be_gaps,
    }


def report(label, r, claim):
    print(f"\n════ {label} ════")
    print(f"  trades   {r['trades']:>8,}    (claim {claim['trades']:,})")
    print(f"  WR(ex-N) {r['wr_excl_neutral']:>7.1f}%    (claim {claim['wr']}%)   [all-trades WR {r['wr_all']:.1f}%]")
    print(f"  W/L/N    {r['wins']:,} / {r['losses']:,} / {r['neutrals']:,}")
    print(f"  profit   ${r['profit']:>+10,.0f}    (claim ${claim['profit']:+,})")
    print(f"  max DD   {r['max_dd']:>7.2f}%    (claim {claim['dd']}%)")
    print(f"  worst    ${r['worst_trade']:+.2f}   (claim {claim['worst']})")
    print(f"  months   {r['months_pos']}/{r['months']} profitable  (claim 82/82)")
    print(f"  exits    " + "  ".join(f"{k}={v}" for k, v in r["exits"].items()))


def main():
    print("Loading extended dataset...")
    df = pd.read_csv(CSV, parse_dates=["timestamp"])
    print(f"  {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}  ({len(df):,} bars)")
    bt = prep(df)
    print(f"  after prep: {len(bt):,} bars\n")

    modes = [
        ("(a) LEGACY fills, 0.02% slip, no fees  [control = claimed numbers]",
         dict(fee_rate=0.0, slippage=0.0002, stop_fill="legacy")),
        ("(b) CORRECTED fills, zero fees",
         dict(fee_rate=0.0, slippage=0.0, stop_fill="open")),
        ("(c) CORRECTED fills + 0.055%/side taker + 0.02% slip",
         dict(fee_rate=0.00055, slippage=0.0002, stop_fill="open")),
    ]
    results = {}
    for cfg_name, tp, tsl in [("v2.1", 0.0025, 72), ("v2.2", 0.0100, 144)]:
        for mode_name, kw in modes:
            r = run(bt, tp_dca=tp, time_sl_bars=tsl, sl_from_worst=0.010, **kw)
            results[(cfg_name, mode_name)] = r
            print(f"════ {cfg_name}  {mode_name} ════")
            print(f"  profit ${r['profit']:+,.0f} | PF {r['pf']:.2f} | avg/trade ${r['avg_trade']:+.2f} | "
                  f"DD(closed) {r['max_dd']:.2f}%")
            print(f"  trades {r['trades']:,}  W/L/N {r['wins']:,}/{r['losses']:,}/{r['neutrals']:,} | "
                  f"worst ${r['worst_trade']:+,.2f} | months green {r['months_pos']}/{r['months']}")
            print(f"  exits " + "  ".join(f"{k}={v}" for k, v in r["exits"].items() if v))
            gaps = np.array(r["be_gaps"])
            if len(gaps):
                fict = gaps[gaps > 1e-9]
                print(f"  BE-DCA diagnostic: {len(gaps):,} exits | open adverse of avg in "
                      f"{len(fict):,} ({len(fict)/len(gaps)*100:.0f}%) | mean gap {gaps.mean()*100:.3f}% "
                      f"| mean when adverse {fict.mean()*100 if len(fict) else 0:.3f}% "
                      f"| p90 {np.percentile(gaps, 90)*100:.3f}% | max {gaps.max()*100:.3f}%")
                notional = 2 * PER_LEG_NOTIONAL
                print(f"  → fictional P&L booked by legacy fills ≈ ${gaps.sum() * notional:,.0f}")
            # year-wise
            yr = {}
            for (y, m), v in r["monthly"].items():
                yr[y] = yr.get(y, 0.0) + v
            print("  years: " + "  ".join(f"{y}:{v:+,.0f}" for y, v in sorted(yr.items())))
            print()


if __name__ == "__main__":
    main()
