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
    # 24h range position (trailing 288 bars incl. current closed bar):
    # 0 = at 24h low, 1 = at 24h high. Known at bar close — no lookahead.
    hh = df["high"].rolling(288).max()
    ll = df["low"].rolling(288).min()
    df["rp24"] = (df["close"] - ll) / (hh - ll).replace(0, np.nan)
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


def run(bt, tp_dca, time_sl, fill, fee, slip, fee_maker=None,
        l1_arm=None, l1_buf=0.10, use_dca=True, sl_l1=SL_L1, fixed_cap=False,
        rp_long=None, rp_short=None, blocked_hours=(), atr_max=None,
        signal_mask=None, signal_sides=None, gap_min=GAP_MIN,
        exit_on_break=None, sl_struct=None, sl_struct_buf=0.001, struct_rr=None,
        sl_levels=None, per_leg_tp=False,
        compound=False, mtm_stop_pct=None, daily_fixed=None, ratchet=None,
        gap_exit_abs=None, gap_exit_frac=None, gap_exit_profit_only=False,
        candle_exit=False, candle_exit_profit_only=False, tp_levels=None,
        line_exit=None, rsi_exit=None, rsi_exit_profit_only=False):
    """l1_arm/l1_buf (percent units): optional L1 trailing stop — arms once the
    close-sampled peak favorable move >= l1_arm, then a stop trails l1_buf
    behind the peak (honest market fill). While armed the DCA is unreachable
    (the trail sits above the trigger, so price hits it first on the way down).
    use_dca=False disables L2 entirely. fixed_cap=True computes the daily-loss
    cap on INITIAL_BAL instead of current balance (fixed-stake ledger mode so
    variant comparisons don't freeze when a variant's balance goes negative).
    """
    honest = fill == "honest"
    exchange = fill == "exchange"
    assert not (exchange and l1_arm is not None), "L1 trail not modeled for exchange mode"
    assert not (per_leg_tp and exchange), "grid mode not modeled for exchange fills"
    assert not (per_leg_tp and l1_arm is not None), "grid mode ignores the L1 trail — don't combine"
    fee_maker = fee if fee_maker is None else fee_maker
    rp_arr = bt["rp24"].values if "rp24" in bt.columns else None
    signal_mask_arr = np.asarray(signal_mask, dtype=bool) if signal_mask is not None else None
    if signal_mask_arr is not None and len(signal_mask_arr) != len(bt):
        raise ValueError("signal_mask must have one value per backtest bar")
    signal_sides_arr = np.asarray(signal_sides, dtype=np.int8) if signal_sides is not None else None
    if signal_sides_arr is not None and len(signal_sides_arr) != len(bt):
        raise ValueError("signal_sides must have one value per backtest bar")
    hours = bt["timestamp"].dt.hour.values if blocked_hours else None
    eff_atr_max = ATR_MAX_PCT if atr_max is None else atr_max
    if exit_on_break:
        lo_brk = bt["low"].shift(1).rolling(exit_on_break).min().values
        hi_brk = bt["high"].shift(1).rolling(exit_on_break).max().values
    if sl_levels is not None:           # (lo_arr, hi_arr): any indicator level
        lo_s, hi_s = sl_levels
    elif sl_struct:                     # rolling N-bar extremes (S/R)
        lo_s = bt["low"].shift(1).rolling(sl_struct).min().values
        hi_s = bt["high"].shift(1).rolling(sl_struct).max().values
    have_struct = sl_levels is not None or bool(sl_struct)
    balance = INITIAL_BAL
    peak, max_dd = balance, 0.0
    eq_peak, max_dd_mtm, min_eq = balance, 0.0, balance
    pos = None
    pending = None           # (side, entry_trend) queued from prior bar signal
    cooldown_until_i = -1
    daily_loss = {}          # UTC date -> sum of negative nets (mode's own net)
    trades = []
    exits = {k: 0 for k in ("TP", "TP_L2LEG", "BE-DCA", "L2_TRAIL", "L1_TRAIL",
                            "SL", "TREND_FLIP", "TIME_SL", "BREAKOUT", "MTM_STOP",
                            "GAP_FADE", "CANDLE")}
    eq_floor = (INITIAL_BAL * ratchet["floor_start"]) if ratchet else None
    halted_at = None
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
        trades.append((net, reason, t_exit, pos["open_i"], side, pos["legs"]))
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
            cap_base = INITIAL_BAL if fixed_cap else balance
            day_cap = daily_fixed if daily_fixed is not None else DAILY_CAP_PCT * cap_base
            ok = (not np.isnan(trend) and not np.isnan(gap) and gap >= gap_min
                  and i >= cooldown_until_i
                  and daily_loss.get(t_open.date(), 0.0) > -day_cap)
            if ok and ratchet:
                peak_now = max(peak, balance)
                if peak_now >= INITIAL_BAL * ratchet["arm"]:
                    eq_floor = max(eq_floor, peak_now * (1 - ratchet["giveback"]))
                if halted_at is not None or balance <= eq_floor:
                    if halted_at is None:
                        halted_at = pd.Timestamp(ts[i])
                    ok = False
            if ok and have_struct:
                # no valid level below/above the entry -> skip the trade
                lvl = lo_s[i] if side == 1 else hi_s[i]
                if np.isnan(lvl):
                    ok = False
                else:
                    sl_cand = lvl * (1 - sl_struct_buf * side)
                    if (sl_cand >= o * (1 - 0.0005)) if side == 1 else (sl_cand <= o * (1 + 0.0005)):
                        ok = False
            if ok:
                eff = o * (1 + slip * side)
                leg_notional = (balance * 0.95 * 5.0 / 2.0) if compound else PER_LEG_NOTIONAL
                if leg_notional <= 0:
                    ok = False
            if ok:
                qty = leg_notional / eff
                pos = {"side": side, "avg": eff, "worst": eff, "qty": qty,
                       "legs": 1, "open_i": i, "l2_i": -1,
                       "entry_trend": trend, "peak_fav": 0.0, "be_live": False,
                       "l1_peak": 0.0, "fees_in": fees_on(eff * qty),
                       "l1_px": eff, "l1_qty": qty, "fee1": fees_on(eff * qty),
                       "leg_n": leg_notional if compound else PER_LEG_NOTIONAL,
                       "gap0": gap}

        if pos is not None:
            side = pos["side"]
            l2_this_bar = False

            # L1 trail arms off the close-sampled peak of PRIOR bars (no lookahead)
            l1_armed = (pos["legs"] == 1 and l1_arm is not None
                        and pos["l1_peak"] >= l1_arm)

            # 1) DCA first (bot: maybe_dca before exit checks). With the trail
            # armed the trail price sits above the DCA trigger, so a falling
            # price exits at the trail before the DCA can ever fill.
            if pos["legs"] == 1 and use_dca and not l1_armed:
                trig = pos["worst"] * (1 - DCA_SPACING * side)
                if (l <= trig) if side == 1 else (h >= trig):
                    if exchange:                      # resting limit fills at its price
                        eff = trig
                    else:
                        raw = (min(trig, o) if side == 1 else max(trig, o)) if honest else trig
                        eff = raw * (1 + slip * side)
                    q2 = pos.get("leg_n", PER_LEG_NOTIONAL) / eff
                    pos["fees_in"] += fees_on(eff * q2, maker=exchange)
                    pos["avg"] = (pos["avg"] * pos["qty"] + eff * q2) / (pos["qty"] + q2)
                    pos["qty"] += q2
                    pos["worst"] = min(pos["worst"], eff) if side == 1 else max(pos["worst"], eff)
                    pos["legs"] = 2
                    pos["l2_i"] = i
                    pos["l2_px"] = eff
                    pos["l2_qty"] = q2
                    pos["fee2"] = fees_on(eff * q2, maker=exchange)
                    l2_this_bar = True

            avg = pos["avg"]

            # 1b) MTM basket stop (live RSISCALP_MTM_STOP_PCT): close everything
            # when unrealized loss reaches pct of current balance
            if mtm_stop_pct is not None:
                lvl = avg - side * mtm_stop_pct * balance / pos["qty"]
                if (l <= lvl) if side == 1 else (h >= lvl):
                    fillp = (min(lvl, o) if side == 1 else max(lvl, o)) if honest else lvl
                    close_trade(i, fillp, "MTM_STOP", market_order=True)

            # 2) TP / stop (deferred on the L2-fill bar)
            if pos is not None and not l2_this_bar and exchange:
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
            elif pos is not None and not l2_this_bar:
                grid_handled = False
                if per_leg_tp and pos["legs"] == 2:
                    grid_handled = True
                    # GRID MODE: each leg exits at +0.5% from ITS OWN entry.
                    # No basket BE/trail/SL while both legs ride (grid
                    # semantics); smart time-SL below remains the backstop.
                    tp2 = pos["l2_px"] * (1 + TP_SINGLE * side)
                    if (h >= tp2) if side == 1 else (l <= tp2):
                        fill = (max(tp2, o) if side == 1 else min(tp2, o)) if honest else tp2
                        eff2 = fill * (1 - slip * side)
                        net2 = ((eff2 - pos["l2_px"]) * pos["l2_qty"] * side
                                - fees_on(eff2 * pos["l2_qty"]) - pos["fee2"])
                        balance += net2
                        peak = max(peak, balance)
                        trades.append((net2, "TP_L2LEG",
                                       pd.Timestamp(ts[i]) + pd.Timedelta(minutes=5),
                                       pos["open_i"], side, 2))
                        exits["TP_L2LEG"] += 1
                        pos["legs"] = 1                      # revert to pure L1
                        pos["qty"] = pos["l1_qty"]
                        pos["avg"] = avg = pos["l1_px"]
                        pos["worst"] = pos["l1_px"]
                        pos["fees_in"] = pos["fee1"]
                        tp1 = pos["l1_px"] * (1 + TP_SINGLE * side)
                        if (h >= tp1) if side == 1 else (l <= tp1):
                            fill1 = (max(tp1, o) if side == 1 else min(tp1, o)) if honest else tp1
                            close_trade(i, fill1, "TP", market_order=True)
                elif pos["legs"] == 1:
                    tp_px = avg * (1 + TP_SINGLE * side)
                    if tp_levels is not None:
                        lvl = tp_levels[0][i] if side == 1 else tp_levels[1][i]
                        if not np.isnan(lvl):
                            tp_px = lvl
                    if l1_armed:
                        sl_px = avg * (1 + (pos["l1_peak"] - l1_buf) / 100.0 * side)
                        sl_reason = "L1_TRAIL"
                    elif have_struct:
                        lvl = lo_s[i] if side == 1 else hi_s[i]
                        sl_px = (lvl * (1 - sl_struct_buf * side)
                                 if not np.isnan(lvl)
                                 else pos["worst"] * (1 - sl_l1 * side))
                        sl_reason = "SL"
                        if struct_rr is not None:
                            tp_px = avg + struct_rr * abs(avg - sl_px) * side
                    else:
                        sl_px = pos["worst"] * (1 - sl_l1 * side)
                        sl_reason = "SL"
                else:
                    tp_px = avg * (1 + tp_dca * side)
                    if tp_levels is not None:
                        lvl = tp_levels[0][i] if side == 1 else tp_levels[1][i]
                        if not np.isnan(lvl):
                            tp_px = lvl
                    if (i - pos["l2_i"]) >= BE_WAIT_BARS:
                        pk = pos["peak_fav"]
                        if pk >= TRAIL_ARM:
                            sl_px = avg * (1 + (pk - TRAIL_BUF) / 100.0 * side)
                            sl_reason = "L2_TRAIL"
                        else:
                            sl_px, sl_reason = avg, "BE-DCA"
                    else:
                        sl_px, sl_reason = None, None

                if grid_handled:
                    tp_hit = sl_hit = False                  # grid branch did its own exits
                else:
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

            # 3e) RSI exit: RSI recovers to the threshold -> close at market
            # (long exits at RSI >= thr; short at RSI <= 100-thr)
            if pos is not None and rsi_exit is not None and not np.isnan(RSI[i]):
                if (RSI[i] >= rsi_exit) if side == 1 else (RSI[i] <= 100 - rsi_exit):
                    if (not rsi_exit_profit_only
                            or (c - avg) * pos["qty"] * side > 0):
                        close_trade(i, c, "GAP_FADE", market_order=True)

            # 3d) line exit: bar CLOSES across the given line against the
            # position (e.g. 15m EMA50) -> close everything at market
            if pos is not None and line_exit is not None:
                lv = line_exit[i]
                if not np.isnan(lv) and ((c < lv) if side == 1 else (c > lv)):
                    close_trade(i, c, "BREAKOUT", market_order=True)

            # 3c) candlestick exit: opposing ENGULFING candle closes against
            # the position -> exit at close (market)
            if pos is not None and candle_exit and i > 0:
                if side == 1:
                    eng = (c < o) and (o >= C[i-1]) and (c <= O[i-1])
                else:
                    eng = (c > o) and (o <= C[i-1]) and (c >= O[i-1])
                if eng and (not candle_exit_profit_only
                            or (c - avg) * pos["qty"] * side > 0):
                    close_trade(i, c, "CANDLE", market_order=True)

            # 3b) gap-fade exit: 15m EMA gap shrinking after entry = trend
            # firmness fading -> close at bar close (market)
            if pos is not None and (gap_exit_abs is not None or gap_exit_frac is not None) \
                    and not np.isnan(gap):
                thr = (pos["gap0"] * gap_exit_frac if gap_exit_frac is not None
                       else gap_exit_abs)
                if gap < thr:
                    if (not gap_exit_profit_only
                            or (c - avg) * pos["qty"] * side > 0):
                        close_trade(i, c, "GAP_FADE", market_order=True)

            # 4) SMART time-SL at close, losers only
            if pos is not None and (i - pos["open_i"]) >= time_sl:
                if (c - avg) * pos["qty"] * side - fees_on(c * pos["qty"]) < 0:
                    close_trade(i, c, "TIME_SL", market_order=True)

            # 4b) breakout exit: bar CLOSES beyond the rolling N-bar extreme
            # against the position -> cut everything at close (market)
            if pos is not None and exit_on_break:
                if (c < lo_brk[i]) if side == 1 else (c > hi_brk[i]):
                    close_trade(i, c, "BREAKOUT", market_order=True)

            # 5) survived the bar: update trail peaks (close-sampled) + MTM equity
            if pos is not None:
                fav = (c - avg) / avg * 100.0 * side
                if pos["legs"] == 2:
                    pos["peak_fav"] = max(pos["peak_fav"], fav)
                else:
                    pos["l1_peak"] = max(pos["l1_peak"], fav)
                adv = (l if side == 1 else h)
                eq_low = balance + (adv - avg) * pos["qty"] * side - pos["fees_in"]
                min_eq = min(min_eq, eq_low)
                eq_peak = max(eq_peak, balance)
                max_dd_mtm = max(max_dd_mtm, (eq_peak - eq_low) / eq_peak)

        # ── signal at this bar's close ──
        if pos is None and pending is None:
            if signal_mask_arr is not None and not signal_mask_arr[i]:
                continue
            a = ATRP[i]
            if np.isnan(a) or a > eff_atr_max:
                continue
            if hours is not None and hours[i] in blocked_hours:
                continue
            if signal_sides_arr is not None:
                side = int(signal_sides_arr[i])
                if side:
                    pending = (side, a)
                continue
            r = RSI[i]
            if np.isnan(r):
                continue
            if r <= RSI_LONG:
                if rp_long is not None:
                    rp = rp_arr[i]
                    if np.isnan(rp) or rp > rp_long:
                        continue          # 24h range gate: longs only near the low
                pending = (1, a)
            elif r >= RSI_SHORT:
                if rp_short is not None:
                    rp = rp_arr[i]
                    if np.isnan(rp) or rp < rp_short:
                        continue          # shorts only near the 24h high
                pending = (-1, a)

    return dict(balance=balance, trades=trades, exits=exits,
                max_dd=max_dd * 100, max_dd_mtm=max_dd_mtm * 100, min_eq=min_eq,
                fiction_usd=fiction_usd, gap_through_stops=gap_through_stops,
                halted_at=halted_at, eq_floor=eq_floor)


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
    for net, _, t, *__ in tr:
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
