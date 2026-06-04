#!/usr/bin/env python3
"""bot_gemini.py — "Gemini" multi-regime 5m BTC bot.

Strategy (per the Gemini spec): classify the regime first, then trade it.
  Indicators: EMA200 (macro trend), EMA20 (local momentum), Bollinger(20,2),
              RSI7 (50 midline / 30 / 70).

  REGIME (on last closed 5m bar):
    BULL  : close > EMA200 AND EMA20 > EMA200 AND EMA20 sloping up
    BEAR  : close < EMA200 AND EMA20 < EMA200 AND EMA20 sloping down
    RANGE : EMA200 ~flat (|2h slope| small) — trade BB extremes
    SQUEEZE (BB width < threshold): NO mean-reversion — sit out, wait for breakout.

  TREND-FOLLOWING (bull/bear): only pullbacks WITH the trend.
    Long  (bull): price dipped to/below EMA20, RSI7 hooks up from <=50, green candle.
                  TP = upper Bollinger band. SL = below recent swing low (cap 0.6%).
    Short (bear): mirror — TP lower band, SL above swing high.

  MEAN-REVERSION (range only): fade the band extremes.
    Long : prior bar closed below lower band, RSI7 crossed back > 30, green candle.
           TP = basis (mid band). SL = fixed 0.4% below entry.
    Short: prior closed above upper band, RSI7 crossed back < 70. TP basis, SL 0.4%.

  3x leverage, risk ~0.5%/trade (R-sized), SL capped 0.6%, circuit breaker.
PAPER-ONLY. State/log in data/paper_gemini/ (override RSISCALP_DATA_DIR-style via GEMINI_DATA_DIR).
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
DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("GEMINI_DATA_DIR", "paper_gemini"))

# ── params ──
LEVERAGE = 3.0
RISK_PCT = 0.005          # 0.5% of balance risked per trade
SL_CAP = 0.006            # max 0.6% stop (risk matrix); wider swing stops are capped
RANGE_SL = 0.004          # fixed 0.4% stop for range mean-reversion
EMA_SLOPE_BARS = 24       # 2h slope window
FLAT_SLOPE = 0.0012       # |EMA200 2h slope| < 0.12% => "flat" => RANGE
SQUEEZE_BW = 0.008        # BB width / basis < 0.8% => squeeze => no reversion
PULLBACK_LOOKBACK = 4     # bars to look back for an EMA20 pullback touch
SWING_LOOKBACK = 6


def classify_regime(df):
    c = df["close"].iloc[-1]
    e200 = df["ema200"].iloc[-1]
    e20 = df["ema20"].iloc[-1]
    e20_prev = df["ema20"].iloc[-1 - EMA_SLOPE_BARS]
    e200_prev = df["ema200"].iloc[-1 - EMA_SLOPE_BARS]
    e20_up = e20 > e20_prev
    e200_slope = abs(e200 / e200_prev - 1)
    bw = (df["bb_up"].iloc[-1] - df["bb_low"].iloc[-1]) / df["bb_mid"].iloc[-1]
    if e200_slope < FLAT_SLOPE:
        return ("SQUEEZE" if bw < SQUEEZE_BW else "RANGE")
    if c > e200 and e20 > e200 and e20_up:
        return "BULL"
    if c < e200 and e20 < e200 and not e20_up:
        return "BEAR"
    return "RANGE" if bw >= SQUEEZE_BW else "SQUEEZE"


def main():
    log = make_logger("bot_gemini", os.path.join(DATA_DIR, "bot.log"))
    os.makedirs(DATA_DIR, exist_ok=True)
    log.info("=" * 60)
    book = PaperBook(DATA_DIR, "Gemini", log, leverage=LEVERAGE)

    df = fetch_klines(PAIR, "5m", 500, log)
    if df is None or len(df) < 220:
        log.error("insufficient 5m klines"); return
    live_px = fetch_live_price(PAIR, log)
    if live_px is None:
        log.error("no live price"); return

    df["ema20"] = ema(df["close"], 20)
    df["ema200"] = ema(df["close"], 200)
    df["rsi"] = rsi(df["close"], 7)
    df["bb_low"], df["bb_mid"], df["bb_up"] = bollinger(df["close"], 20, 2.0)

    closed = df.iloc[:-1]                  # drop the still-forming bar
    last = closed.iloc[-1]
    prev = closed.iloc[-2]
    close_px = float(last["close"])
    rsi_now = float(last["rsi"]); rsi_prev = float(prev["rsi"])
    regime = classify_regime(closed)

    dd = book.mark_equity()
    pos = book.position
    signal = None
    block_reason = None

    # ─────────────── manage open position ───────────────
    if pos:
        side = pos["side"]
        # hard stop
        hit_sl = (side == "LONG" and live_px <= pos["sl"]) or (side == "SHORT" and live_px >= pos["sl"])
        if hit_sl:
            book.close(pos["sl"], "SL")
        else:
            # single TP target (band/basis) — full exit
            tgt = pos["tp_targets"][0]
            hit_tp = (side == "LONG" and live_px >= tgt["px"]) or (side == "SHORT" and live_px <= tgt["px"])
            if hit_tp:
                book.close(tgt["px"], "TP")
            else:
                fav = ((live_px - pos["entry"]) / pos["entry"] * 100) * (1 if side == "LONG" else -1)
                log.info(f"  IN {side} ({pos['meta'].get('regime')}) entry ${pos['entry']:.2f} "
                         f"live ${live_px:.2f} fav {fav:+.2f}% SL ${pos['sl']:.2f} TP ${tgt['px']:.2f}")

    # ─────────────── entries (only when flat & not paused) ───────────────
    pos = book.position
    if pos is None:
        if book.paused():
            block_reason = "circuit breaker — paused after losses"
        elif regime == "SQUEEZE":
            block_reason = "BB squeeze — waiting for breakout (golden rule)"
        else:
            entry = sig_side = sl = tp = None
            reason = None
            pulled_to_ema = (closed["low"].iloc[-PULLBACK_LOOKBACK:] <= closed["ema20"].iloc[-PULLBACK_LOOKBACK:]).any()
            rallied_to_ema = (closed["high"].iloc[-PULLBACK_LOOKBACK:] >= closed["ema20"].iloc[-PULLBACK_LOOKBACK:]).any()
            green = last["close"] > last["open"]
            red = last["close"] < last["open"]

            if regime == "BULL":
                if pulled_to_ema and rsi_prev <= 50 and rsi_now > rsi_prev and green:
                    sig_side, entry, reason = "LONG", live_px, "bull-pullback"
                    sl = min(swing_low(closed, SWING_LOOKBACK), entry * (1 - 0.001))
                    tp = float(last["bb_up"])
            elif regime == "BEAR":
                if rallied_to_ema and rsi_prev >= 50 and rsi_now < rsi_prev and red:
                    sig_side, entry, reason = "SHORT", live_px, "bear-pullback"
                    sl = max(swing_high(closed, SWING_LOOKBACK), entry * (1 + 0.001))
                    tp = float(last["bb_low"])
            elif regime == "RANGE":
                # mean-reversion off the bands
                if prev["close"] < prev["bb_low"] and rsi_prev <= 30 and rsi_now > 30 and green:
                    sig_side, entry, reason = "LONG", live_px, "range-bottom"
                    sl = entry * (1 - RANGE_SL); tp = float(last["bb_mid"])
                elif prev["close"] > prev["bb_up"] and rsi_prev >= 70 and rsi_now < 70 and red:
                    sig_side, entry, reason = "SHORT", live_px, "range-top"
                    sl = entry * (1 + RANGE_SL); tp = float(last["bb_mid"])

            if sig_side:
                # cap the stop distance to SL_CAP
                if sig_side == "LONG":
                    sl = max(sl, entry * (1 - SL_CAP))
                else:
                    sl = min(sl, entry * (1 + SL_CAP))
                qty = book.qty_for_risk(entry, sl, RISK_PCT)
                signal = sig_side
                book.open(sig_side, entry, qty, sl, [{"px": tp, "frac": 1.0}],
                          {"regime": regime, "reason": reason})

    book.save()
    # ── live entry checks for the dashboard (current vs required, regime-aware) ──
    def _c(name, cur, ok):
        return {"name": name, "cur": cur, "ok": bool(ok)}
    _green = last["close"] > last["open"]; _red = last["close"] < last["open"]
    _pulled = bool((closed["low"].iloc[-PULLBACK_LOOKBACK:] <= closed["ema20"].iloc[-PULLBACK_LOOKBACK:]).any())
    _rallied = bool((closed["high"].iloc[-PULLBACK_LOOKBACK:] >= closed["ema20"].iloc[-PULLBACK_LOOKBACK:]).any())
    _rs = f"{rsi_prev:.0f}→{rsi_now:.0f}"
    if regime in ("BULL", "BEAR"):
        checks = {
            "LONG": [_c("Regime = BULL", regime, regime == "BULL"),
                     _c("Pullback to EMA20", "yes" if _pulled else "no", _pulled),
                     _c("RSI7 hook up from ≤50", _rs, rsi_prev <= 50 and rsi_now > rsi_prev),
                     _c("Green candle", "yes" if _green else "no", _green)],
            "SHORT": [_c("Regime = BEAR", regime, regime == "BEAR"),
                      _c("Rally to EMA20", "yes" if _rallied else "no", _rallied),
                      _c("RSI7 hook down from ≥50", _rs, rsi_prev >= 50 and rsi_now < rsi_prev),
                      _c("Red candle", "yes" if _red else "no", _red)],
        }
    elif regime == "RANGE":
        checks = {
            "LONG": [_c("Regime = RANGE", regime, True),
                     _c("Prev close < lower BB", f"{prev['close']:.0f} vs {prev['bb_low']:.0f}", prev["close"] < prev["bb_low"]),
                     _c("RSI7 cross up >30", _rs, rsi_prev <= 30 and rsi_now > 30),
                     _c("Green candle", "yes" if _green else "no", _green)],
            "SHORT": [_c("Regime = RANGE", regime, True),
                      _c("Prev close > upper BB", f"{prev['close']:.0f} vs {prev['bb_up']:.0f}", prev["close"] > prev["bb_up"]),
                      _c("RSI7 cross down <70", _rs, rsi_prev >= 70 and rsi_now < 70),
                      _c("Red candle", "yes" if _red else "no", _red)],
        }
    else:  # SQUEEZE
        checks = {"LONG": [_c("BB squeeze — stand aside", "wait for breakout", False)],
                  "SHORT": [_c("BB squeeze — stand aside", "wait for breakout", False)]}
    log.info(f"  {PAIR} ${close_px:,.2f} live ${live_px:,.2f} | RSI7 {rsi_now:.1f} | "
             f"regime {regime} | {book.stats_line().strip()}")
    book.write_status(
        PAIR, close_px, live_px, signal,
        {"rsi": rsi_now, "rsi_oversold": 30, "rsi_overbought": 70, "price": close_px,
         "ema20": float(last["ema20"]), "ema200": float(last["ema200"]),
         "bb_low": float(last["bb_low"]), "bb_mid": float(last["bb_mid"]), "bb_up": float(last["bb_up"])},
        regime,
        f"Gemini multi-regime (EMA200/EMA20/BB20·2/RSI7 — trend-pullback in BULL/BEAR, "
        f"BB mean-revert in RANGE, sit out SQUEEZE / 3x / risk {RISK_PCT*100:.1f}% / SL≤{SL_CAP*100:.1f}%) [PAPER]",
        block_reason, checks=checks)
    log.info("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.getLogger("bot_gemini").exception(f"FATAL: {e}")
        sys.exit(1)
