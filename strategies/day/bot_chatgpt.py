#!/usr/bin/env python3
"""bot_chatgpt.py — "ChatGPT" Regime-Switching EMA Pullback bot (5m BTC).

Strategy (per the ChatGPT spec): trend-pullback, trade ONLY strong trends.
  Indicators: EMA20, EMA50, EMA200, ADX(14), Volume SMA20.

  MARKET FILTER: ADX(14) > 25 (skip ranging markets entirely; hard-skip ADX < 20).

  LONG entry:  price > EMA200, EMA20 > EMA50, a pullback touched EMA20, the
               current candle closes ABOVE the pullback candle's high, and
               volume > Volume SMA20. Enter at candle close.
  SHORT entry: mirror (price < EMA200, EMA20 < EMA50, pullback to EMA20, close
               below pullback low, volume confirm).

  STOP: below recent swing low (long) / above swing high (short). Risk 0.5%/trade.
  TAKE-PROFIT: TP1 = 1R close 50%; TP2 = 2R close 25%; runner trails the EMA20
               (exit remainder when a 5m bar closes back through EMA20).

  3x leverage, circuit breaker after consecutive losers.
PAPER-ONLY. State/log in data/paper_chatgpt/ (override via CHATGPT_DATA_DIR).
"""
from __future__ import annotations
import os, sys, logging

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(os.path.dirname(STRATEGY_DIR))
sys.path.insert(0, STRATEGY_DIR)
from core_engine import (
    PaperBook, make_logger, fetch_klines, fetch_live_price,
    ema, adx, swing_low, swing_high,
)

PAIR = "BTCUSDT"
DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("CHATGPT_DATA_DIR", "paper_chatgpt"))

# ── params ──
LEVERAGE = 3.0
RISK_PCT = 0.005
ADX_MIN = 25.0            # only trade trends stronger than this
PULLBACK_LOOKBACK = 4     # bars in which the EMA20 pullback must have occurred
SWING_LOOKBACK = 6
SL_CAP = 0.008            # cap a far swing stop at 0.8%


def main():
    log = make_logger("bot_chatgpt", os.path.join(DATA_DIR, "bot.log"))
    os.makedirs(DATA_DIR, exist_ok=True)
    log.info("=" * 60)
    book = PaperBook(DATA_DIR, "ChatGPT", log, leverage=LEVERAGE)

    df = fetch_klines(PAIR, "5m", 500, log)
    if df is None or len(df) < 220:
        log.error("insufficient 5m klines"); return
    live_px = fetch_live_price(PAIR, log)
    if live_px is None:
        log.error("no live price"); return

    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["adx"] = adx(df, 14)
    df["vol_sma"] = df["volume"].rolling(20).mean()

    closed = df.iloc[:-1]
    last = closed.iloc[-1]
    prev = closed.iloc[-2]
    close_px = float(last["close"])
    adx_now = float(last["adx"])
    e20, e50, e200 = float(last["ema20"]), float(last["ema50"]), float(last["ema200"])

    dd = book.mark_equity()
    signal = None
    block_reason = None
    regime = ("UP" if e20 > e50 and close_px > e200 else
              "DOWN" if e20 < e50 and close_px < e200 else "MIXED")

    # ─────────────── manage open position ───────────────
    pos = book.position
    if pos:
        side = pos["side"]
        # 1) hard / trailing stop
        hit_sl = (side == "LONG" and live_px <= pos["sl"]) or (side == "SHORT" and live_px >= pos["sl"])
        if hit_sl:
            book.close(pos["sl"], "TRAIL" if pos.get("trail_active") else "SL")
        else:
            # 2) scaled take-profits (1R / 2R)
            for t in pos["tp_targets"]:
                if t["done"]:
                    continue
                reached = (side == "LONG" and live_px >= t["px"]) or (side == "SHORT" and live_px <= t["px"])
                if reached:
                    book.partial(t["px"], t["frac"], t.get("label", "TP"))
                    t["done"] = True
            pos = book.position
            # 3) runner: trail behind EMA20 once TP1 banked; exit on close through EMA20
            if pos and pos["tp_targets"][0]["done"]:
                if side == "LONG":
                    book.update_sl(max(pos["sl"], e20))
                    if close_px < e20:
                        book.close(live_px, "EMA20-TRAIL")
                else:
                    book.update_sl(min(pos["sl"], e20))
                    if close_px > e20:
                        book.close(live_px, "EMA20-TRAIL")
            pos = book.position
            if pos:
                fav = ((live_px - pos["entry"]) / pos["entry"] * 100) * (1 if side == "LONG" else -1)
                done = sum(1 for t in pos["tp_targets"] if t["done"])
                log.info(f"  IN {side} entry ${pos['entry']:.2f} live ${live_px:.2f} "
                         f"fav {fav:+.2f}% SL ${pos['sl']:.2f} TPs hit {done}/2")

    # ─────────────── entries ───────────────
    pos = book.position
    if pos is None:
        if book.paused():
            block_reason = "circuit breaker — paused after losses"
        elif adx_now < ADX_MIN:
            block_reason = f"ADX {adx_now:.0f} < {ADX_MIN:.0f} — ranging, no trend trade"
        else:
            vol_ok = float(last["volume"]) > float(last["vol_sma"])
            pulled = (closed["low"].iloc[-PULLBACK_LOOKBACK:] <= closed["ema20"].iloc[-PULLBACK_LOOKBACK:]).any()
            rallied = (closed["high"].iloc[-PULLBACK_LOOKBACK:] >= closed["ema20"].iloc[-PULLBACK_LOOKBACK:]).any()
            sig_side = entry = sl = None
            if (close_px > e200 and e20 > e50 and pulled and vol_ok
                    and last["close"] > prev["high"]):
                sig_side, entry = "LONG", live_px
                sl = min(swing_low(closed, SWING_LOOKBACK), entry * (1 - 0.001))
                sl = max(sl, entry * (1 - SL_CAP))
            elif (close_px < e200 and e20 < e50 and rallied and vol_ok
                    and last["close"] < prev["low"]):
                sig_side, entry = "SHORT", live_px
                sl = max(swing_high(closed, SWING_LOOKBACK), entry * (1 + 0.001))
                sl = min(sl, entry * (1 + SL_CAP))
            if sig_side:
                R = abs(entry - sl)
                if sig_side == "LONG":
                    tps = [{"px": entry + R, "frac": 0.5, "label": "TP1-1R"},
                           {"px": entry + 2 * R, "frac": 0.5, "label": "TP2-2R"}]
                else:
                    tps = [{"px": entry - R, "frac": 0.5, "label": "TP1-1R"},
                           {"px": entry - 2 * R, "frac": 0.5, "label": "TP2-2R"}]
                qty = book.qty_for_risk(entry, sl, RISK_PCT)
                signal = sig_side
                book.open(sig_side, entry, qty, sl, tps, {"regime": regime, "reason": "ema-pullback"})

    book.save()
    # ── live entry checks for the dashboard (current vs required) ──
    _pulled = bool((closed["low"].iloc[-PULLBACK_LOOKBACK:] <= closed["ema20"].iloc[-PULLBACK_LOOKBACK:]).any())
    _rallied = bool((closed["high"].iloc[-PULLBACK_LOOKBACK:] >= closed["ema20"].iloc[-PULLBACK_LOOKBACK:]).any())
    _vol, _volsma = float(last["volume"]), float(last["vol_sma"])
    _ph, _pl, _lc = float(prev["high"]), float(prev["low"]), float(last["close"])
    def _c(name, cur, ok):
        return {"name": name, "cur": cur, "ok": bool(ok)}
    checks = {
        "LONG": [
            _c(f"ADX > {ADX_MIN:.0f} (trend strong)", f"{adx_now:.1f}", adx_now >= ADX_MIN),
            _c("EMA20 > EMA50 (up)", f"{e20:.0f} vs {e50:.0f}", e20 > e50),
            _c("Price > EMA200", f"{close_px:.0f} vs {e200:.0f}", close_px > e200),
            _c("Pullback to EMA20", "yes" if _pulled else "no", _pulled),
            _c("Bar closes > prev high", f"{_lc:.0f} vs {_ph:.0f}", _lc > _ph),
            _c("Volume > SMA20", f"{_vol:.0f} vs {_volsma:.0f}", _vol > _volsma),
        ],
        "SHORT": [
            _c(f"ADX > {ADX_MIN:.0f} (trend strong)", f"{adx_now:.1f}", adx_now >= ADX_MIN),
            _c("EMA20 < EMA50 (down)", f"{e20:.0f} vs {e50:.0f}", e20 < e50),
            _c("Price < EMA200", f"{close_px:.0f} vs {e200:.0f}", close_px < e200),
            _c("Rally to EMA20", "yes" if _rallied else "no", _rallied),
            _c("Bar closes < prev low", f"{_lc:.0f} vs {_pl:.0f}", _lc < _pl),
            _c("Volume > SMA20", f"{_vol:.0f} vs {_volsma:.0f}", _vol > _volsma),
        ],
    }
    checks["side"] = ("LONG" if (close_px > e200 and e20 > e50)
                      else "SHORT" if (close_px < e200 and e20 < e50) else "")
    log.info(f"  {PAIR} ${close_px:,.2f} live ${live_px:,.2f} | ADX {adx_now:.0f} | "
             f"EMA20/50/200 {e20:.0f}/{e50:.0f}/{e200:.0f} | {book.stats_line().strip()}")
    book.write_status(
        PAIR, close_px, live_px, signal,
        {"rsi": None, "adx": adx_now, "price": close_px,
         "ema20": e20, "ema50": e50, "ema200": e200, "vol": float(last["volume"]), "vol_sma": float(last["vol_sma"])},
        regime,
        f"ChatGPT EMA-Pullback (EMA20/50/200 + ADX>{ADX_MIN:.0f} + vol>SMA20 / pullback-to-EMA20 / "
        f"1R·2R partials + EMA20 trail / 3x / risk {RISK_PCT*100:.1f}%) [PAPER]",
        block_reason, checks=checks)
    log.info("=" * 60 + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logging.getLogger("bot_chatgpt").exception(f"FATAL: {e}")
        sys.exit(1)
