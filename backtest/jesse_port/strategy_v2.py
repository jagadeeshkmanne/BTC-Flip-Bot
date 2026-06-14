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
    # ── regime gate (the v2.3 test): only enter when HTF ADX is in range/trend ──
    REGIME_ADX_MAX = None         # if set, require <REGIME_TF> ADX < this (confirmed RANGE)
    REGIME_TF = "1h"
    GAP_OFF = False               # True = drop the 15m gap-firmness filter entirely

    # ── helpers ──
    def _c15(self):
        c = self.get_candles(self.exchange, self.symbol, "15m")
        if len(c) and self.time < c[-1][0] + 900_000:   # last 15m bar forming
            c = c[:-1]
        return c

    def _adx_htf(self):
        """ADX14 on the last CLOSED REGIME_TF bar (no lookahead)."""
        tf = self.REGIME_TF
        ms = 3_600_000 if tf == "1h" else (900_000 if tf == "15m" else 14_400_000)
        c = self.get_candles(self.exchange, self.symbol, tf)
        if len(c) and self.time < c[-1][0] + ms:        # drop the forming HTF bar
            c = c[:-1]
        if len(c) < 30:
            return None
        return float(ta.adx(c, period=14, sequential=False))

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
        if self.REGIME_ADX_MAX is not None:                  # v2.3: confirmed-RANGE gate
            adx = self._adx_htf()
            if adx is None or adx >= self.REGIME_ADX_MAX:
                return False
        if not self.GAP_OFF and abs(gap) < self.GAP_MIN_PCT:  # knife-edge filter
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
        # TP from avg — adaptive by fill count; v2.3 stamps leg-specific TP in vars
        _ts, _td = self.vars.get("tp_single"), self.vars.get("tp_dca")
        tp_single = _ts if _ts is not None else self.TP_SINGLE
        tp_dca = _td if _td is not None else self.TP_DCA
        tp_pct = tp_dca if dca_filled else tp_single
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
    """Counter-trend, RSI 35/65, gap 0.20% (as deployed 2026-06-14), TP 0.5/1.0%."""
    RSI_OS, RSI_OB = 35, 65
    COUNTER_TREND = True
    GAP_MIN_PCT = 0.20
    TP_SINGLE, TP_DCA = 0.005, 0.01
    TRENDLINE_SL_PCT = 0.0


# ── v2.3 "what about range?" test: gate counter-trend to confirmed 1h ranges ──
class V22_R20(V22):
    """Range-gated: 1h ADX < 20, gap firmness filter REMOVED (range gate replaces it)."""
    REGIME_ADX_MAX = 20.0
    GAP_OFF = True


class V22_R25(V22):
    """Looser range gate: 1h ADX < 25 (more trades), gap filter removed."""
    REGIME_ADX_MAX = 25.0
    GAP_OFF = True


class V22_R20_GAP(V22):
    """Both filters: 1h ADX < 20 AND 15m gap >= 0.20%."""
    REGIME_ADX_MAX = 20.0
    GAP_OFF = False


class V22_R20_3070(V22):
    """Range gate ADX<20 + RSI 30/70 (fewer, deeper extremes)."""
    RSI_OS, RSI_OB = 30, 70
    REGIME_ADX_MAX = 20.0
    GAP_OFF = True


# ══ v2.3: the combined REGIME ROUTER (v2.1 leg in trends + v2.2 leg in ranges) ══
class V23(RsiScalpV2):
    """Dynamic switch by ADX regime, evaluated each closed bar:
        ADX >= TREND_ADX -> TREND leg  (with-trend, RSI TREND_RSI, gap TREND_GAP, TP 0.5/0.25)
        ADX <  RANGE_ADX -> RANGE leg  (counter-trend, RSI RANGE_RSI, TP 0.5/1.0)
        in between        -> FLAT (no entry)
       DUAL_TF=True requires 15m AND 1h ADX to AGREE on the regime (stricter).
       RANGE_ON=False disables the counter-trend leg (trend-only router)."""
    TREND_ADX = 25.0
    RANGE_ADX = 20.0
    TREND_RSI = (30, 70)
    RANGE_RSI = (35, 65)
    TREND_GAP = 0.15
    RANGE_ON = True
    DUAL_TF = False               # require 15m+1h agreement

    def _regime(self):
        """'trend' / 'range' / None on the last closed HTF bar(s)."""
        a1 = self._adx_htf()      # REGIME_TF (default 1h)
        if a1 is None:
            return None
        def lab(a):
            if a >= self.TREND_ADX:
                return "trend"
            if a < self.RANGE_ADX:
                return "range"
            return None
        r = lab(a1)
        if r is None:
            return None
        if self.DUAL_TF:
            c = self._c15()       # 15m ADX must agree
            if len(c) < 30:
                return None
            a15 = float(ta.adx(c, period=14, sequential=False))
            if lab(a15) != r:
                return None
        if r == "range" and not self.RANGE_ON:
            return None
        return r

    def _signal(self):
        leg = self._regime()
        if leg is None:
            return None
        os_, ob = self.TREND_RSI if leg == "trend" else self.RANGE_RSI
        r = ta.rsi(self.candles, 9, sequential=False)
        if np.isnan(r):
            return None
        self.vars["_leg"] = leg
        if r <= os_:
            return "LONG"
        if r >= ob:
            return "SHORT"
        return None

    def _entry_ok(self, side: str) -> bool:
        if self.vars.get("pause_until", 0) > self.time:
            return False
        leg = self.vars.get("_leg") or self._regime()
        if leg is None:
            return False
        trend, gap = self._trend()
        if trend is None or gap is None:
            return False
        if leg == "trend":                                   # with-trend gate + firmness
            if (side == "LONG") != (trend == "UP"):
                return False
            if abs(gap) < self.TREND_GAP:
                return False
            self.vars["tp_single"], self.vars["tp_dca"] = 0.005, 0.0025
        else:                                                # range = counter-trend, wider DCA TP
            self.vars["tp_single"], self.vars["tp_dca"] = 0.005, 0.01
        a = self._atr_pct()
        if a is None or a > self.ATR_MAX_PCT:
            return False
        self.vars["entry_trend"] = trend
        return True


class V23_NoDeadzone(V23):
    """No flat zone: every bar is trend OR range (split at 20)."""
    TREND_ADX = 20.0
    RANGE_ADX = 20.0


class V23_Dual(V23):
    """Require 15m AND 1h ADX to agree on the regime."""
    DUAL_TF = True


class V23_TrendOnly(V23):
    """Router with the counter-trend (range) leg DISABLED — trend leg only."""
    RANGE_ON = False


class V23_Wide(V23):
    """Wider dead zone: trend>28, range<18 (only act on clear regimes)."""
    TREND_ADX = 28.0
    RANGE_ADX = 18.0
