#!/usr/bin/env python3
"""rsiscalp_backtest.py — Faithful backtest of bot_rsiscalp + double-filter experiments.

Mirrors strategies/day/bot_rsiscalp.py logic on 5m closed-bar data:
  - RSI9 25/75 (configurable) entries
  - 2-leg DCA @ 0.5% adverse, equal size
  - adaptive TP: 0.50% (L1) / 0.25% (L2) from avg
  - 1% SL from worst entry, 3x leverage
  - circuit breaker: pause 2h after 2 consecutive losses

Bar-level fill model (conservative): on each NEW 5m bar after entry we check, in
order, DCA -> SL -> TP using that bar's high/low. Worst-case ordering: if a bar's
range spans both SL and TP, SL wins (pessimistic). Entries fire at next bar open
after a closed-bar RSI signal.

Adds optional double filters (--filter) for the DD-reduction study:
  none      : pure RSI (baseline)
  ema200    : LONG only above 5m EMA200, SHORT only below (trend regime)
  ema50_200 : LONG only when EMA50>EMA200, SHORT when EMA50<EMA200
  trend15m  : the bot's shipped 15m EMA20/50 gate
  bb        : LONG only when close < lower Bollinger(20,2), SHORT only > upper
  bb_or_rsi : RSI signal must ALSO be a BB-band tag (stricter mean-reversion)
  adx_lt    : skip entries when ADX(14) > threshold (avoid strong trends)
"""
from __future__ import annotations
import argparse, sys, os
import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cache")


# ─── indicators ───
def rsi_series(s, n=9):
    d = s.diff()
    gain = d.clip(lower=0); loss = -d.clip(upper=0)
    ag = gain.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    al = loss.ewm(alpha=1.0/n, min_periods=n, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def adx(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff(); dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([(h - l), (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0/n, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1.0/n, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1.0/n, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1.0/n, adjust=False).mean()


# ─── config (mirror core_rsiscalp.py defaults) ───
class Cfg:
    LEVERAGE = 3.0
    RSI_PERIOD = 9
    RSI_OVERSOLD = 25
    RSI_OVERBOUGHT = 75
    DCA_LEVELS = 2
    DCA_SPACING = 0.005
    TP_SINGLE = 0.005
    TP_DCA = 0.0025
    SL = 0.01
    COMMISSION = 0.0004
    USE_BREAKER = True
    BREAKER_LOSSES = 2
    BREAKER_PAUSE_BARS = 24  # 2h / 5m
    INITIAL = 5000.0
    DD_STOP = 0.0
    HOOK = False  # enter on RSI crossing BACK through the band (bounce confirm)
    FLIP_ON_SL = False  # on an SL exit, immediately open the OPPOSITE side
    WIN_COOLDOWN = 0      # bars to block entries after ANY winning close
    WINSTREAK_N = 0       # after this many consecutive wins...
    WINSTREAK_COOL = 0    # ...block entries this many bars
    BE_TRIG = 0.0   # >0: ratchet SL to avg once price runs this far favorable
    TRAIL = 0.0     # >0: trail SL this far behind best favorable price
    NO_TP = False   # True: disable the fixed TP (let trailing stop bank winners)
    TRAIL_CLOSE = 0.0  # conservative close-based trailing distance
    VOL_CAP = 0.0   # 2026-06-05: >0: skip entries when vol[i] > vol_cap × SMA(20, volume)
                    # (user theory: extreme volume = breakout/news, mean-rev fails)
    # 2026-06-05: multi-signal "true breakout" exit. Count how many of these
    # are true on the current bar; if count >= MULTI_EXIT_THRESHOLD, market-exit.
    #   Sign 1: close beyond opposite 5m BB band (tactical, fast)
    #   Sign 2: close beyond opposite 15m BB band (HTF confirm)
    #   Sign 3: volume[i] > 2× SMA(20, vol) (institutional flow)
    #   Sign 4: BBW expanded > 50% since trade entry (regime change)
    #   Sign 5: 2 consecutive 5m closes > 0.4% adverse from entry (sustained pressure)
    MULTI_EXIT_THRESHOLD = 0   # 0 = disabled; 3 = "3-of-5" recommended
    MULTI_EXIT_VOL_MULT  = 2.0
    MULTI_EXIT_BBW_EXP   = 0.50      # 50% BBW expansion since entry
    MULTI_EXIT_ADVERSE   = 0.004     # 0.4% adverse close for streak
    # 2026-06-05: v2 — from live-trade analysis (3 of 4 DCA fills were near-miss SL)
    TREND_STABILITY = 0    # require N consecutive 15m bars same trend before entry (0 = off)
    L2_GATE = False        # skip L2 DCA if vol-spike OR BBW expansion OR price beyond opp BB
    L2_GATE_VOL_MULT = 1.5
    L2_GATE_BBW_EXP  = 0.30    # 30% BBW expansion in last hour
    BB_BREAK_EXIT = False  # close at next bar if close beyond opposite 5m BB
    # 2026-06-05: TREND FIRMNESS — require |EMA20-EMA50|/EMA50 >= this %.
    # Direct answer to user's "what if RSI hits 70 right when trend is about to flip up" worry.
    TREND_GAP_MIN = 0.0    # e.g. 0.0015 = require 0.15% gap. 0 = off
    # Need 15m EMA arrays for this — added in main()
    # 2026-06-06: USER IDEA — "peel" the L2 leg at a small profit and let L1 ride.
    # When L2 has filled, sell ONLY the L2 portion once it runs L2_PEEL_TP above its
    # own entry, halving exposure. Position reverts to single-leg L1 (no further DCA).
    L2_PEEL = False
    L2_PEEL_TP = 0.0025    # sell L2 leg at +0.25% above L2 entry (its own basis)


def tp_pct_for(filled):
    return Cfg.TP_SINGLE if filled <= 1 else Cfg.TP_DCA


def run(df, cfg=Cfg, filt="none", adx_thresh=30.0, verbose=False):
    c = df["close"].values
    h = df["high"].values
    l = df["low"].values
    o = df["open"].values
    rsi = df["rsi"].values
    vol = df["volume"].values if "volume" in df.columns else None
    vol_sma = df["vol_sma20"].values if "vol_sma20" in df.columns else None
    # 2026-06-05 multi-signal exit support
    bb_up_arr  = df["bb_up"].values  if "bb_up"  in df.columns else None
    bb_low_arr = df["bb_low"].values if "bb_low" in df.columns else None
    bbw_arr    = df["bbw"].values    if "bbw"    in df.columns else None
    bb_up15_arr  = df["bb_up15"].values  if "bb_up15"  in df.columns else None
    bb_low15_arr = df["bb_low15"].values if "bb_low15" in df.columns else None

    # filter masks computed per bar (evaluated on the SIGNAL bar = closed bar i)
    long_ok = np.ones(len(df), dtype=bool)
    short_ok = np.ones(len(df), dtype=bool)
    if filt == "ema200":
        e = df["ema200"].values
        long_ok = c > e; short_ok = c < e
    elif filt == "ema50_200":
        e50, e200 = df["ema50"].values, df["ema200"].values
        long_ok = e50 > e200; short_ok = e50 < e200
    elif filt == "trend15m":
        long_ok = df["trend_up"].values; short_ok = ~df["trend_up"].values
    elif filt == "rsi1h":
        # 2026-06-06: live v1's HTF gate — only fade WITH 1h momentum (RSI side of 50)
        hr = df["rsi_1h"].values
        long_ok = hr > 50; short_ok = hr < 50
    elif filt == "bb":
        long_ok = c < df["bb_low"].values; short_ok = c > df["bb_up"].values
    elif filt == "bb_or_rsi":
        long_ok = c < df["bb_low"].values; short_ok = c > df["bb_up"].values
    elif filt == "adx_lt":
        a = df["adx"].values
        long_ok = a < adx_thresh; short_ok = a < adx_thresh
    elif filt == "ema200_adx":
        # trend-aligned AND not a violent trend (adx below thresh)
        e = df["ema200"].values; a = df["adx"].values
        long_ok = (c > e) & (a < adx_thresh); short_ok = (c < e) & (a < adx_thresh)
    elif filt == "ema200_atr":
        # trend-aligned AND enough volatility for mean reversion to pay
        e = df["ema200"].values; ap = df["atr_pct"].values
        med = np.nanmedian(ap)
        long_ok = (c > e) & (ap > med); short_ok = (c < e) & (ap > med)
    elif filt == "ema200_htf":
        # 5m trend-aligned AND 1h RSI in the same camp (not already exhausted)
        e = df["ema200"].values; hr = df["rsi_1h"].values
        long_ok = (c > e) & (hr < 60); short_ok = (c < e) & (hr > 40)
    elif filt == "ema200_bbw":
        # trend-aligned AND bands wide enough (avoid dead chop / squeezes)
        e = df["ema200"].values; bw = df["bbw"].values
        med = np.nanmedian(bw)
        long_ok = (c > e) & (bw > med); short_ok = (c < e) & (bw > med)
    elif filt == "ema100":
        e = df["ema100"].values
        long_ok = c > e; short_ok = c < e
    elif filt == "ema200_slope":
        # trend-aligned AND the EMA200 itself sloping the right way
        e = df["ema200"].values; sl = df["ema200_slope"].values
        long_ok = (c > e) & (sl > 0); short_ok = (c < e) & (sl < 0)
    elif filt == "regime_long":
        # only take LONGs in uptrend, only SHORTs in downtrend, AND skip the
        # counter-trend extreme entirely (no shorting in an uptrend at all)
        e = df["ema200"].values
        long_ok = c > e; short_ok = c < e

    ema200_arr = df["ema200"].values if "ema200" in df.columns else None
    balance = cfg.INITIAL
    peak = balance
    max_dd = 0.0
    pos = None
    consec_losses = 0
    consec_wins = 0
    win_pause_until = -1
    pause_until = -1
    trades = []
    equity_curve = []

    def per_leg_qty(bal, px):
        return (bal * 0.95 * cfg.LEVERAGE / px) / cfg.DCA_LEVELS

    for i in range(len(df)):
        if np.isnan(rsi[i]):
            continue
        # ---- manage open position using THIS bar's range (bar i) ----
        if pos is not None:
            side = pos["side"]
            # track max adverse excursion (deepest move against us, intrabar)
            pos["extreme"] = min(pos["extreme"], l[i]) if side == "LONG" else max(pos["extreme"], h[i])
            if not np.isnan(rsi[i]):
                pos["rsi_ext"] = min(pos.get("rsi_ext", rsi[i]), rsi[i]) if side == "LONG" else max(pos.get("rsi_ext", rsi[i]), rsi[i])
            # DCA check
            if pos["filled"] < cfg.DCA_LEVELS:
                trig = pos["worst"] * (1 - cfg.DCA_SPACING) if side == "LONG" else pos["worst"] * (1 + cfg.DCA_SPACING)
                hit = (side == "LONG" and l[i] <= trig) or (side == "SHORT" and h[i] >= trig)
                if hit:
                    # 2026-06-05: v2 L2 GATE — skip DCA if anomalous conditions at trigger
                    gate_block = False
                    if cfg.L2_GATE:
                        # Sign A: volume spike
                        if vol is not None and vol_sma is not None and vol_sma[i] > 0:
                            if vol[i] > vol_sma[i] * cfg.L2_GATE_VOL_MULT:
                                gate_block = True
                        # Sign B: BBW expanded > X% in last hour (12 5m-bars)
                        if not gate_block and bbw_arr is not None and i >= 12:
                            if not (np.isnan(bbw_arr[i]) or np.isnan(bbw_arr[i-12]) or bbw_arr[i-12] <= 0):
                                if bbw_arr[i] / bbw_arr[i-12] > (1 + cfg.L2_GATE_BBW_EXP):
                                    gate_block = True
                        # Sign C: close beyond opposite 5m BB band
                        if not gate_block and bb_up_arr is not None and bb_low_arr is not None:
                            if not (np.isnan(bb_up_arr[i]) or np.isnan(bb_low_arr[i])):
                                if side == "LONG" and c[i] < bb_low_arr[i]: gate_block = True
                                if side == "SHORT" and c[i] > bb_up_arr[i]: gate_block = True
                    if gate_block:
                        # Mark that we BLOCKED a DCA — used for exit reason analysis
                        pos["dca_blocked"] = pos.get("dca_blocked", 0) + 1
                    else:
                        q = per_leg_qty(balance, trig)
                        balance -= trig * q * cfg.COMMISSION
                        pos["entries"].append((trig, q))
                        pos["worst"] = min(pos["worst"], trig) if side == "LONG" else max(pos["worst"], trig)
                        pos["qty"] = sum(e[1] for e in pos["entries"])
                        pos["filled"] += 1
                        pos["rsi_l2"] = rsi[i]
                        pos["l2_i"] = i
            avg = sum(p*q for p, q in pos["entries"]) / pos["qty"]
            # --- 2026-06-06 USER IDEA: peel L2 at a small profit, keep L1 riding ---
            # Sell only the L2 leg once it runs L2_PEEL_TP above its own entry. Reverts
            # the position to single-leg L1. Pessimistic: never peel on the same bar L2
            # filled (would assume a favorable intrabar wick order).
            if (cfg.L2_PEEL and pos["filled"] >= 2 and not pos.get("peeled")
                    and pos.get("l2_i") != i):
                l2_px, l2_q = pos["entries"][1]
                peel_tp = l2_px * (1 + cfg.L2_PEEL_TP) if side == "LONG" else l2_px * (1 - cfg.L2_PEEL_TP)
                peel_hit = (side == "LONG" and h[i] >= peel_tp) or (side == "SHORT" and l[i] <= peel_tp)
                if peel_hit:
                    pgross = (peel_tp - l2_px) * l2_q if side == "LONG" else (l2_px - peel_tp) * l2_q
                    pfees = peel_tp * l2_q * cfg.COMMISSION
                    pnet = pgross - pfees
                    balance += pnet
                    trades.append({"side": side, "reason": "PEEL", "net": pnet,
                                   "pnl_pct": pnet / balance * 100, "filled": 2,
                                   "entry_i": pos["entry_i"], "exit_i": i,
                                   "bars_held": i - pos["entry_i"], "rsi_entry": pos["rsi_entry"],
                                   "mae_first": 0.0, "mae_avg": 0.0,
                                   "rsi_l2": pos.get("rsi_l2"), "rsi_ext": pos.get("rsi_ext"),
                                   "l2_i": pos.get("l2_i")})
                    # revert to single-leg L1
                    pos["entries"] = [pos["entries"][0]]
                    pos["qty"] = pos["entries"][0][1]
                    pos["worst"] = pos["entries"][0][0]
                    pos["filled"] = 1
                    pos["peeled"] = True
                    avg = pos["entries"][0][0]
            # base catastrophic SL anchored to worst entry
            slp = pos["worst"] * (1 - cfg.SL) if side == "LONG" else pos["worst"] * (1 + cfg.SL)
            # --- breakeven: once price runs +BE_TRIG from avg, ratchet SL to avg (lock no-loss) ---
            if cfg.BE_TRIG > 0:
                be_lvl = avg * (1 + cfg.BE_TRIG) if side == "LONG" else avg * (1 - cfg.BE_TRIG)
                reached = (side == "LONG" and h[i] >= be_lvl) or (side == "SHORT" and l[i] <= be_lvl)
                if reached:
                    pos["be_armed"] = True
                if pos.get("be_armed"):
                    slp = max(slp, avg) if side == "LONG" else min(slp, avg)
            tp_pct = tp_pct_for(pos["filled"])
            tpp = avg * (1 + tp_pct) if side == "LONG" else avg * (1 - tp_pct)
            exit_px = None; reason = None
            # --- CONSERVATIVE close-based trailing (TRAIL_CLOSE): anchor to running
            #     max of CLOSES, trigger only when a bar CLOSES through the stop,
            #     fill at that close. Removes the intrabar "bank the high" artifact. ---
            if cfg.TRAIL_CLOSE > 0:
                best = pos.get("bestc", avg)
                if side == "LONG":
                    stop = best * (1 - cfg.TRAIL_CLOSE)
                    if best > avg * (1 + cfg.TRAIL_CLOSE) and c[i] <= stop:
                        exit_px, reason = c[i], ("BE" if c[i] >= avg else "SL")
                else:
                    stop = best * (1 + cfg.TRAIL_CLOSE)
                    if best < avg * (1 - cfg.TRAIL_CLOSE) and c[i] >= stop:
                        exit_px, reason = c[i], ("BE" if c[i] <= avg else "SL")
            # --- optimistic intrabar trailing (TRAIL) — kept for reference only ---
            if exit_px is None and cfg.TRAIL > 0:
                best = pos.get("best", avg)
                if side == "LONG" and best > avg * (1 + cfg.TRAIL):
                    slp = max(slp, best * (1 - cfg.TRAIL))
                elif side == "SHORT" and best < avg * (1 - cfg.TRAIL):
                    slp = min(slp, best * (1 + cfg.TRAIL))
            # --- EMA200 break exit: after L2, bail if price closed >X% beyond EMA200
            #     (the one weak winner/loser signal — testing if it pays) ---
            if exit_px is None and cfg.EMA200_EXIT > 0 and pos["filled"] >= 2 and ema200_arr is not None:
                e2 = ema200_arr[i]
                if not np.isnan(e2):
                    if side == "LONG" and c[i] < e2 * (1 - cfg.EMA200_EXIT):
                        exit_px, reason = c[i], "EMA200X"
                    elif side == "SHORT" and c[i] > e2 * (1 + cfg.EMA200_EXIT):
                        exit_px, reason = c[i], "EMA200X"

            # --- 2026-06-05: v2 BB BREAK EXIT (single fast signal) ---
            if exit_px is None and cfg.BB_BREAK_EXIT and bb_up_arr is not None and bb_low_arr is not None:
                if not (np.isnan(bb_up_arr[i]) or np.isnan(bb_low_arr[i])):
                    if side == "LONG" and c[i] < bb_low_arr[i]:
                        exit_px, reason = c[i], "BBBRK"
                    elif side == "SHORT" and c[i] > bb_up_arr[i]:
                        exit_px, reason = c[i], "BBBRK"

            # --- 2026-06-05: MULTI-SIGNAL "true breakout" exit ---
            # Counts how many of 5 signals confirm a real breakout against position.
            # Exits at close if count >= threshold. Tracks adverse-streak in pos["adv_streak"].
            if exit_px is None and cfg.MULTI_EXIT_THRESHOLD > 0:
                entry_px = pos["entries"][0][0]  # L1 entry as the reference for adverse-pct
                adverse_pct_now = (entry_px - c[i]) / entry_px if side == "LONG" else (c[i] - entry_px) / entry_px

                # Track 2-bar adverse streak (Sign 5 prerequisite)
                if adverse_pct_now > cfg.MULTI_EXIT_ADVERSE:
                    pos["adv_streak"] = pos.get("adv_streak", 0) + 1
                else:
                    pos["adv_streak"] = 0

                signs = 0
                # Sign 1: close beyond opposite 5m BB
                if bb_up_arr is not None and bb_low_arr is not None:
                    if not (np.isnan(bb_up_arr[i]) or np.isnan(bb_low_arr[i])):
                        if side == "LONG" and c[i] < bb_low_arr[i]: signs += 1
                        if side == "SHORT" and c[i] > bb_up_arr[i]: signs += 1
                # Sign 2: close beyond opposite 15m BB
                if bb_up15_arr is not None and bb_low15_arr is not None:
                    if not (np.isnan(bb_up15_arr[i]) or np.isnan(bb_low15_arr[i])):
                        if side == "LONG" and c[i] < bb_low15_arr[i]: signs += 1
                        if side == "SHORT" and c[i] > bb_up15_arr[i]: signs += 1
                # Sign 3: volume spike
                if vol is not None and vol_sma is not None:
                    if vol_sma[i] > 0 and vol[i] > vol_sma[i] * cfg.MULTI_EXIT_VOL_MULT:
                        signs += 1
                # Sign 4: BBW expanded > 50% since entry
                if bbw_arr is not None and "bbw_entry" in pos:
                    bbw_now = bbw_arr[i]
                    if not np.isnan(bbw_now) and pos["bbw_entry"] > 0:
                        if bbw_now / pos["bbw_entry"] > (1 + cfg.MULTI_EXIT_BBW_EXP):
                            signs += 1
                # Sign 5: 2 consecutive adverse closes > 0.4%
                if pos.get("adv_streak", 0) >= 2:
                    signs += 1

                if signs >= cfg.MULTI_EXIT_THRESHOLD:
                    exit_px, reason = c[i], f"MULTI{signs}"
            if exit_px is None:
                sl_hit = (side == "LONG" and l[i] <= slp) or (side == "SHORT" and h[i] >= slp)
                tp_hit = (not cfg.NO_TP) and ((side == "LONG" and h[i] >= tpp) or (side == "SHORT" and l[i] <= tpp))
                if sl_hit:
                    in_profit = (slp >= avg) if side == "LONG" else (slp <= avg)
                    exit_px, reason = slp, ("BE" if in_profit else "SL")
                elif tp_hit:
                    exit_px, reason = tpp, "TP"
            # update bests AFTER the exit check (prior-bar semantics)
            if side == "LONG":
                pos["best"] = max(pos.get("best", avg), h[i])
                pos["bestc"] = max(pos.get("bestc", avg), c[i])
            else:
                pos["best"] = min(pos.get("best", avg), l[i])
                pos["bestc"] = min(pos.get("bestc", avg), c[i])
            if exit_px is not None:
                qty = pos["qty"]
                gross = (exit_px - avg) * qty if side == "LONG" else (avg - exit_px) * qty
                fees = exit_px * qty * cfg.COMMISSION
                net = gross - fees
                bal_before = balance
                balance += net
                pnl_pct = net / bal_before * 100
                fe = pos["first_entry"]
                mae_first = (fe - pos["extreme"]) / fe * 100 if side == "LONG" else (pos["extreme"] - fe) / fe * 100
                mae_avg = (avg - pos["extreme"]) / avg * 100 if side == "LONG" else (pos["extreme"] - avg) / avg * 100
                trades.append({"side": side, "reason": reason, "net": net, "pnl_pct": pnl_pct,
                               "filled": pos["filled"], "entry_i": pos["entry_i"], "exit_i": i,
                               "bars_held": i - pos["entry_i"], "rsi_entry": pos["rsi_entry"],
                               "mae_first": mae_first, "mae_avg": mae_avg,
                               "rsi_l2": pos.get("rsi_l2"), "rsi_ext": pos.get("rsi_ext"),
                               "l2_i": pos.get("l2_i")})
                if cfg.USE_BREAKER:
                    if net <= 0:
                        consec_losses += 1
                        if consec_losses >= cfg.BREAKER_LOSSES:
                            pause_until = i + cfg.BREAKER_PAUSE_BARS
                            consec_losses = 0
                    else:
                        consec_losses = 0
                # win-cooldown bookkeeping (the user's pattern)
                if net > 0:
                    consec_wins += 1
                    if cfg.WIN_COOLDOWN > 0:
                        win_pause_until = max(win_pause_until, i + cfg.WIN_COOLDOWN)
                    if cfg.WINSTREAK_N > 0 and consec_wins >= cfg.WINSTREAK_N and cfg.WINSTREAK_COOL > 0:
                        win_pause_until = max(win_pause_until, i + cfg.WINSTREAK_COOL)
                        consec_wins = 0
                else:
                    consec_wins = 0
                pos = None
                # stop-and-reverse: open opposite side at the SL price
                if cfg.FLIP_ON_SL and reason == "SL" and i + 1 < len(df):
                    opp = "SHORT" if side == "LONG" else "LONG"
                    epx = exit_px
                    q2 = per_leg_qty(balance, epx)
                    balance -= epx * q2 * cfg.COMMISSION
                    pos = {"side": opp, "entries": [(epx, q2)], "qty": q2, "worst": epx,
                           "filled": 1, "entry_i": i, "rsi_entry": rsi[i],
                           "first_entry": epx, "extreme": epx, "flipped": True}

        # ---- entry on closed bar i signal -> fill at next bar open (i+1) ----
        if pos is None and i + 1 < len(df):
            sig = None
            if cfg.HOOK:
                if rsi[i-1] <= cfg.RSI_OVERSOLD and rsi[i] > cfg.RSI_OVERSOLD:
                    sig = "LONG"
                elif rsi[i-1] >= cfg.RSI_OVERBOUGHT and rsi[i] < cfg.RSI_OVERBOUGHT:
                    sig = "SHORT"
            elif rsi[i] <= cfg.RSI_OVERSOLD:
                sig = "LONG"
            elif rsi[i] >= cfg.RSI_OVERBOUGHT:
                sig = "SHORT"
            if sig and cfg.USE_BREAKER and i < pause_until:
                sig = None
            if sig and cfg.DD_STOP > 0 and (balance/peak - 1) <= -cfg.DD_STOP:
                sig = None
            if sig and i < win_pause_until:
                sig = None
            if sig == "LONG" and not long_ok[i]:
                sig = None
            if sig == "SHORT" and not short_ok[i]:
                sig = None
            # 2026-06-05: anti-volume filter (skip extreme-volume bars — likely breakouts)
            if sig and cfg.VOL_CAP > 0 and vol is not None and vol_sma is not None:
                v_now = vol[i]; v_avg = vol_sma[i]
                if v_avg > 0 and v_now > v_avg * cfg.VOL_CAP:
                    sig = None
            # 2026-06-05: v2 TREND FIRMNESS — require EMA gap to exceed minimum (not knife-edge).
            # For SHORT: gap must be < -TREND_GAP_MIN (firmly DOWN)
            # For LONG : gap must be > +TREND_GAP_MIN (firmly UP)
            if sig and cfg.TREND_GAP_MIN > 0 and "trend_gap_pct" in df.columns:
                gap = df["trend_gap_pct"].values[i]
                if not np.isnan(gap):
                    if sig == "LONG" and gap < cfg.TREND_GAP_MIN: sig = None
                    if sig == "SHORT" and gap > -cfg.TREND_GAP_MIN: sig = None
            # 2026-06-05: v2 TREND STABILITY — require N consecutive 15m bars same trend.
            # Counts in 5m-bar units: 15m = 3 bars, so 4×15m bars = 12 5m-bars.
            if sig and cfg.TREND_STABILITY > 0 and "trend_up" in df.columns:
                req_bars = cfg.TREND_STABILITY * 3  # convert 15m units to 5m-bar count
                if i >= req_bars:
                    if sig == "LONG":
                        # all last req_bars must be UP
                        if not df["trend_up"].values[i-req_bars+1:i+1].all():
                            sig = None
                    else:  # SHORT
                        if df["trend_up"].values[i-req_bars+1:i+1].any():
                            sig = None
                else:
                    sig = None  # not enough history
            if sig:
                entry_px = o[i+1]
                q = per_leg_qty(balance, entry_px)
                balance -= entry_px * q * cfg.COMMISSION
                # 2026-06-05: snapshot BBW at entry for multi-signal exit's "expansion" check
                bbw_entry = bbw_arr[i+1] if bbw_arr is not None and not np.isnan(bbw_arr[i+1] if i+1 < len(bbw_arr) else float('nan')) else 0.0
                pos = {"side": sig, "entries": [(entry_px, q)], "qty": q, "worst": entry_px,
                       "filled": 1, "entry_i": i+1, "rsi_entry": rsi[i],
                       "first_entry": entry_px, "extreme": entry_px,
                       "bbw_entry": bbw_entry, "adv_streak": 0}

        peak = max(peak, balance)
        dd = balance / peak - 1
        max_dd = min(max_dd, dd)
        equity_curve.append(balance)

    return {"balance": balance, "trades": trades, "max_dd": max_dd, "peak": peak,
            "equity": equity_curve}


def analyze(name, res):
    t = res["trades"]
    n = len(t)
    if n == 0:
        print(f"{name}: no trades"); return
    wins = [x for x in t if x["net"] > 0]
    losses = [x for x in t if x["net"] <= 0]
    gross_win = sum(x["net"] for x in wins)
    gross_loss = -sum(x["net"] for x in losses)
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    avg_win = gross_win / len(wins) if wins else 0
    avg_loss = gross_loss / len(losses) if losses else 0
    sl_count = sum(1 for x in t if x["reason"] == "SL")
    be_count = sum(1 for x in t if x["reason"] == "BE")
    tp_count = sum(1 for x in t if x["reason"] == "TP")
    ret = (res["balance"] / Cfg.INITIAL - 1) * 100
    yrs = 5.07
    cagr = ((res['balance']/Cfg.INITIAL)**(1/yrs)*100-100) if res['balance'] > 0 else -100
    print(f"\n=== {name} ===")
    print(f"  trades {n} | WR {len(wins)/n*100:.1f}% | PF {pf:.2f}")
    print(f"  final ${res['balance']:,.0f} | total {ret:+.0f}% | ~{cagr:+.0f}%/yr | maxDD {res['max_dd']*100:.1f}%")
    print(f"  avg win ${avg_win:+.2f} | avg loss ${-avg_loss:+.2f} | win/loss size {avg_win/avg_loss if avg_loss else 0:.2f}x")
    print(f"  exits: TP {tp_count} | SL {sl_count} | BE {be_count}")
    worst = sorted(t, key=lambda x: x["net"])[:3]
    print(f"  3 worst trades: " + ", ".join(f"${x['net']:+.0f}({x['reason']},L{x['filled']})" for x in worst))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filter", default="all")
    ap.add_argument("--start", default="2021-05-03")
    ap.add_argument("--rsi", type=int, default=9)
    ap.add_argument("--os", type=int, default=25)
    ap.add_argument("--ob", type=int, default=75)
    ap.add_argument("--adx", type=float, default=30.0)
    ap.add_argument("--sl", type=float, default=0.01)
    ap.add_argument("--lev", type=float, default=3.0)
    ap.add_argument("--tps", type=float, default=0.005)
    ap.add_argument("--tpd", type=float, default=0.0025)
    ap.add_argument("--be", type=float, default=0.0, help="breakeven trigger %% from avg (e.g. 0.003)")
    ap.add_argument("--trail", type=float, default=0.0, help="trailing stop distance (e.g. 0.004)")
    ap.add_argument("--notp", action="store_true", help="disable fixed TP (let trail bank winners)")
    ap.add_argument("--trailc", type=float, default=0.0, help="conservative close-based trailing distance")
    ap.add_argument("--blosses", type=int, default=2)
    ap.add_argument("--bpause", type=int, default=24, help="breaker pause in 5m bars")
    ap.add_argument("--ddstop", type=float, default=0.0, help="halt new entries once equity DD exceeds this (e.g. 0.25)")
    ap.add_argument("--flip", action="store_true", help="stop-and-reverse: open opposite side on SL")
    ap.add_argument("--emaexit", type=float, default=0.0)
    ap.add_argument("--dca", type=int, default=2, help="DCA levels (1 = no DCA)")
    ap.add_argument("--hook", action="store_true", help="enter on RSI crossing back through the band")
    ap.add_argument("--wincool", type=int, default=0, help="bars to block entries after any win")
    ap.add_argument("--winstreak", type=int, default=0)
    ap.add_argument("--winstreakcool", type=int, default=0)
    ap.add_argument("--vol-cap", type=float, default=0.0,
                    help="skip entries when vol[i] > VOL_CAP × SMA(20, vol). e.g. 2.0, 2.5, 3.0")
    ap.add_argument("--multi-exit", type=int, default=0,
                    help="multi-signal true-breakout exit threshold (0=disabled, 3=recommended)")
    ap.add_argument("--multi-bbw-exp", type=float, default=0.50,
                    help="BBW expansion ratio for Sign 4 (default 0.50 = +50%)")
    # v2 filters
    ap.add_argument("--trend-stability", type=int, default=0,
                    help="require N consecutive 15m bars same trend before entry (0=off)")
    ap.add_argument("--l2-gate", action="store_true",
                    help="skip L2 DCA if vol-spike OR BBW-expansion OR price beyond opp BB")
    ap.add_argument("--bb-break-exit", action="store_true",
                    help="close trade if close beyond opposite 5m BB band")
    ap.add_argument("--trend-gap-min", type=float, default=0.0,
                    help="require |EMA20-EMA50|/EMA50 >= this fraction (e.g. 0.0015 = 0.15%)")
    ap.add_argument("--l2-peel", action="store_true",
                    help="USER IDEA: sell only the L2 leg at +l2-peel-tp, let L1 ride")
    ap.add_argument("--l2-peel-tp", type=float, default=0.0025,
                    help="L2 peel target above L2's own entry (default 0.0025 = 0.25%)")
    args = ap.parse_args()

    Cfg.RSI_PERIOD = args.rsi; Cfg.RSI_OVERSOLD = args.os; Cfg.RSI_OVERBOUGHT = args.ob
    Cfg.SL = args.sl; Cfg.LEVERAGE = args.lev; Cfg.TP_SINGLE = args.tps; Cfg.TP_DCA = args.tpd
    Cfg.BE_TRIG = args.be; Cfg.TRAIL = args.trail; Cfg.NO_TP = args.notp; Cfg.TRAIL_CLOSE = args.trailc
    Cfg.BREAKER_LOSSES = args.blosses; Cfg.BREAKER_PAUSE_BARS = args.bpause; Cfg.DD_STOP = args.ddstop
    Cfg.WIN_COOLDOWN = args.wincool; Cfg.WINSTREAK_N = args.winstreak; Cfg.WINSTREAK_COOL = args.winstreakcool
    Cfg.FLIP_ON_SL = args.flip; Cfg.DCA_LEVELS = args.dca; Cfg.HOOK = args.hook; Cfg.EMA200_EXIT = args.emaexit
    Cfg.VOL_CAP = args.vol_cap
    Cfg.MULTI_EXIT_THRESHOLD = args.multi_exit
    Cfg.MULTI_EXIT_BBW_EXP = args.multi_bbw_exp
    Cfg.TREND_STABILITY = args.trend_stability
    Cfg.L2_GATE = args.l2_gate
    Cfg.BB_BREAK_EXIT = args.bb_break_exit
    Cfg.TREND_GAP_MIN = args.trend_gap_min
    Cfg.L2_PEEL = args.l2_peel
    Cfg.L2_PEEL_TP = args.l2_peel_tp

    df = pd.read_csv(os.path.join(CACHE, "BTCUSDT_5m.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df[df["timestamp"] >= args.start].reset_index(drop=True)
    df["rsi"] = rsi_series(df["close"], args.rsi)
    df["ema50"] = ema(df["close"], 50)
    df["ema100"] = ema(df["close"], 100)
    df["ema200"] = ema(df["close"], 200)
    df["ema200_slope"] = df["ema200"].diff(12)  # slope over last hour
    mid = df["close"].rolling(20).mean()
    sd = df["close"].rolling(20).std()
    df["bb_low"] = mid - 2 * sd
    df["bb_up"] = mid + 2 * sd
    df["bbw"] = (4 * sd) / mid  # band width as % of price
    df["adx"] = adx(df, 14)
    df["vol_sma20"] = df["volume"].rolling(20).mean()
    tr = pd.concat([(df["high"]-df["low"]),
                    (df["high"]-df["close"].shift()).abs(),
                    (df["low"]-df["close"].shift()).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.ewm(alpha=1/14, adjust=False).mean() / df["close"]
    # 1h RSI resampled from 5m, forward-filled
    h1 = df.set_index("timestamp")["close"].resample("1h").last().dropna()
    r1 = rsi_series(h1, 14)
    df["rsi_1h"] = r1.reindex(df["timestamp"], method="ffill").values
    # 15m trend resampled from 5m: EMA20/50 on 15m, forward-fill to 5m
    d15 = df.set_index("timestamp")["close"].resample("15min").last().dropna()
    e20 = d15.ewm(span=20, adjust=False).mean()
    e50 = d15.ewm(span=50, adjust=False).mean()
    tup = (e20 > e50).reindex(df["timestamp"], method="ffill").values
    df["trend_up"] = tup
    # 2026-06-05: 15m EMA gap (signed) for trend-firmness filter
    gap_pct = ((e20 - e50) / e50).reindex(df["timestamp"], method="ffill").values
    df["trend_gap_pct"] = gap_pct
    # 2026-06-05: 15m BB(20, 2) for multi-signal exit's HTF confirm (Sign 2)
    bb15_mid = d15.rolling(20).mean()
    bb15_sd  = d15.rolling(20).std(ddof=0)
    df["bb_up15"]  = (bb15_mid + 2 * bb15_sd).reindex(df["timestamp"], method="ffill").values
    df["bb_low15"] = (bb15_mid - 2 * bb15_sd).reindex(df["timestamp"], method="ffill").values

    all_filters = ["none", "ema200", "ema100", "ema200_slope", "ema200_adx",
                   "ema200_atr", "ema200_htf", "ema200_bbw", "adx_lt"]
    filters = all_filters if args.filter == "all" else [args.filter]
    print(f"Data: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}  ({len(df):,} bars)")
    print(f"RSI{args.rsi} {args.os}/{args.ob} | 3x | DCA2@0.5% | TP .50/.25% | SL 1% | breaker on | ADX<{args.adx}")
    for f in filters:
        res = run(df, Cfg, filt=f, adx_thresh=args.adx)
        analyze(f, res)


if __name__ == "__main__":
    main()
