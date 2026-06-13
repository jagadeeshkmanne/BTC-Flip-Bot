"""strategy_v2.py — Jesse port of bot_rsiscalp_v3.py as deployed (2026-06-13).

Faithful to scripts/run_v2.1.sh / run_v2.2.sh env configs. Route = 5m
(decisions on closed 5m bars, like the bot); Jesse executes resting
stop/limit/TP orders on 1m candles between decisions (richer than our 5m
harness, close to the bot's 1-min polling cadence).

Known engine-semantics differences vs the live polling bot (both small):
  - Jesse fills stops/TPs AT the order price intra-bar; the bot market-exits
    on the next 1-min poll (slightly worse fills). Jesse is mildly OPTIMISTIC.
  - DCA here is a resting limit (fills at trigger); bot market-fills on cross
    (slightly better for the position). Mildly PESSIMISTIC. No slippage model.
  - Trail/peak tracking updates per 5m close (bot: per 1-min tick).
Equity-ratchet kill-switch + daily-loss cap are OFF in both deployed configs'
spirit for strategy measurement (DAILY=0 env; ratchet would just halt the sim).
"""
from __future__ import annotations
import numpy as np
from jesse.strategies import Strategy
import jesse.indicators as ta


class RsiScalpV2(Strategy):
    # ── config (overridden by subclasses; mirrors env vars) ──
    RSI_OS, RSI_OB = 30, 70
    COUNTER_TREND = False          # False = WITH-trend (v2.1), True = v2.2
    GAP_MIN_PCT = 0.15             # |15m EMA20-EMA50 gap| required, in %
    TP_SINGLE, TP_DCA = 0.005, 0.0025
    TRENDLINE_SL_PCT = 0.0        # v2.1: 0.0010
    SL_FROM_WORST = 0.006
    DCA_SPACING = 0.005
    ATR_MAX_PCT = 0.80
    BE_WAIT_BARS = 6              # 5m bars after L2 before BE/trail arms
    TIME_SL_BARS = 72             # hard 6h
    PAUSE_MS = 15 * 60 * 1000     # circuit breaker: 15min after every loss
    LEV = 5.0

    # ── helpers ──
    def _c15(self):
        c = self.get_candles(self.exchange, self.symbol, "15m")
        if len(c) and self.time < c[-1][0] + 900_000:   # last 15m bar forming
            c = c[:-1]
        return c

    def _trend(self):
        c = self._c15()
        if len(c) < 60:
            return None, None
        close = c[:, 2]
        ef = ta.ema(close, 20, sequential=False)
        es = ta.ema(close, 50, sequential=False)
        gap = (ef - es) / es * 100.0
        return ("UP" if ef > es else "DOWN"), gap

    def _ema50_15m(self):
        c = self._c15()
        return ta.ema(c[:, 2], 50, sequential=False) if len(c) >= 60 else None

    def _atr_pct(self):
        h, l, c = self.candles[:, 3], self.candles[:, 4], self.candles[:, 2]
        if len(c) < 16:
            return None
        pc = np.roll(c, 1)
        tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))[1:]
        atr = tr[-14:].mean()
        return atr / c[-1] * 100.0

    def _rsi(self):
        return ta.rsi(self.candles, 9, sequential=False)

    def _signal(self):
        r = self._rsi()
        if np.isnan(r):
            return None
        if r <= self.RSI_OS:
            return "LONG"
        if r >= self.RSI_OB:
            return "SHORT"
        return None

    def _entry_ok(self, side: str) -> bool:
        if self.vars.get("pause_until", 0) > self.time:      # circuit breaker
            return False
        trend, gap = self._trend()
        if trend is None or gap is None:                     # defensive: fail closed
            return False
        if not self.COUNTER_TREND:                           # WITH-trend gate
            if (side == "LONG") != (trend == "UP"):
                return False
        if abs(gap) < self.GAP_MIN_PCT:                      # knife-edge filter
            return False
        a = self._atr_pct()
        if a is None or a > self.ATR_MAX_PCT:                # chop filter
            return False
        self.vars["entry_trend"] = trend
        return True

    # ── entries ──
    def should_long(self) -> bool:
        return self._signal() == "LONG" and self._entry_ok("LONG")

    def should_short(self) -> bool:
        return self._signal() == "SHORT" and self._entry_ok("SHORT")

    def _leg_qty(self) -> float:
        return round(self.balance * 0.95 * self.LEV / self.price / 2.0, 3)

    def go_long(self):
        q = self._leg_qty()
        dca = self.price * (1 - self.DCA_SPACING)
        self.buy = [(q, self.price), (q, dca)]               # L1 market, L2 resting

    def go_short(self):
        q = self._leg_qty()
        dca = self.price * (1 + self.DCA_SPACING)
        self.sell = [(q, self.price), (q, dca)]

    # ── lifecycle ──
    def on_open_position(self, order) -> None:
        v = self.vars
        v["entry_index"] = self.index
        v["l2_index"] = None
        v["l2_peak_fav"] = 0.0
        v["bal_at_open"] = self.balance
        e = self.position.entry_price
        long = self.position.type == "long"
        # trend-line stop (frozen at entry) — v2.1 only
        v["tl"] = None
        if self.TRENDLINE_SL_PCT > 0:
            line = self._ema50_15m()
            if line:
                tl = line * (1 - self.TRENDLINE_SL_PCT) if long else line * (1 + self.TRENDLINE_SL_PCT)
                if (long and tl >= e * 0.9995) or (not long and tl <= e * 1.0005):
                    tl = e * (1 - 2 * self.TRENDLINE_SL_PCT) if long else e * (1 + 2 * self.TRENDLINE_SL_PCT)
                v["tl"] = tl
        self._set_exits()

    def on_increased_position(self, order) -> None:
        self.vars["l2_index"] = self.index
        self.vars["l2_peak_fav"] = 0.0
        self._set_exits()

    def update_position(self) -> None:
        long = self.position.type == "long"
        e = self.position.entry_price
        fav = (self.close / e - 1) * 100 * (1 if long else -1)
        if self.vars.get("l2_index") is not None:
            self.vars["l2_peak_fav"] = max(self.vars.get("l2_peak_fav", 0.0), fav)
        # hard time-SL (6h)
        if self.index - self.vars.get("entry_index", self.index) >= self.TIME_SL_BARS:
            self.liquidate()
            return
        # trend-flip exit, PROFIT-ONLY, vs entry snapshot
        trend, _ = self._trend()
        et = self.vars.get("entry_trend")
        if trend and et and trend != et and self.position.pnl > 0:
            self.liquidate()
            return
        self._set_exits()

    def _set_exits(self) -> None:
        pos = self.position
        if pos.qty == 0:
            return
        long = pos.type == "long"
        sgn = 1 if long else -1
        e = pos.entry_price
        q = abs(pos.qty)
        dca_filled = self.vars.get("l2_index") is not None
        # TP from avg — adaptive by fill count
        tp_pct = self.TP_DCA if dca_filled else self.TP_SINGLE
        self.take_profit = q, e * (1 + sgn * tp_pct)
        # SL ladder
        if not dca_filled:
            worst = e                                        # single leg: worst == entry
            slp = worst * (1 - sgn * self.SL_FROM_WORST)
        else:
            waited = self.index - self.vars["l2_index"] >= self.BE_WAIT_BARS
            if not waited:
                slp = None                                   # BE-wait window: no price stop
            else:
                peak = self.vars.get("l2_peak_fav", 0.0)
                if peak >= 0.05:                             # L2 trail armed
                    trail = peak - 0.025
                    slp = e * (1 + sgn * trail / 100.0)
                else:
                    slp = e                                  # break-even
        tl = self.vars.get("tl")
        if tl is not None:
            slp = tl if slp is None else (max(slp, tl) if long else min(slp, tl))
        if slp is not None:
            # stop must be on the losing side of current price; if already
            # breached at this 5m close, exit at market (bot market-exits too)
            if (long and self.close <= slp) or (not long and self.close >= slp):
                self.liquidate()
                return
            self.stop_loss = q, slp

    def on_close_position(self, order, closed_trade=None) -> None:
        pnl = (closed_trade.pnl if closed_trade is not None
               else self.balance - self.vars.get("bal_at_open", self.balance))
        if pnl < 0:                                          # 15-min cooldown after every loss
            self.vars["pause_until"] = self.time + self.PAUSE_MS

    def should_cancel_entry(self) -> bool:
        return True


class V21(RsiScalpV2):
    """WITH-trend, RSI 30/70, gap 0.15%, TP 0.5/0.25%, trend-line stop 0.10%."""
    RSI_OS, RSI_OB = 30, 70
    COUNTER_TREND = False
    GAP_MIN_PCT = 0.15
    TP_SINGLE, TP_DCA = 0.005, 0.0025
    TRENDLINE_SL_PCT = 0.0010


class V22(RsiScalpV2):
    """Counter-trend, RSI 35/65, gap 0.30%, TP 0.5/1.0%, no trend-line stop."""
    RSI_OS, RSI_OB = 35, 65
    COUNTER_TREND = True
    GAP_MIN_PCT = 0.30
    TP_SINGLE, TP_DCA = 0.005, 0.01
    TRENDLINE_SL_PCT = 0.0
