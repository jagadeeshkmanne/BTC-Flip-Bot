#!/usr/bin/env python3
"""bot_gemini_v2.py — Gemini multi-regime scalper v2.

Spec: BTC_5M_Scalping_Strategy_v2_Specification*.pdf (2026-06-04 revision)
Built as a SIBLING to v1 (bot_gemini.py). v1 is untouched; v2 runs in parallel.

CHANGES FROM v1 (the parts of Gemini's spec we kept after review):

  Layer 1 — Volatility Circuit Breakers:
    1.1 ATR Spike Lock: if (high − low) ≥ 3 × ATR(14), lockout new entries
        for 4 consecutive CLOSED 5m bars (20 min).
    1.2 BB BandWidth upper cap: BW% > 1.5% blocks entries (paired w/ a lower
        bound from v1 — v2 is trend-only so this just gives us a clean "no-trade"
        signal during expansion blow-offs).
    1.3 Daily Equity Halt: rolling 24h DD ≥ 3.0% (raised from Gemini's 2.0%
        because at true 3× notional, 2% triggers on a single ~0.7% adverse
        move — operationally unusable). Force-closes any open position and
        flags `halted=True`. Requires manual reset (clear the flag in state.json).

  Layer 2 — Institutional Entry Filters (TREND ONLY — mean-rev module dropped
  per [feedback_panic_strategy_rejected] memory):
    2.1 Strict 2-candle confirmation. Bar t-1 = boundary touch. Bar t =
        confirming directional reversal candle (green for long, red for short).
        Entry at the OPEN of bar t+1.
    2.2 Trend pullback:
        LONG : close > EMA200, t-1 low ≤ EMA20, RSI(7) crosses up through 50,
               bar t closes green.
        SHORT: close < EMA200, t-1 high ≥ EMA20, RSI(7) crosses down through 50,
               bar t closes red.

  Layer 3 — Risk-Insulated Trailing:
    3.1 Risk-Free Pivot: when fav% ≥ +0.35%, move SL to entry ± 0.10%
        (NOT the spec's 0.05% — Binance taker fees round-trip 0.08%, so 0.05%
        leaves you slightly negative on a scratch; 0.10% covers fees plus tiny
        cushion).
    3.2 Post-pivot trail: 0.1% offset from printing EMA20, ratchet-only.
        Updated once per new closed 5m bar.

  Sizing:
    True 3× NOTIONAL via book.qty_for_notional (= 0.95 × bal × 3 / entry).
    Initial SL: last-5-bar swing extreme ± 0.20% buffer, capped to
    [0.15%, 0.6%] from entry to avoid noise-stops and runaway losses.

  Dropped from Gemini's spec (after review):
    - RSI 30/70 mean-reversion entries (memory: rejected twice on BTC 5m)
    - "Lock behind first breakout candle extreme" mean-rev trail (too vague)
    - "Live API call" framing (this is a paper bot, mirrors v1)

PAPER-ONLY. State/log in data/paper_gemini_v2/ by default
(override via GEMINI_V2_DATA_DIR env var).
"""
from __future__ import annotations
import os, sys, logging
from datetime import datetime, timezone

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(os.path.dirname(STRATEGY_DIR))
sys.path.insert(0, STRATEGY_DIR)
from core_engine import (
    PaperBook, make_logger, fetch_klines, fetch_live_price,
    ema, rsi, atr, bollinger, swing_low, swing_high,
)

PAIR = "BTCUSDT"
DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("GEMINI_V2_DATA_DIR", "paper_gemini_v2"))

# ─── Params ───
LEVERAGE          = 3.0         # used by book.qty_for_notional → true 3× notional
ATR_LEN           = 14
ATR_SPIKE_MULT    = 3.0         # (H−L) ≥ 3×ATR triggers lockout
LOCKOUT_BARS      = 4           # 4 × 5m = 20 min entry blackout after spike
BBW_UPPER_CAP_PCT = 1.5         # BB BandWidth % > 1.5 → block entries
DAILY_HALT_DD     = 0.03        # 3% rolling-24h drawdown → halt
INITIAL_SL_BUFFER = 0.0020      # swing extreme ± 0.20%
SL_MIN_DIST_PCT   = 0.0015      # minimum 0.15% stop distance
SL_MAX_DIST_PCT   = 0.0060      # max 0.60% (caps worst-case trade loss at ~1.8% on 3× notional)
PIVOT_TRIGGER     = 0.0035      # +0.35% fav → arm BE pivot
BE_BUFFER         = 0.0010      # SL → entry ± 0.10% post-pivot (fee-aware)
TRAIL_OFFSET      = 0.0010      # EMA20 ± 0.1% trailing SL post-pivot
SWING_LOOKBACK    = 5


def _bw_pct(row):
    """BB BandWidth % — (upper − lower) / basis × 100."""
    return (row["bb_up"] - row["bb_low"]) / row["bb_mid"] * 100.0


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    log = make_logger("bot_gemini_v2", os.path.join(DATA_DIR, "bot.log"))
    log.info("=" * 60)
    book = PaperBook(DATA_DIR, "Gemini v2", log, leverage=LEVERAGE)

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

    closed   = df.iloc[:-1]                          # drop forming bar
    last_bar = closed.iloc[-1]
    prev_bar = closed.iloc[-2]
    close_px = float(last_bar["close"])
    last_bar_ts = str(last_bar["timestamp"])  # canonical bar-open time from kline

    state = book.state
    new_bar = state.get("last_processed_bar_ts") != last_bar_ts

    block_reason = None
    signal = None

    # ──────────────────── (1) New-bar maintenance ────────────────────
    if new_bar:
        bar_range = float(last_bar["high"] - last_bar["low"])
        atr_now   = float(last_bar["atr14"]) if last_bar["atr14"] == last_bar["atr14"] else 0.0
        # 1.1 ATR Spike Lock
        if atr_now > 0 and bar_range >= atr_now * ATR_SPIKE_MULT:
            state["lockout_remaining_bars"] = LOCKOUT_BARS
            log.warning(f"  ATR SPIKE: bar range ${bar_range:.0f} ≥ {ATR_SPIKE_MULT}×ATR "
                        f"${atr_now*ATR_SPIKE_MULT:.0f} — entry lockout {LOCKOUT_BARS} bars")
        elif state.get("lockout_remaining_bars", 0) > 0:
            state["lockout_remaining_bars"] -= 1
        # 2026-06-04 fix: removed pending_entry expiration block. Signal now
        # executes inline at detection time (see section 4). The old "queue +
        # execute on next-bar tick" pattern had a sequencing bug — the
        # new-bar maintenance below expired pending_entry before the execution
        # block could fire it. Net result: bot detected signals but never opened.
        state["last_processed_bar_ts"] = last_bar_ts

    # ──────────────────── (2) 24h rolling equity halt ────────────────────
    now_ts = datetime.now(timezone.utc).timestamp()
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
        log.error(f"  EQUITY HALT: {state['halt_reason']} — manual reset required")
        if book.position:
            book.close(live_px, "EQUITY_HALT")

    # ──────────────────── (3) Manage open position (every tick) ────────────────────
    pos = book.position
    if pos:
        side = pos["side"]
        # 3a. SL check
        sl_hit = (side == "LONG"  and live_px <= pos["sl"]) or \
                 (side == "SHORT" and live_px >= pos["sl"])
        if sl_hit:
            reason = "TRAIL" if pos.get("trail_active") else \
                     ("BE_PIVOT" if pos.get("pivot_achieved") else "SL")
            book.close(pos["sl"], reason)
        else:
            # 3b. Risk-Free Pivot at +0.35%
            fav = (live_px / pos["entry"] - 1) * (1 if side == "LONG" else -1)
            if not pos.get("pivot_achieved", False) and fav >= PIVOT_TRIGGER:
                be_sl = pos["entry"] * (1 + BE_BUFFER) if side == "LONG" \
                                                       else pos["entry"] * (1 - BE_BUFFER)
                book.update_sl(be_sl)
                pos["pivot_achieved"] = True
                log.warning(f"  PIVOT @ fav {fav*100:+.2f}% → SL → BE+{BE_BUFFER*100:.2f}% "
                            f"${be_sl:.2f}")
            # 3c. EMA20 trailing (only post-pivot, only on new bars)
            if new_bar and pos.get("pivot_achieved"):
                ema20_now = float(last_bar["ema20"])
                if side == "LONG":
                    trail_sl = ema20_now * (1 - TRAIL_OFFSET)
                else:
                    trail_sl = ema20_now * (1 + TRAIL_OFFSET)
                book.update_sl(trail_sl)
            # status logging
            log.info(f"  IN {side} entry ${pos['entry']:.2f} live ${live_px:.2f} "
                     f"fav {fav*100:+.2f}% SL ${pos['sl']:.2f}"
                     + (" [pivot]" if pos.get("pivot_achieved") else "")
                     + (" [trail]" if pos.get("trail_active") else ""))

    # ──────────────────── (4) Detect signal at close of new bar ────────────────────
    if new_bar and book.position is None and not state.get("halted") and not book.paused():
        if state.get("lockout_remaining_bars", 0) > 0:
            block_reason = f"ATR spike lockout — {state['lockout_remaining_bars']} bars left"
        else:
            # 2026-06-04 user-driven change: BBW cap REMOVED from the trend engine.
            # In a real 5m trend, BB expansion is expected (and often where the
            # best pullback entries live). The cap was inherited from Gemini's
            # mean-rev framing, which v2 doesn't run. The 4h ATR spike lock still
            # guards against genuine vertical flushes. If we ever add a sweep
            # engine, the BBW cap would gate THAT, not the trend pullback.
            if True:
                e200_last = float(last_bar["ema200"]); e200_prev = float(prev_bar["ema200"])
                above_e200 = close_px > e200_last and float(prev_bar["close"]) > e200_prev
                below_e200 = close_px < e200_last and float(prev_bar["close"]) < e200_prev
                prev_low_touched_e20  = float(prev_bar["low"])  <= float(prev_bar["ema20"])
                prev_high_touched_e20 = float(prev_bar["high"]) >= float(prev_bar["ema20"])
                rsi_prev = float(prev_bar["rsi"]); rsi_now = float(last_bar["rsi"])
                rsi_cross_up   = rsi_prev <= 50.0 and rsi_now > 50.0
                rsi_cross_down = rsi_prev >= 50.0 and rsi_now < 50.0
                green = float(last_bar["close"]) > float(last_bar["open"])
                red   = float(last_bar["close"]) < float(last_bar["open"])

                sig_side = None
                if above_e200 and prev_low_touched_e20  and rsi_cross_up   and green:
                    sig_side = "LONG"
                elif below_e200 and prev_high_touched_e20 and rsi_cross_down and red:
                    sig_side = "SHORT"
                if sig_side:
                    # 2026-06-04 fix: execute INLINE at signal detection. Old code
                    # queued pending_entry and waited for "next tick" but never
                    # actually fired (queued entry was expired by the new-bar
                    # maintenance block on the next-bar tick). live_px at this
                    # point is ~1 min into the t+1 bar — close enough to the
                    # spec's "open of t+1" intent for paper purposes.
                    entry = live_px
                    if sig_side == "LONG":
                        swing = float(closed.tail(SWING_LOOKBACK)["low"].min())
                        raw_sl = swing * (1 - INITIAL_SL_BUFFER)
                        sl = max(raw_sl, entry * (1 - SL_MAX_DIST_PCT))   # not more than 0.6%
                        sl = min(sl,     entry * (1 - SL_MIN_DIST_PCT))   # not less than 0.15%
                    else:
                        swing = float(closed.tail(SWING_LOOKBACK)["high"].max())
                        raw_sl = swing * (1 + INITIAL_SL_BUFFER)
                        sl = min(raw_sl, entry * (1 + SL_MAX_DIST_PCT))
                        sl = max(sl,     entry * (1 + SL_MIN_DIST_PCT))
                    qty = book.qty_for_notional(entry)  # TRUE 3× notional
                    if qty > 0:
                        book.open(sig_side, entry, qty, sl, [],
                                  {"regime": "TREND", "reason": "pullback_strict_2candle",
                                   "atr_at_entry": float(last_bar["atr14"]) if last_bar["atr14"] == last_bar["atr14"] else None})
                        signal = sig_side
                        log.warning(f"  SIGNAL {sig_side} executed @ ${entry:.2f} (closed bar {last_bar_ts})")

    book.save()

    # ──────────────────── (6) Dashboard status ────────────────────
    bw_pct_now = float(_bw_pct(last_bar))
    rsi_now    = float(last_bar["rsi"])
    e200_last  = float(last_bar["ema200"])

    def _c(name, cur, ok): return {"name": name, "cur": cur, "ok": bool(ok)}

    above_e200 = close_px > e200_last
    below_e200 = close_px < e200_last
    rsi_prev = float(prev_bar["rsi"])
    _pulled_long  = float(prev_bar["low"])  <= float(prev_bar["ema20"])
    _pulled_short = float(prev_bar["high"]) >= float(prev_bar["ema20"])
    _green = float(last_bar["close"]) > float(last_bar["open"])
    _red   = float(last_bar["close"]) < float(last_bar["open"])
    _rsi_str = f"{rsi_prev:.0f}→{rsi_now:.0f}"
    lock_left = state.get("lockout_remaining_bars", 0)
    bw_ok = bw_pct_now <= BBW_UPPER_CAP_PCT

    checks = {
        "LONG": [
            _c("Above 200 EMA (structural uptrend)", "yes" if above_e200 else "no", above_e200),
            _c("Prev bar pulled to EMA20 (touch)", "yes" if _pulled_long else "no", _pulled_long),
            _c("RSI(7) crossed UP through 50", _rsi_str, rsi_prev <= 50 and rsi_now > 50),
            _c("Green close (reversal confirm)", "yes" if _green else "no", _green),
            _c("No ATR spike lockout", f"{lock_left} bars left" if lock_left else "clear", lock_left == 0),
        ],
        "SHORT": [
            _c("Below 200 EMA (structural downtrend)", "yes" if below_e200 else "no", below_e200),
            _c("Prev bar rallied to EMA20 (touch)", "yes" if _pulled_short else "no", _pulled_short),
            _c("RSI(7) crossed DOWN through 50", _rsi_str, rsi_prev >= 50 and rsi_now < 50),
            _c("Red close (reversal confirm)", "yes" if _red else "no", _red),
            _c("No ATR spike lockout", f"{lock_left} bars left" if lock_left else "clear", lock_left == 0),
        ],
    }
    checks["side"] = "LONG" if above_e200 else ("SHORT" if below_e200 else "—")

    regime_str = ("TREND-UP" if above_e200 else ("TREND-DOWN" if below_e200 else "FLAT"))
    if state.get("halted"):
        block_reason = state.get("halt_reason", "HALTED — manual reset required")
    elif block_reason is None and pos is None and lock_left > 0:
        block_reason = f"ATR spike lockout — {lock_left} bars left"

    log.info(f"  {PAIR} ${close_px:,.2f} live ${live_px:,.2f} | RSI7 {rsi_now:.1f} | "
             f"regime {regime_str} | BW {bw_pct_now:.2f}% | DD24h {dd24*100:.2f}% | "
             f"{book.stats_line().strip()}")

    book.write_status(
        PAIR, close_px, live_px, signal,
        {"rsi": rsi_now, "rsi_oversold": 30, "rsi_overbought": 70, "price": close_px,
         "ema20": float(last_bar["ema20"]), "ema200": e200_last,
         "atr14": float(last_bar["atr14"]) if last_bar["atr14"] == last_bar["atr14"] else 0.0,
         "bb_low": float(last_bar["bb_low"]), "bb_mid": float(last_bar["bb_mid"]), "bb_up": float(last_bar["bb_up"]),
         "bb_width_pct": bw_pct_now, "dd_24h_pct": dd24 * 100,
         "lockout_bars_left": lock_left, "halted": state.get("halted", False)},
        regime_str,
        f"Gemini v2 trend-only (5m EMA200 regime + 2-candle RSI50 cross / 3× notional / "
        f"ATR spike lock {LOCKOUT_BARS}b (BBW cap REMOVED 2026-06-04 — trend expansion is welcome) / "
        f"BE pivot +{PIVOT_TRIGGER*100:.2f}% → entry±{BE_BUFFER*100:.2f}% / "
        f"EMA20±{TRAIL_OFFSET*100:.2f}% trail / 24h halt {DAILY_HALT_DD*100:.1f}%) [PAPER]",
        block_reason, checks=checks)
    log.info("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.getLogger("bot_gemini_v2").exception(f"FATAL: {e}")
        sys.exit(1)
