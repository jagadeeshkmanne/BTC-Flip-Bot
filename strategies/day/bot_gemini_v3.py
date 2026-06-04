#!/usr/bin/env python3
"""bot_gemini_v3.py — Dual-engine 5m BTC scalper (Gemini v3 spec).

Spec source: gemini/v3/gemini-code-1780578165222.md
Sibling to v1 (bot_gemini.py) + v2 (bot_gemini_v2.py). Neither v1 nor v2 is
modified by this file. PAPER-ONLY (mirrors v1 / v2).

Architecture — Dynamic Volatility Switch (5m BBW):
  BBW% > 1.5%  →  Engine A: 2-Candle Trend Pullback (v2 logic, dropped BBW gate)
  BBW% ≤ 1.5%  →  Engine B: 3-Bar Horizontal Liquidity Sweep (new)

Engine A — Trend Pullback (TREND-UP / TREND-DOWN)
  LONG:  close > EMA200 (t-1 AND t), low[t-1] ≤ EMA20, RSI(7) crosses up
         through 50 (prev≤50 & now>50), close[t] > open[t] (green).
  SHORT: mirror with close < EMA200, high[t-1] ≥ EMA20, RSI cross down, red.
  Entry: live_px on next cron tick (≈ open of t+1).
  Initial SL: 5-bar swing extreme ± 0.20%, clamped to [0.15%, 0.60%].
  Profit protection:
    +0.35% fav  → SL → entry ± 0.10% (BE pivot, fee-shielded)
    Post-pivot → EMA20 ± 0.10% trail, ratchet-only, updated each new bar.
  No fixed TP — pure trail exit.

Engine B — Liquidity Sweep (3-bar trap pattern at horizontal levels)
  Levels: DYNAMIC prev_day H / mid / L (computed from yesterday's UTC 5m bars).
    Gemini's hardcoded values $64,051/$65,772/$67,494 are kept as a comment for
    reference but NOT used — they'd be stale in 24h.
  Pattern (SHORT sweep at level L — trap longs above):
    bar t-2 (Bait):     prior.high > L AND prior.close > L      (breakout)
    bar t-1 (Trap):     prev.high  > L AND prev.high > prior.high (DEEPER wick)
                        AND prev.close < L                       (rejected)
    bar t   (Trigger):  last.close < prev.low                     (confirms reversal)
  LONG sweep: mirror around the level.
  Entry: live_px on next cron tick.
  SL: 0.02% past trap-cluster extreme (max of [prior.high, prev.high] for SHORT).
  TP: fixed 1:2 R:R (entry − 2×risk for SHORT).
  SKIP if structural SL distance > 0.60% (too wide — would risk > 1.8% on a 3× notional stop).
  No trail — bracket runs to either SL or TP.

Guardrails (always active, both engines)
  - True 3× notional sizing: qty = balance × 0.95 × 3 / entry.
  - ATR(14) × 3 spike lock: bar with (H−L) ≥ 3×ATR triggers 4-bar entry blackout.
  - Rolling 24h equity DD halt at 3.0%: force-close any position + block entries.
    Auto-resumes 24h after the halt fires (no manual reset needed).
  - SL distance clamp [0.15%, 0.60%] caps single-trade loss at ~1.8% account DD.
"""
from __future__ import annotations
import os, sys, logging
from datetime import datetime, timezone, timedelta

import pandas as pd

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(os.path.dirname(STRATEGY_DIR))
sys.path.insert(0, STRATEGY_DIR)
from core_engine import (
    PaperBook, make_logger, fetch_klines, fetch_live_price,
    ema, rsi, atr, bollinger,
)

PAIR = "BTCUSDT"
DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("GEMINI_V3_DATA_DIR", "paper_gemini_v3"))

# ─── Shared params ───
LEVERAGE          = 3.0
ATR_LEN           = 14
ATR_SPIKE_MULT    = 3.0
LOCKOUT_BARS      = 4
DAILY_HALT_DD     = 0.03            # 3% rolling 24h
HALT_AUTO_RESUME_HOURS = 24         # auto-unhalt 24h after the halt fires
SL_MIN_DIST_PCT   = 0.0015
SL_MAX_DIST_PCT   = 0.0060

# ─── Engine A (Trend) ───
BBW_SWITCH_PCT    = 1.5             # > 1.5% → Engine A; ≤ → Engine B
INITIAL_SL_BUFFER = 0.0020
PIVOT_TRIGGER     = 0.0035
BE_BUFFER         = 0.0010
TRAIL_OFFSET      = 0.0010
SWING_LOOKBACK    = 5

# ─── Engine B (Sweep) ───
SWEEP_SL_BUFFER   = 0.0002          # 0.02% past trap-cluster extreme
SWEEP_RR          = 2.0             # 1:2 RR

# Gemini's hardcoded chart levels (2026-06-04 screenshot). KEPT AS COMMENT ONLY —
# we compute prev_day_high/low/mid dynamically each tick so the bot doesn't
# trade phantom levels in 24h.
# HARDCODED_LEVELS_REF = {"prev_L": 64051.0, "mid": 65772.0, "prev_H": 67494.0}


def _bw_pct(row):
    return (row["bb_up"] - row["bb_low"]) / row["bb_mid"] * 100.0


def compute_prev_day_levels(closed_df: pd.DataFrame) -> dict | None:
    """Aggregate prev UTC day's 5m bars → {high, low, mid}. None if not enough data."""
    ts = pd.to_datetime(closed_df["timestamp"])
    today = datetime.now(timezone.utc).date()
    prev_day = today - timedelta(days=1)
    mask = (ts.dt.date == prev_day)
    if not mask.any():
        return None
    prev = closed_df[mask]
    high = float(prev["high"].max())
    low  = float(prev["low"].min())
    return {"prev_day_high": high, "prev_day_low": low, "prev_day_mid": (high + low) / 2}


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    log = make_logger("bot_gemini_v3", os.path.join(DATA_DIR, "bot.log"))
    log.info("=" * 60)
    book = PaperBook(DATA_DIR, "Gemini v3", log, leverage=LEVERAGE)

    df = fetch_klines(PAIR, "5m", 500, log)
    if df is None or len(df) < 250:
        log.error("insufficient 5m klines"); return
    live_px = fetch_live_price(PAIR, log)
    if live_px is None:
        log.error("no live price"); return

    df["ema20"]  = ema(df["close"], 20)
    df["ema200"] = ema(df["close"], 200)
    df["rsi"]    = rsi(df["close"], 7)
    df["atr14"]  = atr(df, ATR_LEN)
    df["bb_low"], df["bb_mid"], df["bb_up"] = bollinger(df["close"], 20, 2.0)

    closed    = df.iloc[:-1]
    last_bar  = closed.iloc[-1]    # bar t (most recent closed)
    prev_bar  = closed.iloc[-2]    # bar t-1 (trap candidate in sweep terms)
    prior_bar = closed.iloc[-3]    # bar t-2 (bait candidate in sweep terms)
    close_px = float(last_bar["close"])
    last_bar_ts = str(last_bar["timestamp"])

    state = book.state
    new_bar = state.get("last_processed_bar_ts") != last_bar_ts

    block_reason = None
    signal = None

    # ──────────────── (1) New-bar maintenance ────────────────
    if new_bar:
        bar_range = float(last_bar["high"] - last_bar["low"])
        atr_now   = float(last_bar["atr14"]) if last_bar["atr14"] == last_bar["atr14"] else 0.0
        if atr_now > 0 and bar_range >= atr_now * ATR_SPIKE_MULT:
            state["lockout_remaining_bars"] = LOCKOUT_BARS
            log.warning(f"  ATR SPIKE: range ${bar_range:.0f} ≥ {ATR_SPIKE_MULT}×ATR "
                        f"${atr_now*ATR_SPIKE_MULT:.0f} → lockout {LOCKOUT_BARS} bars")
        elif state.get("lockout_remaining_bars", 0) > 0:
            state["lockout_remaining_bars"] -= 1
        # 2026-06-04 fix: removed pending_entry expiration block — see v2 for
        # the bug explanation. Signals now execute inline at detection time.
        state["last_processed_bar_ts"] = last_bar_ts

    # ──────────────── (2) 24h equity halt + auto-resume ────────────────
    now_ts = datetime.now(timezone.utc).timestamp()

    # Auto-resume: if halted ≥ HALT_AUTO_RESUME_HOURS ago, clear the flag
    if state.get("halted"):
        halted_at_str = state.get("halted_at")
        if halted_at_str:
            try:
                halted_at = datetime.fromisoformat(halted_at_str.replace("Z", "+00:00"))
                elapsed_h = (datetime.now(timezone.utc) - halted_at).total_seconds() / 3600.0
                if elapsed_h >= HALT_AUTO_RESUME_HOURS:
                    state["halted"] = False
                    state["halted_at"] = None
                    state["halt_reason"] = None
                    state["equity_history_24h"] = []    # reset peak window
                    log.warning(f"  HALT AUTO-LIFTED after {elapsed_h:.1f}h — peak window reset")
            except Exception as e:
                log.error(f"  halt auto-resume parse error: {e}")

    eq_hist = state.setdefault("equity_history_24h", [])
    pos = book.position
    unrealised = 0.0
    if pos:
        if pos["side"] == "LONG":
            unrealised = (live_px - pos["entry"]) * pos["qty"]
        else:
            unrealised = (pos["entry"] - live_px) * pos["qty"]
    mark_eq = book.balance + unrealised
    eq_hist.append([now_ts, round(mark_eq, 2)])
    cutoff = now_ts - 86400
    state["equity_history_24h"] = [e for e in eq_hist if e[0] >= cutoff]

    peak24 = max((e[1] for e in state["equity_history_24h"]), default=mark_eq)
    dd24   = (peak24 - mark_eq) / peak24 if peak24 > 0 else 0.0

    if dd24 >= DAILY_HALT_DD and not state.get("halted", False):
        state["halted"] = True
        state["halted_at"] = datetime.now(timezone.utc).isoformat()
        state["halt_reason"] = f"24h DD {dd24*100:.2f}% ≥ {DAILY_HALT_DD*100:.1f}%"
        log.error(f"  EQUITY HALT: {state['halt_reason']} — auto-resumes in {HALT_AUTO_RESUME_HOURS}h")
        if book.position:
            book.close(live_px, "EQUITY_HALT")

    # ──────────────── (3) Manage open position (every tick) ────────────────
    pos = book.position
    if pos:
        side = pos["side"]
        engine = (pos.get("meta") or {}).get("engine", "A")

        # SL check
        sl_hit = (side == "LONG"  and live_px <= pos["sl"]) or \
                 (side == "SHORT" and live_px >= pos["sl"])
        # TP check (only Engine B has a fixed TP)
        tp_targets = pos.get("tp_targets") or []
        tp_px = tp_targets[0].get("px") if tp_targets and not tp_targets[0].get("done") else None
        tp_hit = bool(tp_px) and ((side == "LONG" and live_px >= tp_px) or
                                  (side == "SHORT" and live_px <= tp_px))

        if sl_hit:
            reason = "TRAIL" if pos.get("trail_active") else \
                     ("BE_PIVOT" if pos.get("pivot_achieved") else "SL")
            book.close(pos["sl"], reason)
        elif tp_hit:
            book.close(tp_px, "TP")
        else:
            fav = (live_px / pos["entry"] - 1) * (1 if side == "LONG" else -1)
            # Engine A: BE pivot + EMA20 trail (no fixed TP)
            if engine == "A":
                if not pos.get("pivot_achieved", False) and fav >= PIVOT_TRIGGER:
                    be_sl = pos["entry"] * (1 + BE_BUFFER) if side == "LONG" \
                                                          else pos["entry"] * (1 - BE_BUFFER)
                    book.update_sl(be_sl)
                    pos["pivot_achieved"] = True
                    log.warning(f"  [A] PIVOT @ fav {fav*100:+.2f}% → SL → BE±{BE_BUFFER*100:.2f}% "
                                f"${be_sl:.2f}")
                if new_bar and pos.get("pivot_achieved"):
                    ema20_now = float(last_bar["ema20"])
                    trail_sl = ema20_now * (1 - TRAIL_OFFSET) if side == "LONG" \
                                                              else ema20_now * (1 + TRAIL_OFFSET)
                    book.update_sl(trail_sl)
            # Engine B: bracket runs to either SL or TP, no trail
            log.info(f"  IN {side} [{engine}] entry ${pos['entry']:.2f} live ${live_px:.2f} "
                     f"fav {fav*100:+.2f}% SL ${pos['sl']:.2f}"
                     + (f" TP ${tp_px:.2f}" if tp_px else "")
                     + (" [pivot]" if pos.get("pivot_achieved") else ""))

    # ──────────────── (4) Detect signal on new closed bar ────────────────
    bw_pct_now    = float(_bw_pct(last_bar))
    active_engine = "A" if bw_pct_now > BBW_SWITCH_PCT else "B"
    prev_day      = compute_prev_day_levels(closed)

    e200_last = float(last_bar["ema200"]); e200_prev_val = float(prev_bar["ema200"])
    above_e200 = close_px > e200_last and float(prev_bar["close"]) > e200_prev_val
    below_e200 = close_px < e200_last and float(prev_bar["close"]) < e200_prev_val

    if new_bar and book.position is None and not state.get("halted") and not book.paused():
        if state.get("lockout_remaining_bars", 0) > 0:
            block_reason = f"ATR spike lockout — {state['lockout_remaining_bars']} bars left"
        elif active_engine == "A":
            # ─── Engine A: Trend Pullback ───
            prev_low_touched_e20  = float(prev_bar["low"])  <= float(prev_bar["ema20"])
            prev_high_touched_e20 = float(prev_bar["high"]) >= float(prev_bar["ema20"])
            rsi_prev = float(prev_bar["rsi"]); rsi_now = float(last_bar["rsi"])
            rsi_cross_up   = rsi_prev <= 50.0 and rsi_now > 50.0
            rsi_cross_down = rsi_prev >= 50.0 and rsi_now < 50.0
            green = float(last_bar["close"]) > float(last_bar["open"])
            red   = float(last_bar["close"]) < float(last_bar["open"])

            # 2026-06-04 fix: inline execution (see v2 for bug detail).
            sig_side = None
            if above_e200 and prev_low_touched_e20 and rsi_cross_up and green:
                sig_side = "LONG"
            elif below_e200 and prev_high_touched_e20 and rsi_cross_down and red:
                sig_side = "SHORT"
            if sig_side:
                entry = live_px
                if sig_side == "LONG":
                    swing = float(closed.tail(SWING_LOOKBACK)["low"].min())
                    raw_sl = swing * (1 - INITIAL_SL_BUFFER)
                    sl = max(raw_sl, entry * (1 - SL_MAX_DIST_PCT))
                    sl = min(sl,    entry * (1 - SL_MIN_DIST_PCT))
                else:
                    swing = float(closed.tail(SWING_LOOKBACK)["high"].max())
                    raw_sl = swing * (1 + INITIAL_SL_BUFFER)
                    sl = min(raw_sl, entry * (1 + SL_MAX_DIST_PCT))
                    sl = max(sl,    entry * (1 + SL_MIN_DIST_PCT))
                qty = book.qty_for_notional(entry)
                if qty > 0:
                    book.open(sig_side, entry, qty, sl, [],
                              {"engine": "A", "regime": "TREND",
                               "reason": "pullback_strict_2candle"})
                    signal = sig_side
                    log.warning(f"  SIGNAL {sig_side} [A trend] executed @ ${entry:.2f}")
        else:
            # ─── Engine B: Liquidity Sweep ───
            if prev_day is None:
                block_reason = "Engine B — prev_day levels unavailable (insufficient history)"
            else:
                levels = {
                    "prev_day_low":  prev_day["prev_day_low"],
                    "prev_day_mid":  prev_day["prev_day_mid"],
                    "prev_day_high": prev_day["prev_day_high"],
                }
                p_high  = float(prev_bar["high"]);   p_low  = float(prev_bar["low"])
                p_close = float(prev_bar["close"])
                pri_high  = float(prior_bar["high"]); pri_low  = float(prior_bar["low"])
                pri_close = float(prior_bar["close"])
                l_close = float(last_bar["close"])

                # 2026-06-04 fix: inline execution for sweep entries too.
                for name, level in levels.items():
                    # SHORT sweep: trap longs above the level
                    b1_breakout      = pri_high > level and pri_close > level
                    b2_deeper_wick   = p_high   > level and p_high > pri_high
                    b2_failed_close  = p_close < level
                    b3_break_low     = l_close < p_low
                    if b1_breakout and b2_deeper_wick and b2_failed_close and b3_break_low:
                        entry = live_px
                        trap_ext = max(pri_high, p_high)
                        sl = trap_ext * (1 + SWEEP_SL_BUFFER)
                        risk = sl - entry
                        if risk <= 0:
                            log.warning(f"  [B {name}] skip — non-positive risk")
                        elif risk / entry > SL_MAX_DIST_PCT:
                            log.warning(f"  [B {name}] skip — cluster too wide ({risk/entry*100:.2f}%)")
                        else:
                            tp = entry - risk * SWEEP_RR
                            qty = book.qty_for_notional(entry)
                            if qty > 0:
                                book.open("SHORT", entry, qty, sl,
                                          [{"px": tp, "frac": 1.0}],
                                          {"engine": "B", "regime": "SWEEP",
                                           "reason": name, "level": level})
                                signal = "SHORT"
                                log.warning(f"  SIGNAL SHORT [B sweep @ {name}=${level:.0f}] executed @ ${entry:.2f}")
                        break

                    # LONG sweep: trap shorts below the level
                    b1_breakdown     = pri_low < level and pri_close < level
                    b2_deeper_wick_l = p_low   < level and p_low < pri_low
                    b2_failed_close_l = p_close > level
                    b3_break_high    = l_close > p_high
                    if b1_breakdown and b2_deeper_wick_l and b2_failed_close_l and b3_break_high:
                        entry = live_px
                        trap_ext = min(pri_low, p_low)
                        sl = trap_ext * (1 - SWEEP_SL_BUFFER)
                        risk = entry - sl
                        if risk <= 0:
                            log.warning(f"  [B {name}] skip — non-positive risk")
                        elif risk / entry > SL_MAX_DIST_PCT:
                            log.warning(f"  [B {name}] skip — cluster too wide ({risk/entry*100:.2f}%)")
                        else:
                            tp = entry + risk * SWEEP_RR
                            qty = book.qty_for_notional(entry)
                            if qty > 0:
                                book.open("LONG", entry, qty, sl,
                                          [{"px": tp, "frac": 1.0}],
                                          {"engine": "B", "regime": "SWEEP",
                                           "reason": name, "level": level})
                                signal = "LONG"
                                log.warning(f"  SIGNAL LONG [B sweep @ {name}=${level:.0f}] executed @ ${entry:.2f}")
                        break

    book.save()

    # ──────────────── (6) Dashboard status ────────────────
    rsi_prev_val = float(prev_bar["rsi"]); rsi_now_val = float(last_bar["rsi"])
    _pulled_long  = float(prev_bar["low"])  <= float(prev_bar["ema20"])
    _pulled_short = float(prev_bar["high"]) >= float(prev_bar["ema20"])
    _green = float(last_bar["close"]) > float(last_bar["open"])
    _red   = float(last_bar["close"]) < float(last_bar["open"])
    lock_left = state.get("lockout_remaining_bars", 0)
    rsi_str = f"{rsi_prev_val:.0f}→{rsi_now_val:.0f}"

    def _c(name, cur, ok): return {"name": name, "cur": cur, "ok": bool(ok)}

    if active_engine == "A":
        checks = {
            "LONG": [
                _c("Engine A active (BBW > 1.5%)", f"{bw_pct_now:.2f}%", bw_pct_now > BBW_SWITCH_PCT),
                _c("Above 5m EMA200", "yes" if above_e200 else "no", above_e200),
                _c("Prev bar pulled to EMA20", "yes" if _pulled_long else "no", _pulled_long),
                _c("RSI(7) crossed UP through 50", rsi_str, rsi_prev_val <= 50 and rsi_now_val > 50),
                _c("Green close (reversal)", "yes" if _green else "no", _green),
                _c("No ATR spike lockout", f"{lock_left}b left" if lock_left else "clear", lock_left == 0),
            ],
            "SHORT": [
                _c("Engine A active (BBW > 1.5%)", f"{bw_pct_now:.2f}%", bw_pct_now > BBW_SWITCH_PCT),
                _c("Below 5m EMA200", "yes" if below_e200 else "no", below_e200),
                _c("Prev bar rallied to EMA20", "yes" if _pulled_short else "no", _pulled_short),
                _c("RSI(7) crossed DOWN through 50", rsi_str, rsi_prev_val >= 50 and rsi_now_val < 50),
                _c("Red close (reversal)", "yes" if _red else "no", _red),
                _c("No ATR spike lockout", f"{lock_left}b left" if lock_left else "clear", lock_left == 0),
            ],
        }
    else:
        # Engine B checklist — actually compute pattern stage per level.
        lvl_str = "n/a"
        # For each level, compute: has Bar 1 (bait), Bar 2 (trap), Bar 3 (trigger)
        # condition been met? Show the closest-to-firing level's progress so the
        # user sees "we're 2/3 of the way" rather than a hardcoded "watching" ✓.
        best_long_stage  = 0   # 0=no bait, 1=bait, 2=bait+trap, 3=full setup
        best_short_stage = 0
        best_long_level  = best_short_level = ""
        p_high_ = float(prev_bar["high"]);   p_low_ = float(prev_bar["low"])
        p_close_ = float(prev_bar["close"])
        pri_high_ = float(prior_bar["high"]); pri_low_ = float(prior_bar["low"])
        pri_close_ = float(prior_bar["close"])
        l_close_ = float(last_bar["close"])
        if prev_day:
            lvl_str = (f"L${prev_day['prev_day_low']:.0f} / "
                       f"M${prev_day['prev_day_mid']:.0f} / "
                       f"H${prev_day['prev_day_high']:.0f}")
            for nm, lv in [("prev_day_low",  prev_day["prev_day_low"]),
                           ("prev_day_mid",  prev_day["prev_day_mid"]),
                           ("prev_day_high", prev_day["prev_day_high"])]:
                # SHORT sweep stages (trap above level)
                s_b1 = pri_high_ > lv and pri_close_ > lv
                s_b2 = p_high_ > lv and p_high_ > pri_high_ and p_close_ < lv
                s_b3 = l_close_ < p_low_
                s_stage = (3 if (s_b1 and s_b2 and s_b3) else
                           2 if (s_b1 and s_b2) else
                           1 if s_b1 else 0)
                if s_stage > best_short_stage:
                    best_short_stage = s_stage; best_short_level = nm
                # LONG sweep stages (trap below level)
                l_b1 = pri_low_ < lv and pri_close_ < lv
                l_b2 = p_low_  < lv and p_low_ < pri_low_ and p_close_ > lv
                l_b3 = l_close_ > p_high_
                l_stage = (3 if (l_b1 and l_b2 and l_b3) else
                           2 if (l_b1 and l_b2) else
                           1 if l_b1 else 0)
                if l_stage > best_long_stage:
                    best_long_stage = l_stage; best_long_level = nm

        def _stage_label(stage):
            return ("Bar 1 (bait) NOT detected" if stage == 0 else
                    "Bar 1 ✓ (bait detected, waiting on Bar 2)" if stage == 1 else
                    "Bar 1 ✓ Bar 2 ✓ (trap formed, waiting on trigger)" if stage == 2 else
                    "All 3 bars aligned — fires this tick")

        checks = {
            "LONG": [
                _c("Engine B active (BBW ≤ 1.5%)", f"{bw_pct_now:.2f}%", bw_pct_now <= BBW_SWITCH_PCT),
                _c("Prev-day levels (L/Mid/H)", lvl_str, prev_day is not None),
                _c(f"Bar 1 (bait): close past a level", _stage_label(best_long_stage) + (f" @ {best_long_level}" if best_long_level else ""), best_long_stage >= 1),
                _c(f"Bar 2 (trap): deeper wick + close back inside", _stage_label(best_long_stage), best_long_stage >= 2),
                _c(f"Bar 3 (trigger): close > Bar 2 high", "yes" if best_long_stage >= 3 else "no", best_long_stage >= 3),
                _c("No ATR spike lockout", f"{lock_left}b left" if lock_left else "clear", lock_left == 0),
            ],
            "SHORT": [
                _c("Engine B active (BBW ≤ 1.5%)", f"{bw_pct_now:.2f}%", bw_pct_now <= BBW_SWITCH_PCT),
                _c("Prev-day levels (L/Mid/H)", lvl_str, prev_day is not None),
                _c(f"Bar 1 (bait): close past a level", _stage_label(best_short_stage) + (f" @ {best_short_level}" if best_short_level else ""), best_short_stage >= 1),
                _c(f"Bar 2 (trap): deeper wick + close back inside", _stage_label(best_short_stage), best_short_stage >= 2),
                _c(f"Bar 3 (trigger): close < Bar 2 low", "yes" if best_short_stage >= 3 else "no", best_short_stage >= 3),
                _c("No ATR spike lockout", f"{lock_left}b left" if lock_left else "clear", lock_left == 0),
            ],
        }
    # `checks.side` tells the dashboard which side(s) are currently viable.
    # Engine A: regime is EMA200-locked → one side only.
    # Engine B: sweep can fire either direction (LONG @ prev_day_low traps,
    # SHORT @ prev_day_high traps), so both sides remain viable.
    if active_engine == "B":
        checks["side"] = "BOTH"
    elif above_e200:
        checks["side"] = "LONG"
    elif below_e200:
        checks["side"] = "SHORT"
    else:
        checks["side"] = "—"

    regime_str = ("ENGINE-A·UP"   if active_engine == "A" and above_e200 else
                  "ENGINE-A·DOWN" if active_engine == "A" and below_e200 else
                  "ENGINE-A·FLAT" if active_engine == "A" else
                  "ENGINE-B·SWEEP")

    if state.get("halted"):
        block_reason = state.get("halt_reason", "HALTED — auto-resumes in 24h")
    elif block_reason is None and pos is None and lock_left > 0:
        block_reason = f"ATR spike lockout — {lock_left} bars left"

    log.info(f"  {PAIR} ${close_px:,.2f} live ${live_px:,.2f} | RSI7 {rsi_now_val:.1f} | "
             f"BBW {bw_pct_now:.2f}% → {active_engine} | "
             f"regime {regime_str} | DD24h {dd24*100:.2f}% | "
             f"{book.stats_line().strip()}")

    pd_for_status = prev_day or {}
    book.write_status(
        PAIR, close_px, live_px, signal,
        {"rsi": rsi_now_val, "rsi_oversold": 30, "rsi_overbought": 70, "price": close_px,
         "ema20": float(last_bar["ema20"]), "ema200": e200_last,
         "atr14": float(last_bar["atr14"]) if last_bar["atr14"] == last_bar["atr14"] else 0.0,
         "bb_low": float(last_bar["bb_low"]), "bb_mid": float(last_bar["bb_mid"]), "bb_up": float(last_bar["bb_up"]),
         "bb_width_pct": bw_pct_now, "dd_24h_pct": dd24 * 100,
         "lockout_bars_left": lock_left, "halted": state.get("halted", False),
         "active_engine": active_engine,
         "prev_day_high": pd_for_status.get("prev_day_high"),
         "prev_day_mid":  pd_for_status.get("prev_day_mid"),
         "prev_day_low":  pd_for_status.get("prev_day_low")},
        regime_str,
        f"Gemini v3 dual-engine (BBW switch @ {BBW_SWITCH_PCT}% / 5m EMA200+RSI50 trend "
        f"pullback / 3-bar liquidity sweep @ DYNAMIC prev_day H/Mid/L, 1:{SWEEP_RR:.0f} RR / "
        f"3× notional / ATR spike lock {LOCKOUT_BARS}b / 24h halt {DAILY_HALT_DD*100:.1f}% "
        f"(auto-resume {HALT_AUTO_RESUME_HOURS}h)) [PAPER]",
        block_reason, checks=checks)
    log.info("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.getLogger("bot_gemini_v3").exception(f"FATAL: {e}")
        sys.exit(1)
