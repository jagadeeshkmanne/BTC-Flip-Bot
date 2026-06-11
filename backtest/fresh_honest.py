"""fresh_honest.py — ground-up backtest of the deployed v2.1 / v2.2 bots.

Written 2026-06-11 directly from bot/bot_rsiscalp_v3.py + bot/core_rsiscalp.py
+ scripts/run_v2.{1,2}.sh (NOT from the earlier backtest scripts, which had a
fee double-count and a variable-shadowing bug).

DECISION MODEL (identical in all modes — mirrors the live 1-min tick):
  - Signal: Wilder RSI9 (core.rsi_series) on last CLOSED 5m bar, 35/65.
  - Gates at entry time (first tick of next bar): ATR14(SMA of TR) of signal
    bar <= 0.80% of close; 15m EMA20/50 gap >= 0.20% (defensive: NaN blocks);
    counter-trend mode (no direction gate, trend snapshotted); 15-min cooldown
    after losses except BE-DCA; daily loss cap 4% of balance (UTC day).
    15m view "as of bar open" is exact for every tick in the bar because 15m
    closes always land on 5m boundaries.
  - Tick order per bar (bot order): DCA -> exits. TP/stop deferred on the
    L2-fill bar (the fill tick price cannot also be at TP/BE; later intra-bar
    ticks are unknowable) — trend-flip / time-SL at close still allowed.
  - Pessimistic intra-bar conflict: stop before TP.
  - L1 catastrophic SL at core's 1.0% from worst (the 0.6% env default never
    reaches sl_price() — known bug). Dead code in practice: DCA at 0.5% always
    precedes it.
  - L2 trail: peak favorable %, close-sampled, updated AFTER exit checks while
    the position survives, including during the 6-bar BE wait. Arm >= 0.05%,
    buffer 0.025%. BE-DCA stop = avg, armed 6 bars after L2.
  - Trend-flip: profit-only, vs entry-trend snapshot, at close.
  - SMART time-SL (72 bars v2.1 / 144 v2.2): only if losing, at close.
  - Sizing: LINEAR $5,000 * 0.95 * 5 / 2 = $11,875 notional per leg (matches
    the claimed-backtest sizing in the run-script headers).

FILL MODELS:
  PARITY — book exits exactly like the live software does: stops AT the stop
    price even when the market is already beyond it, TP at the TP price, DCA
    at the trigger. Zero fees (paper). Gates driven by booked P&L.
    -> reproduces what the dashboards / claimed backtests show.
  HONEST — every trigger fill happens at the first observable price beyond
    the trigger: bar open if the bar opens beyond it, else the trigger price.
    Symmetric (helps TP/DCA on gaps, hurts stops on gaps). Gates driven by
    real P&L (a real-execution bot would see real losses).
    -> HONEST-0: zero fees, zero slip (isolates the booking artifact)
    -> HONEST-F: 0.055%/side taker + 0.02% slip on market fills (real world)
  EXCHANGE — what a real order-on-exchange implementation (3commas-style)
    would get: DCA + TP as resting limits (fill AT their price on touch,
    maker fee); BE exit as a stop-limit at avg — fills at avg ONLY when price
    comes back to touch it, otherwise the position holds until time-SL/flip
    (market, taker). Once the BE limit is live (price dipped to avg after
    arming), any recovery fills it at $0 — so TP and the L2 trail are
    unreachable for those trades by construction. Trail survives only for
    positions that were above avg when BE armed (stop-market, honest fill).
    -> EXCHANGE-0: zero fees (isolates the structure)
    -> EXCHANGE-F: 0.055% taker / 0.02% maker + 0.02% slip on market fills

KNOWN LIMITS (all flagged, none favor the strategy):
  - 5m bars vs 1-min polling: within-bar stop crossings fill at the stop
    price; the live poller's first sample beyond is worse on average (slip
    knob partially covers). Same-bar exit->re-entry is delayed to next bar.
  - Trail peak is close-sampled; 1-min sampling would arm slightly more often.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"

INITIAL_BAL = 5000.0
PER_LEG_NOTIONAL = INITIAL_BAL * 0.95 * 5.0 / 2.0   # $11,875 linear
RSI_LONG, RSI_SHORT = 35, 65
GAP_MIN = 0.0020
ATR_MAX_PCT = 0.80
DCA_SPACING = 0.005
TP_SINGLE = 0.005
SL_L1 = 0.010            # core sl_price() value actually used live
BE_WAIT_BARS = 6
TRAIL_ARM = 0.05         # percent units
TRAIL_BUF = 0.025
COOLDOWN_BARS = 3        # 15 min from exit (booked at bar close)
DAILY_CAP_PCT = 0.04

CONFIGS = {"v2.1": dict(tp_dca=0.0025, time_sl=72),
           "v2.2": dict(tp_dca=0.0100, time_sl=144)}
MODES = {
    "PARITY  (live booking, 0 fees)":  dict(fill="parity",   fee=0.0,     slip=0.0),
    "HONEST-0 (real fills, 0 fees)":   dict(fill="honest",   fee=0.0,     slip=0.0),
    "HONEST-F (real fills + fees)":    dict(fill="honest",   fee=0.00055, slip=0.0002),
    "EXCHANGE-0 (real orders, 0 fees)": dict(fill="exchange", fee=0.0,     slip=0.0),
    "EXCHANGE-F (real orders + fees)":  dict(fill="exchange", fee=0.00055, slip=0.0002,
                                             fee_maker=0.0002),
}

CLAIMS = {"v2.1": "+$726,884 / 71.9% WR / 1.27% DD",
          "v2.2": "+$884,471 / 72.1% WR / 0.64% DD"}


def prep():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp")
    df = df.reset_index(drop=True)
    # Wilder RSI9 exactly as core.rsi_series
    d = df["close"].diff()
    gain = d.clip(lower=0); loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1/9, min_periods=9, adjust=False).mean()
    al = loss.ewm(alpha=1/9, min_periods=9, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + ag / al.replace(0, np.nan))
    # ATR14 = simple rolling mean of TR (bot formula), as % of close
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.rolling(14).mean() / df["close"] * 100
    # 15m trend/gap, visible once the 15m bar has closed at-or-before bar open
    dfx = df.set_index("timestamp")
    c15 = dfx["close"].resample("15min", label="left", closed="left").last().dropna()
    e20 = c15.ewm(span=20, adjust=False, min_periods=20).mean()
    e50 = c15.ewm(span=50, adjust=False, min_periods=50).mean()
    t15 = pd.DataFrame({"closed_at": c15.index + pd.Timedelta(minutes=15),
                        "trend": np.where(e20 > e50, 1.0, -1.0),
                        "gap": (e20 - e50).abs() / e50})
    t15.loc[e20.isna() | e50.isna(), ["trend", "gap"]] = np.nan
    m = pd.merge_asof(df, t15.sort_values("closed_at"),
                      left_on="timestamp", right_on="closed_at",
                      direction="backward", allow_exact_matches=True)
    return m


def run(bt, tp_dca, time_sl, fill, fee, slip, fee_maker=None):
    honest = fill == "honest"
    exchange = fill == "exchange"
    fee_maker = fee if fee_maker is None else fee_maker
    balance = INITIAL_BAL
    peak, max_dd = balance, 0.0
    eq_peak, max_dd_mtm, min_eq = balance, 0.0, balance
    pos = None
    pending = None           # (side, entry_trend) queued from prior bar signal
    cooldown_until_i = -1
    daily_loss = {}          # UTC date -> sum of negative nets (mode's own net)
    trades = []
    exits = {k: 0 for k in ("TP", "BE-DCA", "L2_TRAIL", "SL", "TREND_FLIP", "TIME_SL")}
    fiction_usd = 0.0        # PARITY booking minus first-tick fill, stop exits
    gap_through_stops = 0

    ts = bt["timestamp"].values
    O, H, L, C = (bt[c].values for c in ("open", "high", "low", "close"))
    RSI, ATRP = bt["rsi"].values, bt["atr_pct"].values
    TR15, GAP15 = bt["trend"].values, bt["gap"].values
    n = len(bt)

    def fees_on(notional, maker=False):
        return notional * (fee_maker if maker else fee)

    def close_trade(i, exit_px, reason, market_order):
        nonlocal balance, peak, max_dd, cooldown_until_i, pos
        side, qty, avg = pos["side"], pos["qty"], pos["avg"]
        eff = exit_px * (1 - slip * side) if market_order else exit_px
        gross = (eff - avg) * qty * side
        net = gross - fees_on(eff * qty, maker=not market_order) - pos["fees_in"]
        balance += net
        peak = max(peak, balance)
        max_dd = max(max_dd, (peak - balance) / peak)
        t_exit = pd.Timestamp(ts[i]) + pd.Timedelta(minutes=5)
        if net < 0:
            dkey = t_exit.date()
            daily_loss[dkey] = daily_loss.get(dkey, 0.0) + net
        if net < 0 and reason != "BE-DCA":
            cooldown_until_i = i + 1 + COOLDOWN_BARS
        trades.append((net, reason, t_exit))
        exits[reason] += 1
        pos = None

    for i in range(n):
        o, h, l, c = O[i], H[i], L[i], C[i]
        trend, gap = TR15[i], GAP15[i]

        # ── entry at this bar's open (signal queued at prior bar close) ──
        if pos is None and pending is not None:
            side, atrp_sig = pending
            pending = None
            t_open = pd.Timestamp(ts[i])
            ok = (not np.isnan(trend) and not np.isnan(gap) and gap >= GAP_MIN
                  and i >= cooldown_until_i
                  and daily_loss.get(t_open.date(), 0.0) > -DAILY_CAP_PCT * balance)
            if ok:
                eff = o * (1 + slip * side)
                qty = PER_LEG_NOTIONAL / eff
                pos = {"side": side, "avg": eff, "worst": eff, "qty": qty,
                       "legs": 1, "open_i": i, "l2_i": -1,
                       "entry_trend": trend, "peak_fav": 0.0, "be_live": False,
                       "fees_in": fees_on(eff * qty)}

        if pos is not None:
            side = pos["side"]
            l2_this_bar = False

            # 1) DCA first (bot: maybe_dca before exit checks)
            if pos["legs"] == 1:
                trig = pos["worst"] * (1 - DCA_SPACING * side)
                if (l <= trig) if side == 1 else (h >= trig):
                    if exchange:                      # resting limit fills at its price
                        eff = trig
                    else:
                        raw = (min(trig, o) if side == 1 else max(trig, o)) if honest else trig
                        eff = raw * (1 + slip * side)
                    q2 = PER_LEG_NOTIONAL / eff
                    pos["fees_in"] += fees_on(eff * q2, maker=exchange)
                    pos["avg"] = (pos["avg"] * pos["qty"] + eff * q2) / (pos["qty"] + q2)
                    pos["qty"] += q2
                    pos["worst"] = min(pos["worst"], eff) if side == 1 else max(pos["worst"], eff)
                    pos["legs"] = 2
                    pos["l2_i"] = i
                    l2_this_bar = True

            avg = pos["avg"]

            # 2) TP / stop (deferred on the L2-fill bar)
            if not l2_this_bar and exchange:
                # real-orders model: TP is a resting limit (fills at its price,
                # maker); BE is a stop-limit at avg — once price has dipped to
                # avg after arming ("be_live"), the recovery limit rests there
                # and ANY bounce back to avg fills it, making TP/trail
                # unreachable for that trade.
                tp_px = avg * (1 + (TP_SINGLE if pos["legs"] == 1 else tp_dca) * side)
                tp_hit = (h >= tp_px) if side == 1 else (l <= tp_px)
                if pos["legs"] == 1:
                    sl_px = pos["worst"] * (1 - SL_L1 * side)
                    if (l <= sl_px) if side == 1 else (h >= sl_px):  # stop-market
                        sl_fill = min(sl_px, o) if side == 1 else max(sl_px, o)
                        close_trade(i, sl_fill, "SL", market_order=True)
                    elif tp_hit:
                        close_trade(i, tp_px, "TP", market_order=False)
                else:
                    armed = (i - pos["l2_i"]) >= BE_WAIT_BARS
                    pk = pos["peak_fav"]
                    if armed and not pos["be_live"]:
                        if (l <= avg) if side == 1 else (h >= avg):
                            pos["be_live"] = True
                    if pos["be_live"]:
                        if (h >= avg) if side == 1 else (l <= avg):  # recovery touch
                            close_trade(i, avg, "BE-DCA", market_order=False)
                    elif armed and pk >= TRAIL_ARM:
                        trail_px = avg * (1 + (pk - TRAIL_BUF) / 100.0 * side)
                        if (l <= trail_px) if side == 1 else (h >= trail_px):
                            t_fill = min(trail_px, o) if side == 1 else max(trail_px, o)
                            close_trade(i, t_fill, "L2_TRAIL", market_order=True)
                        elif tp_hit:
                            close_trade(i, tp_px, "TP", market_order=False)
                    elif tp_hit:
                        close_trade(i, tp_px, "TP", market_order=False)
            elif not l2_this_bar:
                if pos["legs"] == 1:
                    tp_px = avg * (1 + TP_SINGLE * side)
                    sl_px = pos["worst"] * (1 - SL_L1 * side)
                    sl_reason = "SL"
                else:
                    tp_px = avg * (1 + tp_dca * side)
                    if (i - pos["l2_i"]) >= BE_WAIT_BARS:
                        pk = pos["peak_fav"]
                        if pk >= TRAIL_ARM:
                            sl_px = avg * (1 + (pk - TRAIL_BUF) / 100.0 * side)
                            sl_reason = "L2_TRAIL"
                        else:
                            sl_px, sl_reason = avg, "BE-DCA"
                    else:
                        sl_px, sl_reason = None, None

                tp_hit = (h >= tp_px) if side == 1 else (l <= tp_px)
                sl_hit = sl_px is not None and ((l <= sl_px) if side == 1 else (h >= sl_px))
                if sl_hit:                                   # pessimistic: stop first
                    opened_beyond = (o < sl_px) if side == 1 else (o > sl_px)
                    if opened_beyond:
                        gap_through_stops += 1
                        fiction_usd += abs(sl_px - o) * pos["qty"]
                    sl_fill = (min(sl_px, o) if side == 1 else max(sl_px, o)) if honest else sl_px
                    close_trade(i, sl_fill, sl_reason, market_order=True)
                elif tp_hit:
                    tp_fill = (max(tp_px, o) if side == 1 else min(tp_px, o)) if honest else tp_px
                    close_trade(i, tp_fill, "TP", market_order=True)

            # 3) trend-flip, profit-only, at close
            if pos is not None and not np.isnan(trend) and trend != pos["entry_trend"]:
                if (c - avg) * pos["qty"] * side > 0:
                    close_trade(i, c, "TREND_FLIP", market_order=True)

            # 4) SMART time-SL at close, losers only
            if pos is not None and (i - pos["open_i"]) >= time_sl:
                if (c - avg) * pos["qty"] * side - fees_on(c * pos["qty"]) < 0:
                    close_trade(i, c, "TIME_SL", market_order=True)

            # 5) survived the bar: update trail peak (close-sampled) + MTM equity
            if pos is not None:
                if pos["legs"] == 2:
                    fav = (c - avg) / avg * 100.0 * side
                    pos["peak_fav"] = max(pos["peak_fav"], fav)
                adv = (l if side == 1 else h)
                eq_low = balance + (adv - avg) * pos["qty"] * side - pos["fees_in"]
                min_eq = min(min_eq, eq_low)
                eq_peak = max(eq_peak, balance)
                max_dd_mtm = max(max_dd_mtm, (eq_peak - eq_low) / eq_peak)

        # ── signal at this bar's close ──
        if pos is None and pending is None:
            r, a = RSI[i], ATRP[i]
            if np.isnan(r) or np.isnan(a) or a > ATR_MAX_PCT:
                continue
            if r <= RSI_LONG:
                pending = (1, a)
            elif r >= RSI_SHORT:
                pending = (-1, a)

    return dict(balance=balance, trades=trades, exits=exits,
                max_dd=max_dd * 100, max_dd_mtm=max_dd_mtm * 100, min_eq=min_eq,
                fiction_usd=fiction_usd, gap_through_stops=gap_through_stops)


def report(cfg, mode, r):
    tr = r["trades"]
    nets = np.array([t[0] for t in tr])
    w = int((nets > 1e-9).sum()); lo = int((nets < -1e-9).sum())
    neu = len(tr) - w - lo
    gw, gl = nets[nets > 0].sum(), nets[nets < 0].sum()
    pf = abs(gw / gl) if gl < 0 else float("inf")
    profit = r["balance"] - INITIAL_BAL
    print(f"\n══ {cfg}  {mode} ══   [claim: {CLAIMS[cfg]}]")
    print(f"  profit ${profit:+,.0f} | PF {pf:.3f} | avg/trade ${nets.mean():+.2f} | "
          f"DD closed {r['max_dd']:.2f}% / MTM {r['max_dd_mtm']:.2f}% | min equity ${r['min_eq']:,.0f}")
    print(f"  trades {len(tr):,}  W/L/N {w:,}/{lo:,}/{neu:,}  "
          f"WR ex-neutral {w/(w+lo)*100 if w+lo else 0:.1f}%  WR all {w/len(tr)*100 if tr else 0:.1f}%")
    print(f"  exits " + "  ".join(f"{k}={v}" for k, v in r["exits"].items() if v))
    print(f"  stop exits with bar already beyond stop: {r['gap_through_stops']:,} "
          f"(parity booking adds ≈ ${r['fiction_usd']:,.0f} of fiction)")
    yr = {}
    for net, _, t in tr:
        yr[t.year] = yr.get(t.year, 0.0) + net
    print("  years: " + "  ".join(f"{y}:{v:+,.0f}" for y, v in sorted(yr.items())))


def main():
    import sys
    want = sys.argv[1] if len(sys.argv) > 1 else ""
    bt = prep()
    print(f"Data: {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]}  ({len(bt):,} bars)")
    for cfg, p in CONFIGS.items():
        for mode, mk in MODES.items():
            if want and want.lower() not in mode.lower():
                continue
            r = run(bt, p["tp_dca"], p["time_sl"], **mk)
            report(cfg, mode, r)


if __name__ == "__main__":
    main()
