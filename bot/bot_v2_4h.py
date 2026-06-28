#!/usr/bin/env python3
"""bot_v2_4h.py — BTC V2 (4h) PAPER bot. The session's validated endpoint.

STRATEGY (full 2017-2026, 3 bears, ret/DD 3.34, DD -31%, green every year — best of ~50 honest
backtests + 15 verification agents). BTC only (does NOT generalize to alts), 1x base.
  LONG  : enter when 4h EMA50>200 AND prior-day EMA50>200 AND close > 9-month SMA (macro filter).
          stop = entry - 3.5*ATR(14) capped 12%; break-even at +1R (-> entry+1%);
          pyramid +100% of original notional at +2R (-> ~2x peak, stop to BE);
          PARABOLIC DE-RISK: take 50% off when close > 120% above the 20-week SMA.
          exit on stop or regime flip (4h or daily trend down).
  SHORT : enter when close down >10% from 40-day high AND prior-day daily MACD < signal.
          size by BEAR DEPTH: 0.50/1.00/1.00x equity at -10/-20/-30% drawdown from 180-day high.
          stop = entry + 6*ATR(14) capped 20%; break-even at +1R; TRAILING stop (low_since+3.5*ATR)
          once +1R — rides sustained bears, locks before counter-rallies; exit on stop or bear-gate clear.

HONEST PAPER ACCOUNTING (same rules as the other bots):
  - fills at LIVE price (+/- slippage); taker fee 0.055%/side on traded notional
  - entries/exits/pyramid/parabolic decided on CLOSED 4h bars (one pass per new bar)
  - stop is also checked every run against the live price (responsive exits)

Cron: every ~15 min (status freshness + stop checks); structural decisions on each new 4h bar.
State/log/status: data/btcv2/
"""
from __future__ import annotations
import os, sys, json, logging, time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

STRATEGY_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(STRATEGY_DIR)
sys.path.insert(0, STRATEGY_DIR)
from bybit_data import fetch_live_price
import requests

# ─── Parameters (FIXED — validated as a set; do NOT tune live) ───
SYMBOL = "BTCUSDT"
SHORT_SYMBOL = "ETHUSDT"        # short ETH on BTC's bear signal (validated: ret/DD 4.18 vs 4.05 short-BTC)
INITIAL_BALANCE = 5000.0
FEE_PCT = 0.00055
SLIP_PCT = 0.0005
BPD = 6                                   # 4h bars per day
ATR_MULT, SL_CAP = 3.5, 0.12              # long stop
BE_R, BE_BUF = 1.0, 0.01                  # break-even at +1R -> entry+1%
PYR_R, PYR_FRAC = 2.0, 1.0                # pyramid at +2R, +100% of original notional
MACRO_MO = 9                              # macro filter: close > SMA(9 months)
PARAB_MULT = 2.2                          # parabolic: close > 2.2*SMA(20wk) (=120% above)
DROP_PCT, DROP_LOOK = 0.10, 35            # short gate: down 10% from 35-day high (was 40 — backtest 2026-06-25, config C)
S_ATR, S_CAP = 6.0, 0.20                  # short stop (was 5.0/0.15 — wider stop = fewer whipsaw cover-outs)
STRAIL_K, STRAIL_ARM_R = 3.5, 1.0         # trailing short exit (config D): once short is +1R, trail stop at low_since+3.5*ATR (tighten-only) — rides sustained bears, locks before counter-rallies. ret/DD 4.90->5.61, DD -35->-33%, 2022 +48->+75%
LONG_LEV_MAX = 2.5                        # conviction leverage: long = 1x (weak) -> 2.5x (strong trend); short stays 1x
LOCK_FRAC, LOCK_R = 0.33, 6.0             # lock 33% of the long at +6R (banks leveraged gains -> cuts DD)
BYBIT = "https://api.bybit.com"

DATA_DIR = os.path.join(BOT_DIR, "data", os.environ.get("BTCV2_DATA_DIR", "btcv2"))
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
STATUS_FILE = os.path.join(DATA_DIR, "status.json")
LOG_FILE = os.path.join(DATA_DIR, "bot.log")

log = logging.getLogger("bot_v2_4h")
log.setLevel(logging.INFO); log.handlers.clear()
for h in (logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)):
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")); log.addHandler(h)


def ema(s, n): return s.ewm(span=n, adjust=False).mean()
def sma(s, n): return s.rolling(n).mean()


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(span=n, adjust=False).mean()


def adx(df, n=14):
    """Wilder ADX — trend-strength for conviction leverage."""
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff(); dn = -l.diff()
    plus = ((up > dn) & (up > 0)) * up
    minus = ((dn > up) & (dn > 0)) * dn
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_ = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * plus.ewm(alpha=1/n, adjust=False).mean() / atr_.replace(0, np.nan)
    mdi = 100 * minus.ewm(alpha=1/n, adjust=False).mean() / atr_.replace(0, np.nan)
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean().fillna(0)


def fetch_btc_4h(bars=2700, symbol=SYMBOL):
    """Paginate Bybit 4h klines backward to ~bars (warms the 9-month SMA + daily EMA200)."""
    rows, end_ms = [], None
    while len(rows) < bars:
        p = {"category": "linear", "symbol": symbol, "interval": "240", "limit": 1000}
        if end_ms is not None:
            p["end"] = end_ms
        try:
            b = requests.get(f"{BYBIT}/v5/market/kline", params=p, timeout=20).json()
        except Exception as e:
            log.error(f"4h kline fetch failed: {e}"); break
        batch = b.get("result", {}).get("list", [])
        if not batch:
            break
        rows.extend(batch); end_ms = min(int(x[0]) for x in batch) - 1; time.sleep(0.05)
    if not rows:
        return None
    rows = sorted({int(x[0]): x for x in rows}.values(), key=lambda x: int(x[0]))
    return pd.DataFrame({
        "timestamp": pd.to_datetime([int(x[0]) for x in rows], unit="ms"),
        "open": [float(x[1]) for x in rows], "high": [float(x[2]) for x in rows],
        "low": [float(x[3]) for x in rows], "close": [float(x[4]) for x in rows],
    }).reset_index(drop=True)


def compute_signals(df):
    """Return a dict of the latest CLOSED-bar decision values (matches the backtest's shifts)."""
    closed = df.iloc[:-1].copy()
    c = closed["close"]
    e50, e200 = ema(c, 50), ema(c, 200)
    a = atr(closed, 14)
    adx_s = adx(closed, 14)
    egap = (e50 - e200) / e200
    macro_sma = sma(c, MACRO_MO * 30 * BPD).shift(1)
    parab_sma = sma(c, 140 * BPD).shift(1)
    hh_drop = c.rolling(DROP_LOOK * BPD).max().shift(1)
    hh180 = c.rolling(180 * BPD).max().shift(1)
    # daily resample for daily trend + MACD (prior completed day)
    dd = closed.set_index("timestamp").resample("1D").agg({"close": "last"}).dropna()
    de50, de200 = ema(dd["close"], 50), ema(dd["close"], 200)
    dml = ema(dd["close"], 12) - ema(dd["close"], 26); dsig = ema(dml, 9)
    d_bull = (de50 > de200).shift(1)
    d_macd_bear = (dml < dsig).shift(1)
    last_day = closed["timestamp"].iloc[-1].normalize()
    # latest prior-day daily values applicable to the last closed 4h bar
    dser_bull = d_bull[d_bull.index <= last_day]
    dser_macd = d_macd_bear[d_macd_bear.index <= last_day]
    i = -1
    px = float(c.iloc[i])
    dd_from_high = px / float(hh180.iloc[i]) - 1 if pd.notna(hh180.iloc[i]) else 0.0
    # bear-depth sizing — config C (2026-06-25): more aggressive in moderate drawdowns (was 0.25/0.50/1.0)
    ssize = 1.0 if dd_from_high <= -0.30 else (1.0 if dd_from_high <= -0.20 else 0.50)
    conv = min(1.0, max(0.0, float(adx_s.iloc[i]) / 35.0)) * 0.5 + min(1.0, max(0.0, float(egap.iloc[i]) / 0.12)) * 0.5
    long_lev = round(1.0 + (LONG_LEV_MAX - 1.0) * conv, 2)
    # --- plain-PRICE trigger levels (so the dashboard shows "fires below $X" instead of raw MACD) ---
    de12, de26 = ema(dd["close"], 12), ema(dd["close"], 26)
    a12, a26 = 2.0 / 13.0, 2.0 / 27.0
    # next daily close where MACD crosses below signal: solve newMACD(P) = current signal
    macd_cross_px = (float(dsig.iloc[-1]) - (float(de12.iloc[-1]) * (1 - a12) - float(de26.iloc[-1]) * (1 - a26))) / (a12 - a26)
    drop_level = float(hh_drop.iloc[i]) * (1 - DROP_PCT) if pd.notna(hh_drop.iloc[i]) else None
    macro_level = float(macro_sma.iloc[i]) if pd.notna(macro_sma.iloc[i]) else None
    _drop_ok = (px / float(hh_drop.iloc[i]) - 1 < -DROP_PCT) if pd.notna(hh_drop.iloc[i]) else False
    _macd_bear = bool(dser_macd.iloc[-1]) if len(dser_macd) else False
    # short fires only when BOTH (down>10% from high) AND (daily MACD bear) hold → watch the not-yet-met level
    if _macd_bear and drop_level is not None:
        short_fires_below = drop_level
    elif _drop_ok:
        short_fires_below = macd_cross_px
    elif drop_level is not None:
        short_fires_below = min(drop_level, macd_cross_px)
    else:
        short_fires_below = macd_cross_px
    return {
        "bar_id": str(closed["timestamp"].iloc[i]),
        "close": px, "atr": float(a.iloc[i]),
        "adx": round(float(adx_s.iloc[i]), 1), "conviction": round(conv, 2), "long_lev": long_lev,
        "f_bull": bool(e50.iloc[i] > e200.iloc[i]),
        "d_bull": bool(dser_bull.iloc[-1]) if len(dser_bull) else False,
        "macro_ok": bool(px > macro_sma.iloc[i]) if pd.notna(macro_sma.iloc[i]) else False,
        "drop_ok": bool(px / hh_drop.iloc[i] - 1 < -DROP_PCT) if pd.notna(hh_drop.iloc[i]) else False,
        "d_macd_bear": bool(dser_macd.iloc[-1]) if len(dser_macd) else False,
        "parab": bool(px > PARAB_MULT * parab_sma.iloc[i]) if pd.notna(parab_sma.iloc[i]) else False,
        "ssize": ssize, "dd_from_high": dd_from_high,
        "e50": float(e50.iloc[i]), "e200": float(e200.iloc[i]),
        "d_ema50": round(float(de50.iloc[-1])), "d_ema200": round(float(de200.iloc[-1])),
        # plain-price triggers for the dashboard
        "short_fires_below": round(short_fires_below) if short_fires_below else None,
        "long_fires_above": round(macro_level) if macro_level else None,
        "macd_cross_px": round(macd_cross_px), "drop_level": round(drop_level) if drop_level else None,
    }


def blank_state():
    return {"balance": INITIAL_BALANCE, "peak_equity": INITIAL_BALANCE, "position": None,
            "last_bar": None, "armed_long": True, "armed_short": True,
            "stats": {"total": 0, "wins": 0, "losses": 0, "pnl": 0.0}, "trade_log": []}


def load_state():
    if not os.path.exists(STATE_FILE):
        return blank_state()
    with open(STATE_FILE) as f:
        st = json.load(f)
    for k, v in blank_state().items():
        st.setdefault(k, v)
    return st


def atomic_write(path, payload):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, default=str, indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def equity_of(st, px):
    pos = st.get("position")
    if not pos:
        return st["balance"]
    d = 1 if pos["side"] == "LONG" else -1
    return st["balance"] + d * (px - pos["entry"]) * pos["qty"]


def _close_position(st, fill, reason):
    pos = st["position"]; d = 1 if pos["side"] == "LONG" else -1
    pnl = d * (fill - pos["entry"]) * pos["qty"] - FEE_PCT * pos["qty"] * fill
    st["balance"] += pnl
    s = st["stats"]; s["total"] += 1; s["pnl"] += pnl; s["wins" if pnl > 0 else "losses"] += 1
    st.setdefault("trade_log", []).append({
        "side": pos["side"], "inst": pos.get("inst"), "entry": pos["entry"], "exit": fill,
        "qty": pos["qty"], "net_usd": pnl, "reason": reason,
        "entry_time": pos.get("entry_time"), "exit_time": datetime.now(timezone.utc).isoformat()})
    st["trade_log"] = st["trade_log"][-100:]
    log.warning(f"  CLOSE {pos['side']} {pos['qty']:.4f} @ ${fill:,.0f} net ${pnl:+,.2f} ({reason})")
    st["position"] = None


def _partial_close(st, frac, fill, reason):
    pos = st["position"]; q = pos["qty"] * frac; d = 1 if pos["side"] == "LONG" else -1
    pnl = d * (fill - pos["entry"]) * q - FEE_PCT * q * fill
    st["balance"] += pnl; pos["qty"] -= q
    s = st["stats"]; s["pnl"] += pnl
    st.setdefault("trade_log", []).append({
        "side": pos["side"] + "(50%)", "inst": pos.get("inst"), "entry": pos["entry"], "exit": fill,
        "qty": q, "net_usd": pnl, "reason": reason,
        "entry_time": pos.get("entry_time"), "exit_time": datetime.now(timezone.utc).isoformat()})
    st["trade_log"] = st["trade_log"][-100:]
    log.warning(f"  PARTIAL {reason} {q:.4f} @ ${fill:,.0f} net ${pnl:+,.2f}")


def main():
    st = load_state()
    df = fetch_btc_4h()
    if df is None or len(df) < MACRO_MO * 30 * BPD + 50:
        log.error("insufficient BTC 4h klines for warmup"); return
    sig = compute_signals(df)
    px = fetch_live_price(SYMBOL, log)
    if px is None:
        log.error("BTC live price unavailable"); return
    # ---- ETH short leg (short ETH on BTC's bear signal) ----
    edf = fetch_btc_4h(symbol=SHORT_SYMBOL)
    eth_px = fetch_live_price(SHORT_SYMBOL, log)
    eth_close = eth_atr = eth_low = None
    if edf is not None and len(edf) > 20:
        eclosed = edf.iloc[:-1]
        eth_close = float(eclosed["close"].iloc[-1]); eth_atr = float(atr(eclosed, 14).iloc[-1])
        eth_low = float(eclosed["low"].iloc[-1])
    bar_id = sig["bar_id"]
    bull = sig["f_bull"] and sig["d_bull"] and sig["macro_ok"]
    bear = sig["drop_ok"] and sig["d_macd_bear"]
    new_bar = st.get("last_bar") != bar_id

    def mark_price(p):
        """Position mark: BTC price for a LONG, ETH price for a SHORT."""
        if not p or p["side"] == "LONG":
            return px
        return eth_px if eth_px is not None else p["entry"]

    # ---- stop check EVERY run (responsive) ----
    pos = st.get("position")
    if pos:
        if pos["side"] == "LONG" and px <= pos["stop"]:
            _close_position(st, pos["stop"] * (1 - SLIP_PCT), "STOP")
        elif pos["side"] == "SHORT" and eth_px is not None and eth_px >= pos["stop"]:
            _close_position(st, pos["stop"] * (1 + SLIP_PCT), "STOP")

    # ---- structural logic once per new closed 4h bar ----
    if new_bar:
        st["last_bar"] = bar_id
        if not bull: st["armed_long"] = True
        if not bear: st["armed_short"] = True
        pos = st.get("position")
        if pos and not (pos["side"] == "SHORT" and eth_px is None):  # manage open position
            mp = mark_price(pos)                       # BTC px for long, ETH px for short
            d = 1 if pos["side"] == "LONG" else -1
            prof = d * (mp - pos["entry"]) / pos["R"]
            regime_out = (pos["side"] == "LONG" and not bull) or (pos["side"] == "SHORT" and not bear)
            if regime_out:
                _close_position(st, mp * (1 - SLIP_PCT * d), "REGIME")
            else:
                if prof >= BE_R:  # break-even
                    be = pos["entry"] * (1 + BE_BUF) if pos["side"] == "LONG" else pos["entry"] * (1 - BE_BUF)
                    pos["stop"] = max(pos["stop"], be) if pos["side"] == "LONG" else min(pos["stop"], be)
                if pos["side"] == "SHORT":  # trailing short exit (config D): ride sustained bears, lock before counter-rallies
                    if eth_low is not None:
                        pos["low_since"] = min(pos.get("low_since", eth_low), eth_low)
                    if prof >= STRAIL_ARM_R and eth_atr is not None and pos.get("low_since") is not None:
                        pos["stop"] = min(pos["stop"], pos["low_since"] + STRAIL_K * eth_atr)  # tighten only
                if pos["side"] == "LONG" and not pos.get("pyramided") and prof >= PYR_R:  # pyramid
                    addn = PYR_FRAC * pos["notional0"]; fill = px * (1 + SLIP_PCT)
                    dq = addn / fill; st["balance"] -= FEE_PCT * addn
                    pos["entry"] = (pos["qty"] * pos["entry"] + dq * fill) / (pos["qty"] + dq)
                    pos["qty"] += dq; pos["pyramided"] = True; pos["stop"] = max(pos["stop"], pos["entry0"])
                    log.warning(f"  PYRAMID +{dq:.4f} -> {pos['qty']:.4f}")
                if pos["side"] == "LONG" and not pos.get("lock_done") and prof >= LOCK_R:  # lock partial profit @+6R
                    _partial_close(st, LOCK_FRAC, px * (1 - SLIP_PCT), "LOCK")
                    pos["lock_done"] = True
                if pos["side"] == "LONG" and not pos.get("parab_done") and sig["parab"]:  # parabolic de-risk
                    _partial_close(st, 0.5, px * (1 - SLIP_PCT), "PARABOLIC")
                    pos["parab_done"] = True
        # ---- entries (flat) ----
        if not st.get("position"):
            eq = st["balance"]
            if bull and st.get("armed_long"):
                stop = max(sig["close"] - ATR_MULT * sig["atr"], sig["close"] * (1 - SL_CAP))
                if sig["close"] - stop > 0:
                    lev = sig.get("long_lev", 1.0)            # conviction leverage 1x..2.5x
                    fill = px * (1 + SLIP_PCT); notional = eq * lev; qty = notional / fill
                    st["balance"] -= FEE_PCT * notional
                    st["position"] = {"side": "LONG", "qty": qty, "entry": fill, "entry0": fill,
                                      "stop": stop, "R": fill - stop, "notional0": notional,
                                      "pyramided": False, "parab_done": False, "lock_done": False, "lev": lev,
                                      "entry_time": datetime.now(timezone.utc).isoformat()}
                    st["armed_long"] = False
                    log.warning(f"  OPEN LONG {qty:.4f} @ ${fill:,.0f} stop ${stop:,.0f} lev {lev}x conv {sig.get('conviction')}")
            elif bear and st.get("armed_short") and eth_px is not None and eth_close is not None:
                # short ETH (1x, bear-depth sized) on BTC's bear signal; stop from ETH's ATR
                stop = min(eth_close + S_ATR * eth_atr, eth_close * (1 + S_CAP))
                if stop - eth_close > 0:
                    fill = eth_px * (1 - SLIP_PCT); notional = sig["ssize"] * eq; qty = notional / fill
                    st["balance"] -= FEE_PCT * notional
                    st["position"] = {"side": "SHORT", "inst": SHORT_SYMBOL, "qty": qty, "entry": fill, "entry0": fill,
                                      "stop": stop, "R": stop - fill, "notional0": notional,
                                      "pyramided": False, "parab_done": False, "low_since": eth_close,
                                      "entry_time": datetime.now(timezone.utc).isoformat()}
                    st["armed_short"] = False
                    log.warning(f"  OPEN SHORT ETH {qty:.4f} @ ${fill:,.2f} stop ${stop:,.2f} (BTC depth {sig['dd_from_high']*100:.0f}% size {sig['ssize']})")

    pos = st.get("position")
    eq = equity_of(st, mark_price(pos))
    st["peak_equity"] = max(st.get("peak_equity", eq), eq)
    dd = (eq / st["peak_equity"] - 1) if st["peak_equity"] > 0 else 0.0
    signal = "LONG" if bull else ("SHORT" if bear else "FLAT")
    bt_data = None
    bt_path = os.path.join(DATA_DIR, "backtest.json")
    if os.path.exists(bt_path):
        try:
            with open(bt_path) as f: bt_data = json.load(f)
        except Exception:
            pass

    atomic_write(STATE_FILE, st)
    atomic_write(STATUS_FILE, {
        "env": os.environ.get("BTCV2_DATA_DIR", "btcv2"), "symbol": SYMBOL,
        "balance": eq, "peak_equity": st["peak_equity"], "drawdown_pct": dd,
        "signal": signal, "live_price": px, "short_symbol": SHORT_SYMBOL, "eth_price": eth_px,
        "v2gates": {"bull": bull, "bear": bear, "f_bull": sig["f_bull"], "d_bull": sig["d_bull"],
                    "macro_ok": sig["macro_ok"], "drop_ok": sig["drop_ok"], "d_macd_bear": sig["d_macd_bear"],
                    "parab": sig["parab"], "ssize": sig["ssize"], "dd_from_high": sig["dd_from_high"],
                    "short_fires_below": sig.get("short_fires_below"), "long_fires_above": sig.get("long_fires_above"),
                    "macd_cross_px": sig.get("macd_cross_px"), "drop_level": sig.get("drop_level"),
                    "d_ema50": sig.get("d_ema50"), "d_ema200": sig.get("d_ema200")},
        "indicators": {"close": sig["close"], "ema50": sig["e50"], "ema200": sig["e200"],
                       "atr": sig["atr"], "adx": sig.get("adx"), "conviction": sig.get("conviction"),
                       "long_lev": sig.get("long_lev"), "closed_bar": bar_id},
        "backtest": bt_data,
        "position": ({"side": pos["side"], "inst": pos.get("inst", SYMBOL), "qty": pos["qty"], "avg_entry": pos["entry"], "stop": pos["stop"],
                      "entry_time": pos.get("entry_time"),
                      "unrealized_usd": (1 if pos["side"] == "LONG" else -1) * (mark_price(pos) - pos["entry"]) * pos["qty"]}
                     if pos else None),
        "stats": st["stats"],
        "strategy": ("V2 — 4h. LONG BTC: macro-filtered MTF (EMA50/200 4h+daily + >9mo SMA) with "
                     "CONVICTION LEVERAGE (1x weak → 2.5x strong, by ADX+EMA-gap) + pyramid@2R + lock 33%@+6R "
                     "+ parabolic de-risk. SHORT ETH on BTC's bear signal (BTC down>10% from 35d high & BTC "
                     "daily MACD<sig), bear-depth sized 0.5/1.0/1.0, 1x, TRAILING exit (low+3.5*ATR after +1R). "
                     "Full 2017-2026: CAGR ~188%, DD -33%, ret/DD 5.61, green every year [PAPER]"),
        "paper_mode": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    log.info(f"bar {bar_id} close ${sig['close']:,.0f} | sig {signal} bull={bull} bear={bear} | "
             f"equity ${eq:,.2f} (peak ${st['peak_equity']:,.2f}, DD {dd*100:+.2f}%) | "
             f"trades {st['stats']['total']} realized ${st['stats']['pnl']:+,.2f}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log.exception(f"FATAL: {e}"); sys.exit(1)
