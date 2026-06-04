#!/usr/bin/env python3
"""bot_pullback.py — "Claude" MTF Trend-Pullback bot (5m BTC). My pick.

Thesis (from my own 5y backtest of this repo + the 2022 Shen/Urquhart/Wang
intraday-momentum paper): on 5m BTC, fading RSI extremes loses because BTC
trends; the only robust edge is trading WITH the trend, sparingly, with
volatility-adaptive risk and letting winners run. So this bot is deliberately
LOW-frequency and high-conviction — the opposite of the rsiscalp churn.

  Regime gate (must ALL hold to arm an entry):
    - 15m macro trend: 15m close vs 15m EMA200   (LONG only if above, SHORT below)
    - 5m alignment:    EMA50 vs EMA200            (same direction)
    - ADX(14) > 20     (avoid chop — where scalpers donate)

  Trigger — RSI7 as a PULLBACK timer (not a reversion signal):
    LONG : price pulled back to/below EMA20, RSI7 dipped < 45 then hooks up,
           and the current candle is a green momentum candle (closes > prev high).
    SHORT: mirror (pullback to EMA20, RSI7 > 55 hooking down, red momentum candle).

  Risk — volatility-adaptive:
    SL = 1.2 x ATR(14) from entry, clamped to [0.3%, 0.6%]. Risk 0.5%/trade.
    Take-profit: bank 50% at 1.5R, then TRAIL the runner by a chandelier
      (max(EMA20, highest_close - 2*ATR)); exit on a close through it.
    Circuit breaker after 2 consecutive losers.  3x leverage (ATR stop keeps
      true risk ~0.5%, far from the liquidation line).
PAPER-ONLY. State/log in data/paper_pullback/ (override via PULLBACK_DATA_DIR).
"""
from __future__ import annotations
import os, sys, logging

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(os.path.dirname(STRATEGY_DIR))
sys.path.insert(0, STRATEGY_DIR)
from core_engine import (
    PaperBook, make_logger, fetch_klines, fetch_live_price,
    ema, rsi, atr, adx,
)

PAIR = "BTCUSDT"
DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("PULLBACK_DATA_DIR", "paper_pullback"))

# ── params ──
LEVERAGE = 3.0
RISK_PCT = 0.005
ADX_MIN = 20.0
ATR_MULT = 1.2
SL_MIN, SL_MAX = 0.003, 0.006   # clamp the ATR stop into the 0.3%–0.6% risk band
PARTIAL_R = 1.5                 # bank half here
TRAIL_ATR = 2.0
PULLBACK_LOOKBACK = 4
RSI_LONG_DIP = 45
RSI_SHORT_POP = 55


def main():
    log = make_logger("bot_pullback", os.path.join(DATA_DIR, "bot.log"))
    os.makedirs(DATA_DIR, exist_ok=True)
    log.info("=" * 60)
    book = PaperBook(DATA_DIR, "Claude", log, leverage=LEVERAGE)

    df = fetch_klines(PAIR, "5m", 500, log)
    if df is None or len(df) < 220:
        log.error("insufficient 5m klines"); return
    df15 = fetch_klines(PAIR, "15m", 300, log)
    live_px = fetch_live_price(PAIR, log)
    if live_px is None or df15 is None or len(df15) < 210:
        log.error("missing live price or 15m data"); return

    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["rsi"] = rsi(df["close"], 7)
    df["atr"] = atr(df, 14)
    df["adx"] = adx(df, 14)
    e200_15 = ema(df15["close"], 200)

    closed = df.iloc[:-1]
    last = closed.iloc[-1]
    prev = closed.iloc[-2]
    close_px = float(last["close"])
    rsi_now, rsi_prev = float(last["rsi"]), float(prev["rsi"])
    e20, e50, e200 = float(last["ema20"]), float(last["ema50"]), float(last["ema200"])
    atr_now = float(last["atr"])
    adx_now = float(last["adx"])

    # macro 15m trend (use last CLOSED 15m bar)
    macro_up = float(df15["close"].iloc[-2]) > float(e200_15.iloc[-2])
    macro_dn = float(df15["close"].iloc[-2]) < float(e200_15.iloc[-2])
    regime = ("UP" if macro_up and e50 > e200 else
              "DOWN" if macro_dn and e50 < e200 else "MIXED")

    dd = book.mark_equity()
    signal = None
    block_reason = None

    # ─────────────── manage open position ───────────────
    pos = book.position
    if pos:
        side = pos["side"]
        hit_sl = (side == "LONG" and live_px <= pos["sl"]) or (side == "SHORT" and live_px >= pos["sl"])
        if hit_sl:
            book.close(pos["sl"], "TRAIL" if pos.get("trail_active") else "SL")
        else:
            # bank 50% at 1.5R
            t = pos["tp_targets"][0]
            if not t["done"]:
                reached = (side == "LONG" and live_px >= t["px"]) or (side == "SHORT" and live_px <= t["px"])
                if reached:
                    book.partial(t["px"], t["frac"], "TP-1.5R")
                    t["done"] = True
            pos = book.position
            # chandelier trail on the runner (after the partial)
            if pos and pos["tp_targets"][0]["done"]:
                pos["best"] = max(pos["best"], close_px) if side == "LONG" else min(pos["best"], close_px)
                if side == "LONG":
                    chand = max(e20, pos["best"] - TRAIL_ATR * atr_now)
                    book.update_sl(chand)
                    if close_px < chand:
                        book.close(live_px, "CHANDELIER")
                else:
                    chand = min(e20, pos["best"] + TRAIL_ATR * atr_now)
                    book.update_sl(chand)
                    if close_px > chand:
                        book.close(live_px, "CHANDELIER")
            pos = book.position
            if pos:
                fav = ((live_px - pos["entry"]) / pos["entry"] * 100) * (1 if side == "LONG" else -1)
                log.info(f"  IN {side} entry ${pos['entry']:.2f} live ${live_px:.2f} fav {fav:+.2f}% "
                         f"SL ${pos['sl']:.2f} partial={'Y' if pos['tp_targets'][0]['done'] else 'N'}")

    # ─────────────── entries ───────────────
    pos = book.position
    if pos is None:
        if book.paused():
            block_reason = "circuit breaker — paused after losses"
        elif adx_now < ADX_MIN:
            block_reason = f"ADX {adx_now:.0f} < {ADX_MIN:.0f} — chop, stand aside"
        elif regime == "MIXED":
            block_reason = "MTF trend not aligned (15m macro vs 5m EMA50/200)"
        else:
            pulled = (closed["low"].iloc[-PULLBACK_LOOKBACK:] <= closed["ema20"].iloc[-PULLBACK_LOOKBACK:]).any()
            rallied = (closed["high"].iloc[-PULLBACK_LOOKBACK:] >= closed["ema20"].iloc[-PULLBACK_LOOKBACK:]).any()
            green = last["close"] > last["open"] and last["close"] >= prev["high"]
            red = last["close"] < last["open"] and last["close"] <= prev["low"]
            sig_side = entry = sl = None
            if regime == "UP" and pulled and rsi_prev < RSI_LONG_DIP and rsi_now > rsi_prev and green:
                sig_side, entry = "LONG", live_px
                sl = entry - ATR_MULT * atr_now
                sl = min(max(sl, entry * (1 - SL_MAX)), entry * (1 - SL_MIN))
            elif regime == "DOWN" and rallied and rsi_prev > RSI_SHORT_POP and rsi_now < rsi_prev and red:
                sig_side, entry = "SHORT", live_px
                sl = entry + ATR_MULT * atr_now
                sl = max(min(sl, entry * (1 + SL_MAX)), entry * (1 + SL_MIN))
            if sig_side:
                R = abs(entry - sl)
                tp = entry + PARTIAL_R * R if sig_side == "LONG" else entry - PARTIAL_R * R
                qty = book.qty_for_risk(entry, sl, RISK_PCT)
                signal = sig_side
                book.open(sig_side, entry, qty, sl, [{"px": tp, "frac": 0.5}],
                          {"regime": regime, "reason": "mtf-pullback"})

    book.save()
    log.info(f"  {PAIR} ${close_px:,.2f} live ${live_px:,.2f} | RSI7 {rsi_now:.1f} ADX {adx_now:.0f} | "
             f"macro {'UP' if macro_up else 'DOWN' if macro_dn else '?'} 5m {regime} | {book.stats_line().strip()}")
    book.write_status(
        PAIR, close_px, live_px, signal,
        {"rsi": rsi_now, "rsi_oversold": 30, "rsi_overbought": 70, "price": close_px,
         "adx": adx_now, "atr": atr_now, "ema20": e20, "ema50": e50, "ema200": e200,
         "macro_15m": "UP" if macro_up else "DOWN" if macro_dn else "?"},
        regime,
        f"Claude MTF Trend-Pullback (15m EMA200 macro + 5m EMA50/200 + ADX>{ADX_MIN:.0f} / RSI7 pullback "
        f"trigger / ATR stop 0.3-0.6% / 1.5R partial + chandelier trail / 3x / risk {RISK_PCT*100:.1f}%) [PAPER]",
        block_reason)
    log.info("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.getLogger("bot_pullback").exception(f"FATAL: {e}")
        sys.exit(1)
