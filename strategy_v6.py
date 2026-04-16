"""
strategy_v6.py — MTF Candlestick Flip Bot + SL-Flip extension  ◀ LIVE VERSION

⭐ THIS IS THE CURRENT LIVE STRATEGY (deployed on GCP testnet as of 2026-04-16).
   bot.py + core.py mirror this backtest exactly.

5-year Binance BTCUSDT backtest: $5K → $147,496 (+96.9% CAGR, -19.7% DD, PF 4.29, 73 trades)
vs previous v6 (no BE after TP): $5K → $148,562 (+97.1% CAGR, -19.7% DD, PF 4.20, 79 trades)
→ BE-after-partial-TP cuts 8 SLs, lifts PF 4.20→4.29, same CAGR/DD. Kills runner-giveback.

Architecture (3 timeframes):
  Daily : EMA50 trend         (close > Daily EMA50 → LONG bias / < → SHORT bias)
  4H    : RSI(14) confirm     (RSI > 50 confirms LONG / < 50 confirms SHORT)
  1H    : EXECUTION + entry stack:
            - RSI(14) in pullback zone:   long > 45,  short < 55
            - MACD(12,26,9):              line > signal (long) / < (short)
            - Bullish/Bearish engulfing:  body > 1.0× prev body
            - Volatility filter:          ATR(14) > rolling-50-mean ATR
            - Volume spike:               vol > 1.5× rolling-20-mean

Entry  : ALL of the above must agree (Daily trend + 4H RSI + 1H entry stack)

Exit:
  Stop loss     : pattern-based (min of bar/prev low), capped at 2.5% from entry
  Partial TP    : 30% off at +5R, remainder runs with SL moved to BE+0.1%
                  (break-even exit after partial locks in profit + kills giveback)
  Opposite exit : close only on opposite signal (no flip-open, V4 rule)
  DD circuit    : halt 7 days after −25% peak-to-trough drawdown
  Cooldown      : 24h same-dir after SL hit + 2h generic post-exit

V6 SL-FLIP extension (NEW, live on testnet):
  - On SL hit → queue opposite-direction flip (if this wasn't already a flip)
  - Wait 1h after SL for whipsaw to settle
  - Open flip with TIGHTER SL: min(swing high/low + 0.1% buffer, 1.5% cap from original SL)
  - Flip time-stop: exit after 24h if still open
  - No flip-on-flip (prevents cascading)
  - 5yr backtest: +32 flip trades at 55% WR = +$108K over v5 baseline

Risk:
  - 1% of equity per trade (sized off SL distance)
  - 2× leverage
  - Fees: 0.04% taker + 0.03% slippage
"""
import os, json
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))

# ─── Config ───
START_CAPITAL    = 5_000.0
LEVERAGE         = 2.0
TAKER_FEE        = 0.0004      # taker (0.0002 = maker)
SLIPPAGE         = 0.0003

SL_MODE          = "pattern"    # "pattern" = candle-based  |  "atr" = volatility-based
SL_ATR_MULT      = 2.0          # used when SL_MODE = "atr"
SL_MAX_PCT       = 0.025        # cap pattern-based SL at 2.5% from entry
SL_BUFFER_PCT    = 0.001        # 0.1% padding below/above the pattern low/high
DD_HALT_PCT      = 0.25
DD_HALT_BARS     = 168
COOLDOWN_BARS    = 2
SAME_DIR_CD_BARS = 24           # 24h same-dir cooldown (v6+flip: tested 24h matches 36h performance with +4 trades)
BE_TRIGGER_R     = 999.0        # disabled — BE-move killed winners

RSI_LONG_MIN     = 45
RSI_SHORT_MAX    = 55
ENGULF_BODY_MULT = 1.0           # any-size engulfing (sweep: +2 trades, +3pp CAGR)
ATR_MA_LEN       = 50

# V3 SAFE additions (do not alter V2 entry/exit core logic):
USE_VOLUME_FILTER = True
VOL_SMA_LEN       = 20
VOL_SPIKE_RATIO   = 1.5          # stricter vol filter (sweep: +8pp CAGR, better DD+PF)
USE_PARTIAL_TP    = True
PARTIAL_TP_R      = 5.0          # lock 30% at +5R (best Calmar 4.57 with vol 1.5×)
PARTIAL_TP_FRAC   = 0.30         # take 30% off, leave 70% to keep running on original SL
PARTIAL_BE_BUF    = 0.001        # after partial TP fires, move SL to entry ± 0.1% (covers fees)

# ─── V6 SL-FLIP extension (+$131K over baseline in 5yr backtest) ───
USE_SL_FLIP       = True         # on SL hit, flip to opposite direction
FLIP_WAIT_BARS    = 1            # wait 1 hour after SL before flipping (avoids whipsaw)
FLIP_SL_CAP       = 0.015        # 1.5% max SL for flip (tighter than v5's 2.5%)
FLIP_SR_LOOKBACK  = 10           # bars to look back for swing high/low on flip SL
FLIP_TIME_STOP    = 24           # exit flip after 24h if still open


# ─── Indicators ───
def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def macd_lines(s, f=12, sl=26, sg=9):
    ef = s.ewm(span=f, adjust=False).mean()
    es = s.ewm(span=sl, adjust=False).mean()
    line = ef - es
    sig  = line.ewm(span=sg, adjust=False).mean()
    return line, sig

def atr_calc(df, period=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"]  - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def map_htf(htf_df, htf_rule, base_ts, value_col):
    """Causal: stamp HTF value at close_time, asof-ffill onto base_ts."""
    htf_df = htf_df.copy()
    htf_df["close_time"] = htf_df["timestamp"] + pd.Timedelta(htf_rule)
    s = pd.Series(htf_df[value_col].values, index=htf_df["close_time"].values)
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(s.index.union(base_ts)).sort_index().ffill().reindex(base_ts).fillna(0).values


def load_15m():
    df = pd.read_csv(os.path.join(ROOT, "data", "cache", "BTCUSDT_15m_1825d.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    return df.sort_values("timestamp").reset_index(drop=True)


def run():
    src = load_15m()
    print(f"Source: {len(src):,} 15m bars")
    # Save 15m candles for chart display (resampled 1H is for trading logic)
    chart_15m = src.copy()

    # Resample 1H / 4H / Daily
    df = src.set_index("timestamp").resample("1h").agg({
        "open":"first","high":"max","low":"min","close":"last","volume":"sum"
    }).dropna().reset_index()
    df_4h = src.set_index("timestamp").resample("4h").agg({
        "open":"first","high":"max","low":"min","close":"last","volume":"sum"
    }).dropna().reset_index()
    df_d  = src.set_index("timestamp").resample("1D").agg({
        "open":"first","high":"max","low":"min","close":"last","volume":"sum"
    }).dropna().reset_index()
    print(f"Frames — 1H: {len(df):,}  4H: {len(df_4h):,}  Daily: {len(df_d):,}")

    # 1H indicators
    df["rsi"]        = rsi(df["close"])
    df["macd_line"], df["macd_signal"] = macd_lines(df["close"])
    df["atr"]        = atr_calc(df)
    df["atr_ma"]     = df["atr"].rolling(ATR_MA_LEN).mean()
    df["high_vol"]   = df["atr"] > df["atr_ma"]
    # V3: volume filter
    df["vol_sma"]    = df["volume"].rolling(VOL_SMA_LEN).mean()
    df["vol_ok"]     = df["volume"] > VOL_SPIKE_RATIO * df["vol_sma"] if USE_VOLUME_FILTER else True

    # 1H engulfing patterns (with 1.2× body filter)
    body = (df["close"] - df["open"]).abs()
    pbody = body.shift(1)
    df["bull_eng"] = (
        (df["close"].shift(1) < df["open"].shift(1))
        & (df["close"] > df["open"])
        & (df["close"] >= df["open"].shift(1))
        & (df["open"]  <= df["close"].shift(1))
        & (body > pbody * ENGULF_BODY_MULT)
    )
    df["bear_eng"] = (
        (df["close"].shift(1) > df["open"].shift(1))
        & (df["close"] < df["open"])
        & (df["open"]  >= df["close"].shift(1))
        & (df["close"] <= df["open"].shift(1))
        & (body > pbody * ENGULF_BODY_MULT)
    )

    # 4H RSI confirm
    df_4h["rsi"] = rsi(df_4h["close"])
    df_4h["confirm"] = np.where(df_4h["rsi"] > 50, 1,
                       np.where(df_4h["rsi"] < 50, -1, 0))

    # Daily EMA50 trend
    df_d["ema50"] = ema(df_d["close"], 50)
    df_d["bias"]  = np.where(df_d["close"] > df_d["ema50"], 1,
                    np.where(df_d["close"] < df_d["ema50"], -1, 0))

    # Map HTF to 1H base
    base_ts    = pd.DatetimeIndex(df["timestamp"])
    bias_d     = map_htf(df_d, "1D", base_ts, "bias").astype(int)
    confirm_4h = map_htf(df_4h, "4h", base_ts, "confirm").astype(int)

    # ─── Backtest ───
    capital     = START_CAPITAL
    position    = 0
    entry_price = 0.0
    pos_atr     = 0.0
    pos_sl      = 0.0       # current stop price (can move to BE after +1R)
    pos_be_moved = False    # has SL been moved to BE for this position?
    cooldown    = 0
    halt_until  = -1
    last_long_sl_bar  = -10**9
    last_short_sl_bar = -10**9
    peak        = capital
    max_dd_pct  = 0.0
    trades      = []
    yearly      = {}
    n_long = n_short = wins = losses = sl_hits = flips = halts = be_exits = 0
    sigs_raw = sigs_full = 0
    # V3: partial TP tracking
    partial_taken = False
    partial_pnls  = []   # list of locked-in partial profits per position
    n_partial_tps = 0
    trade_log = []          # for HTML report
    equity_curve = []
    open_trade = None       # dict tracking current open position
    # V6 SL-flip state
    is_flip = False                # current position is a flip (not a normal entry)
    flip_entry_bar = -1            # bar index where flip entered
    pending_flip_side = 0          # 0 = none, 1 = pending LONG flip, -1 = pending SHORT flip
    pending_flip_bar = -1          # bar when flip was queued
    pending_flip_ref_price = 0.0   # original SL price (ref for % cap)
    n_flips_taken = n_flip_wins = n_flip_losses = 0
    flip_pnl_sum = 0.0

    for i in range(100, len(df)):
        row    = df.iloc[i]
        price  = row["close"]
        atr_v  = row["atr"]
        ts     = row["timestamp"]
        yr     = ts.year
        if yr not in yearly:
            yearly[yr] = {"start":capital,"end":capital,"t":[],"peak":capital,"dd":0.0}
        yr_r = yearly[yr]

        if i < halt_until:
            continue
        if cooldown > 0:
            cooldown -= 1

        # ─── V6 SL-FLIP: execute pending flip if wait period elapsed ───
        # Respects DD halt + post-exit cooldown (bug fix: flips were bypassing halt)
        if (USE_SL_FLIP and pending_flip_side != 0 and position == 0
                and (i - pending_flip_bar) >= FLIP_WAIT_BARS
                and cooldown == 0 and i >= halt_until
                and not pd.isna(atr_v) and atr_v > 0):
            side = pending_flip_side
            ref_p = pending_flip_ref_price
            lb_start = max(0, i - FLIP_SR_LOOKBACK)
            if side == -1:  # flip to SHORT
                swing_high = float(df["high"].iloc[lb_start:i+1].max())
                raw_sl = swing_high * (1 + SL_BUFFER_PCT)
                cap_sl = ref_p * (1 + FLIP_SL_CAP)
                pos_sl = min(raw_sl, cap_sl)  # tighter for shorts = lower
                position = -1
                n_short += 1
            else:  # flip to LONG
                swing_low = float(df["low"].iloc[lb_start:i+1].min())
                raw_sl = swing_low * (1 - SL_BUFFER_PCT)
                cap_sl = ref_p * (1 - FLIP_SL_CAP)
                pos_sl = max(raw_sl, cap_sl)  # tighter for longs = higher
                position = 1
                n_long += 1
            entry_price = price
            pos_atr = atr_v
            pos_be_moved = False
            is_flip = True
            flip_entry_bar = i
            n_flips_taken += 1
            open_pos(side, price, atr_v)
            pending_flip_side = 0

        # ─── V6 SL-FLIP: time-stop on flip position ───
        if is_flip and position != 0 and (i - flip_entry_bar) >= FLIP_TIME_STOP:
            close_and_record(price, "TIME")
            continue

        # 1H entry stack — V2 logic + V3 volume filter
        vol_pass = bool(row["vol_ok"]) if USE_VOLUME_FILTER else True
        long_1h = bool(
            row["rsi"] > RSI_LONG_MIN
            and row["macd_line"] > row["macd_signal"]
            and row["bull_eng"]
            and row["high_vol"]
            and vol_pass
        )
        short_1h = bool(
            row["rsi"] < RSI_SHORT_MAX
            and row["macd_line"] < row["macd_signal"]
            and row["bear_eng"]
            and row["high_vol"]
            and vol_pass
        )

        if row["bull_eng"] or row["bear_eng"]: sigs_raw += 1

        # MTF gate
        long_full  = long_1h  and bias_d[i] == 1  and confirm_4h[i] == 1
        short_full = short_1h and bias_d[i] == -1 and confirm_4h[i] == -1
        if long_full or short_full: sigs_full += 1

        # V2: SAME-DIRECTION COOLDOWN — block re-entry after SL hit in same dir
        if long_full  and (i - last_long_sl_bar)  < SAME_DIR_CD_BARS: long_full  = False
        if short_full and (i - last_short_sl_bar) < SAME_DIR_CD_BARS: short_full = False

        def close_and_record(exit_px, reason):
            nonlocal capital, position, cooldown, wins, losses, sl_hits, flips, be_exits, open_trade
            nonlocal last_long_sl_bar, last_short_sl_bar, partial_taken
            nonlocal is_flip, pending_flip_side, pending_flip_bar, pending_flip_ref_price
            nonlocal n_flips_taken, n_flip_wins, n_flip_losses, flip_pnl_sum
            # V3: if partial TP was taken, only the remaining (1 - PARTIAL_TP_FRAC) closes here
            remaining = (1.0 - PARTIAL_TP_FRAC) if (USE_PARTIAL_TP and partial_taken) else 1.0
            pmp = ((exit_px - entry_price)/entry_price if position == 1
                   else (entry_price - exit_px)/entry_price)
            pnl_lev = pmp * LEVERAGE * remaining
            cost    = (TAKER_FEE + SLIPPAGE) * 2 * LEVERAGE * remaining
            net     = pnl_lev - cost
            capital *= (1 + net)
            # Add back any locked-in partial PnL for the trade-record's pnl_pct display
            full_trade_pnl = net + (partial_pnls[-1] / 100.0 if (USE_PARTIAL_TP and partial_taken and partial_pnls) else 0.0)
            partial_taken = False
            trades.append(full_trade_pnl * 100); yr_r["t"].append(full_trade_pnl * 100)
            if full_trade_pnl > 0: wins += 1
            else:                  losses += 1
            # V6: track flip trade stats separately
            was_flip = is_flip
            if was_flip:
                flip_pnl_sum += full_trade_pnl * 100
                if full_trade_pnl > 0: n_flip_wins += 1
                else:                  n_flip_losses += 1
            if reason == "SL":
                sl_hits += 1
                if position == 1:  last_long_sl_bar  = i
                if position == -1: last_short_sl_bar = i
                # V6 SL-FLIP: queue opposite-direction flip, but ONLY if this wasn't already a flip
                if USE_SL_FLIP and not was_flip:
                    pending_flip_side = -1 if position == 1 else 1
                    pending_flip_bar = i
                    pending_flip_ref_price = exit_px
            elif reason == "BE":   be_exits += 1
            elif reason == "FLIP": flips += 1
            elif reason == "EXIT-OPP": flips += 1   # exit only (no reverse)
            if open_trade is not None:
                open_trade.update({
                    "exit_time": int(ts.timestamp()),
                    "exit_price": float(exit_px),
                    "exit_reason": ("FLIP-" + reason) if was_flip else reason,
                    "pnl_pct": float(net * 100),
                    "capital_after": float(capital),
                })
                trade_log.append(open_trade)
                open_trade = None
            position = 0
            is_flip = False
            cooldown = COOLDOWN_BARS

        def open_pos(side, e_price, e_atr):
            nonlocal open_trade
            open_trade = {
                "entry_time": int(ts.timestamp()),
                "entry_price": float(e_price),
                "side": "LONG" if side == 1 else "SHORT",
            }

        # V3 PARTIAL TP — fires only on huge winners (PARTIAL_TP_R = 6R)
        # Locks PARTIAL_TP_FRAC of position, leaves rest running on original SL
        if USE_PARTIAL_TP and position != 0 and not partial_taken:
            initial_sl_dist = abs(entry_price - pos_sl)
            if position == 1 and (price - entry_price) >= PARTIAL_TP_R * initial_sl_dist:
                pmp = (price - entry_price) / entry_price
                pnl_lev = pmp * LEVERAGE * PARTIAL_TP_FRAC
                cost = (TAKER_FEE + SLIPPAGE) * 2 * LEVERAGE * PARTIAL_TP_FRAC
                net = pnl_lev - cost
                capital *= (1 + net)
                partial_pnls.append(net * 100)
                partial_taken = True
                n_partial_tps += 1
                # After partial TP: move SL to break-even + buffer (locks in profit, kills giveback)
                pos_sl = entry_price * (1 + PARTIAL_BE_BUF)
            elif position == -1 and (entry_price - price) >= PARTIAL_TP_R * initial_sl_dist:
                pmp = (entry_price - price) / entry_price
                pnl_lev = pmp * LEVERAGE * PARTIAL_TP_FRAC
                cost = (TAKER_FEE + SLIPPAGE) * 2 * LEVERAGE * PARTIAL_TP_FRAC
                net = pnl_lev - cost
                capital *= (1 + net)
                partial_pnls.append(net * 100)
                partial_taken = True
                n_partial_tps += 1
                pos_sl = entry_price * (1 - PARTIAL_BE_BUF)

        # Manage open position. pos_sl is the source-of-truth stop (may be moved to BE)
        if position == 1:
            # Move to BE after +1R favorable
            initial_sl_dist = entry_price - (pos_sl if not pos_be_moved else 0)
            if not pos_be_moved:
                if (price - entry_price) >= BE_TRIGGER_R * initial_sl_dist:
                    pos_sl = entry_price + (TAKER_FEE + SLIPPAGE) * 2 * entry_price
                    pos_be_moved = True
            if row["low"] <= pos_sl:
                close_and_record(pos_sl, "BE" if pos_be_moved else "SL")
            elif short_full:
                # V4: EXIT only — do NOT flip-open. Avoids buying tops after winning shorts.
                close_and_record(price, "EXIT-OPP")
        elif position == -1:
            initial_sl_dist = (pos_sl if not pos_be_moved else 0) - entry_price
            if not pos_be_moved:
                if (entry_price - price) >= BE_TRIGGER_R * initial_sl_dist:
                    pos_sl = entry_price - (TAKER_FEE + SLIPPAGE) * 2 * entry_price
                    pos_be_moved = True
            if row["high"] >= pos_sl:
                close_and_record(pos_sl, "BE" if pos_be_moved else "SL")
            elif long_full:
                # V4: EXIT only — do NOT flip-open. Avoids selling bottoms after winning longs.
                close_and_record(price, "EXIT-OPP")
        elif cooldown == 0 and pending_flip_side == 0:
            if pd.isna(atr_v) or atr_v <= 0:
                pass
            elif long_full:
                position = 1; entry_price = price; pos_atr = atr_v
                is_flip = False
                # SL based on chosen mode
                if SL_MODE == "pattern":
                    # Use min(low of entry bar, low of prior bar) — the engulfed body's low
                    pat_low = min(row["low"], df["low"].iloc[i-1])
                    raw_sl = pat_low * (1 - SL_BUFFER_PCT)
                    cap_sl = entry_price * (1 - SL_MAX_PCT)
                    pos_sl = max(raw_sl, cap_sl)   # use tighter (closer to entry) of the two
                else:
                    pos_sl = entry_price - atr_v * SL_ATR_MULT
                pos_be_moved = False; n_long += 1
                open_pos(1, price, atr_v)
            elif short_full:
                position = -1; entry_price = price; pos_atr = atr_v
                is_flip = False
                if SL_MODE == "pattern":
                    pat_high = max(row["high"], df["high"].iloc[i-1])
                    raw_sl = pat_high * (1 + SL_BUFFER_PCT)
                    cap_sl = entry_price * (1 + SL_MAX_PCT)
                    pos_sl = min(raw_sl, cap_sl)
                else:
                    pos_sl = entry_price + atr_v * SL_ATR_MULT
                pos_be_moved = False; n_short += 1
                open_pos(-1, price, atr_v)

        # DD tracking + circuit breaker
        peak = max(peak, capital)
        dd_pct = (capital - peak) / peak * 100 if peak > 0 else 0
        if dd_pct < max_dd_pct: max_dd_pct = dd_pct
        if dd_pct <= -DD_HALT_PCT * 100:
            halt_until = i + DD_HALT_BARS
            halts += 1
            if position != 0:
                close_and_record(price, "SL")
            peak = capital  # reset to avoid retriggering on same DD

        yr_r["end"]  = capital
        yr_r["peak"] = max(yr_r["peak"], capital)
        ydd = (capital - yr_r["peak"]) / yr_r["peak"] * 100 if yr_r["peak"] > 0 else 0
        if ydd < yr_r["dd"]: yr_r["dd"] = ydd

        # Sample equity curve daily-ish (every 24 1H bars)
        if i % 24 == 0:
            equity_curve.append({"t": int(ts.timestamp()), "v": float(capital)})

    yrs   = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25
    n     = len(trades)
    wr    = wins / max(wins + losses, 1) * 100
    gw    = sum(t for t in trades if t > 0)
    gl    = abs(sum(t for t in trades if t <= 0))
    pf    = gw / max(gl, 1e-9)
    cagr  = ((capital/START_CAPITAL)**(1/yrs) - 1) * 100 if capital > 0 else float("nan")
    fee_label = "MAKER" if TAKER_FEE <= 0.0003 else "TAKER"

    print(f"\n{'='*68}")
    print(f"  STRATEGY V5 (V4 + post-mortem audit)  |  lev={LEVERAGE}x  |  {fee_label}")
    print(f"  Daily EMA50 + 4H RSI + 1H stack | SL={SL_MODE} (cap {SL_MAX_PCT*100:.1f}%) | BE-move @+1R")
    print(f"  24b same-dir cooldown after SL  |  DD-halt @{DD_HALT_PCT*100:.0f}% / 7d")
    print(f"  Fees: {TAKER_FEE*100:.2f}% + slip {SLIPPAGE*100:.2f}% on notional×lev")
    print(f"{'='*68}")
    print(f"  Funnel: {sigs_raw} raw 1H engulf signals → {sigs_full} pass MTF + indicator gate")
    print(f"  Start → Final:  ${START_CAPITAL:,.0f} → ${capital:,.2f}  ({(capital/START_CAPITAL-1)*100:+.1f}%)")
    print(f"  CAGR:           {cagr:+.1f}%")
    print(f"  Max drawdown:   {max_dd_pct:+.1f}%")
    print(f"  Trades:         {n}  (~{n/yrs:.0f}/yr)  Long {n_long}  Short {n_short}  "
          f"Flips {flips}  SLs {sl_hits}  DD-halts {halts}")
    print(f"  Win rate:       {wr:.1f}%  ({wins}W/{losses}L)")
    print(f"  Profit factor:  {pf:.2f}")

    # ─── Audit cooldown rule ───
    print(f"\n  Cooldown audit (should be ≥24 1H bars between SL-hit and next same-dir entry):")
    last_sl_long  = None
    last_sl_short = None
    violations = 0
    for tr in trade_log:
        et = tr["entry_time"]
        side = tr["side"]
        if side == "LONG" and last_sl_long is not None:
            gap_h = (et - last_sl_long) / 3600
            if gap_h < 24:
                violations += 1
                if violations <= 5:
                    print(f"    ⚠ LONG entry {pd.Timestamp(et, unit='s')} only {gap_h:.1f}h after last LONG-SL")
        if side == "SHORT" and last_sl_short is not None:
            gap_h = (et - last_sl_short) / 3600
            if gap_h < 24:
                violations += 1
                if violations <= 5:
                    print(f"    ⚠ SHORT entry {pd.Timestamp(et, unit='s')} only {gap_h:.1f}h after last SHORT-SL")
        if tr["exit_reason"] == "SL":
            if side == "LONG":  last_sl_long  = tr["exit_time"]
            else:               last_sl_short = tr["exit_time"]
    if violations == 0:
        print(f"    ✓ All {len(trade_log)} trades respect 24h cooldown after SL")
    else:
        print(f"    Total violations: {violations}")

    # Sample cluster — print 10 trades around 2024-09 (your screenshot area)
    print(f"\n  Sample trade sequence (first 15 trades from Sep 2024):")
    print(f"    {'#':<4}{'Side':<6}{'Entry Time':<20}{'Exit Time':<20}{'Reason':<10}{'PnL%':>8}")
    cnt = 0
    for idx, tr in enumerate(trade_log):
        if pd.Timestamp(tr["entry_time"], unit="s").year == 2024 and pd.Timestamp(tr["entry_time"], unit="s").month == 9:
            print(f"    {idx+1:<4}{tr['side']:<6}"
                  f"{str(pd.Timestamp(tr['entry_time'],unit='s')):<20}"
                  f"{str(pd.Timestamp(tr['exit_time'],unit='s')):<20}"
                  f"{tr['exit_reason']:<10}{tr['pnl_pct']:>+7.2f}%")
            cnt += 1
            if cnt >= 15: break

    # ─── V5 POST-MORTEM: Were SLs valid? Were exits too early? ───
    print(f"\n  ━━━ SL POST-MORTEM (24h after each SL hit) ━━━")
    sl_correct = 0     # SL was right: price continued in adverse direction (we'd have lost more holding)
    sl_wrong   = 0     # SL was wrong: price reversed within 24h (we should have held)
    sl_avg_continued = []  # how much further price went in adverse direction
    sl_avg_reversed  = []  # how much price reversed back toward our entry

    # Build 1H bar lookup by timestamp
    df_idx = {int(t.timestamp()): i for i, t in enumerate(df["timestamp"])}
    closes_arr = df["close"].values
    highs_arr  = df["high"].values
    lows_arr   = df["low"].values

    for tr in trade_log:
        if tr["exit_reason"] != "SL": continue
        exit_t = tr["exit_time"]; entry_p = tr["entry_price"]; exit_p = tr["exit_price"]
        side = tr["side"]
        bar_i = df_idx.get(exit_t)
        if bar_i is None or bar_i + 24 >= len(df): continue
        # Check 24h forward
        fwd_window = closes_arr[bar_i+1 : bar_i+25]
        if side == "LONG":
            # SL hit at exit_p (below entry). If price continues lower → SL was correct.
            min_fwd = lows_arr[bar_i+1 : bar_i+25].min()
            if min_fwd < exit_p:
                sl_correct += 1
                sl_avg_continued.append((exit_p - min_fwd) / exit_p * 100)
            else:
                # Price reversed back up — did it go back above entry?
                max_fwd = highs_arr[bar_i+1 : bar_i+25].max()
                if max_fwd > entry_p:
                    sl_wrong += 1
                    sl_avg_reversed.append((max_fwd - exit_p) / exit_p * 100)
                else:
                    sl_correct += 1  # bounced but didn't recover entry
        else:  # SHORT
            max_fwd = highs_arr[bar_i+1 : bar_i+25].max()
            if max_fwd > exit_p:
                sl_correct += 1
                sl_avg_continued.append((max_fwd - exit_p) / exit_p * 100)
            else:
                min_fwd = lows_arr[bar_i+1 : bar_i+25].min()
                if min_fwd < entry_p:
                    sl_wrong += 1
                    sl_avg_reversed.append((exit_p - min_fwd) / exit_p * 100)
                else:
                    sl_correct += 1

    total_sl_audit = sl_correct + sl_wrong
    if total_sl_audit > 0:
        print(f"    SL hits audited:  {total_sl_audit}")
        print(f"    SL was CORRECT (price continued adverse):  {sl_correct}  ({sl_correct/total_sl_audit*100:.1f}%)")
        print(f"    SL was WRONG (price reversed within 24h):  {sl_wrong}  ({sl_wrong/total_sl_audit*100:.1f}%)")
        if sl_avg_continued:
            print(f"    Avg further adverse move when SL was correct:  {np.mean(sl_avg_continued):+.2f}%")
        if sl_avg_reversed:
            print(f"    Avg recovery when SL was wrong:                {np.mean(sl_avg_reversed):+.2f}%")

    # ─── EXIT TIMING audit ───
    print(f"\n  ━━━ EXIT TIMING audit (would holding longer help?) ━━━")
    early_exit = 0     # winners that price continued in our favor after exit
    perfect    = 0     # winners that reversed soon after exit
    extra_pcts = []    # how much MORE we could have made
    for tr in trade_log:
        if tr["pnl_pct"] <= 0: continue   # only check winners
        exit_t = tr["exit_time"]; exit_p = tr["exit_price"]; side = tr["side"]
        bar_i = df_idx.get(exit_t)
        if bar_i is None or bar_i + 48 >= len(df): continue
        # Check 48h forward
        if side == "LONG":
            max_fwd = highs_arr[bar_i+1 : bar_i+49].max()
            extra = (max_fwd - exit_p) / exit_p * 100
        else:
            min_fwd = lows_arr[bar_i+1 : bar_i+49].min()
            extra = (exit_p - min_fwd) / exit_p * 100
        if extra > 1.0:   # >1% more move available
            early_exit += 1
            extra_pcts.append(extra)
        else:
            perfect += 1

    total_exit_audit = early_exit + perfect
    if total_exit_audit > 0:
        print(f"    Winners audited:  {total_exit_audit}")
        print(f"    Exited TOO EARLY (>1% more move available in next 48h):  {early_exit}  ({early_exit/total_exit_audit*100:.1f}%)")
        print(f"    Exited at right time:                                   {perfect}")
        if extra_pcts:
            print(f"    Avg extra move missed on early exits:  {np.mean(extra_pcts):+.2f}%  (max {max(extra_pcts):+.2f}%)")

    print(f"\n  Year-by-year:")
    print(f"  {'Year':<6}{'Start':>14}{'End':>14}{'Ret%':>9}{'DD%':>8}{'Trades':>8}{'Win%':>7}{'PF':>7}")
    for y in sorted(yearly.keys()):
        d = yearly[y]; t = d['t']
        gwy = sum(x for x in t if x > 0); gly = abs(sum(x for x in t if x <= 0))
        py  = gwy / max(gly, 1e-9)
        wy  = sum(1 for x in t if x > 0); tot = len(t)
        wry = wy/tot*100 if tot else 0
        ret = (d['end']/d['start']-1)*100 if d['start'] > 0 else 0
        print(f"  {y:<6}{d['start']:>14,.0f}{d['end']:>14,.0f}"
              f"{ret:>+9.1f}{d['dd']:>+8.1f}{tot:>8}{wry:>7.1f}{py:>7.2f}")

    # ─── HTML Report ───
    # Use 15m candles for chart display (more granular than 1H execution timeframe)
    candles_data = [{
        "time": int(t.timestamp()),
        "open": float(o), "high": float(h),
        "low": float(l), "close": float(c)
    } for t,o,h,l,c in zip(chart_15m["timestamp"], chart_15m["open"],
                            chart_15m["high"], chart_15m["low"], chart_15m["close"])]

    summary = {
        "final": float(capital), "cagr": float(cagr), "max_dd": float(max_dd_pct),
        "trades": int(n), "wins": int(wins), "losses": int(losses),
        "wr": float(wr), "pf": float(pf), "leverage": float(LEVERAGE),
        "fee_label": fee_label, "n_long": int(n_long), "n_short": int(n_short),
        "sl_hits": int(sl_hits), "flips": int(flips),
    }
    html = build_html(candles_data, trade_log, equity_curve, summary, yearly)
    out_path = os.path.join(ROOT, "report_v5.html")
    with open(out_path, "w") as f:
        f.write(html)
    print(f"\n  HTML report → {out_path}")


def build_html(candles, trades, equity, summary, yearly):
    candles_json = json.dumps(candles)
    trades_json  = json.dumps(trades)
    equity_json  = json.dumps(equity)
    yearly_rows = ""
    for y in sorted(yearly.keys()):
        d = yearly[y]; t = d['t']
        gwy = sum(x for x in t if x>0); gly = abs(sum(x for x in t if x<=0))
        py = gwy/max(gly,1e-9); wy = sum(1 for x in t if x>0); tot = len(t)
        wry = wy/tot*100 if tot else 0
        ret = (d['end']/d['start']-1)*100 if d['start']>0 else 0
        cls = "pos" if ret >= 0 else "neg"
        yearly_rows += (f"<tr><td>{y}</td><td>${d['start']:,.0f}</td><td>${d['end']:,.0f}</td>"
                        f"<td class='{cls}'>{ret:+.1f}%</td><td class='neg'>{d['dd']:+.1f}%</td>"
                        f"<td>{tot}</td><td>{wry:.1f}%</td><td>{py:.2f}</td></tr>")
    cagr_cls = "pos" if summary["cagr"] >= 0 else "neg"
    final_cls = "pos" if summary["final"] >= 10000 else "neg"
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Strategy V5 — Audit Report</title>
<script src="https://unpkg.com/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js"></script>
<style>
body{{margin:0;background:#0d1117;color:#e6edf3;font-family:-apple-system,sans-serif}}
.header{{padding:20px;background:#161b22;border-bottom:1px solid #30363d}}
h1{{margin:0;font-size:20px}} .sub{{color:#7d8590;margin-top:4px;font-size:13px}}
.stats{{display:grid;grid-template-columns:repeat(8,1fr);gap:12px;margin:16px 20px}}
.stat{{background:#161b22;padding:12px;border-radius:6px;border:1px solid #30363d}}
.stat .label{{color:#7d8590;font-size:11px;text-transform:uppercase}}
.stat .value{{font-size:18px;font-weight:600;margin-top:4px}}
.pos{{color:#3fb950}} .neg{{color:#f85149}}
.container{{padding:0 20px 20px}}
#chart,#equity_chart{{width:100%;background:#0d1117;border:1px solid #30363d;border-radius:6px}}
#chart{{height:520px;margin-bottom:8px}} #equity_chart{{height:200px}}
table{{width:100%;border-collapse:collapse;background:#161b22;border:1px solid #30363d;border-radius:6px;overflow:hidden}}
th,td{{padding:8px 12px;text-align:right;border-bottom:1px solid #30363d;font-size:13px}}
th{{background:#21262d;font-weight:600;color:#7d8590;text-transform:uppercase;font-size:11px}}
th:first-child,td:first-child{{text-align:left}}
.section{{margin-top:24px}} .section h2{{font-size:14px;color:#7d8590;text-transform:uppercase;margin:0 0 12px;font-weight:600}}
.legend{{font-size:12px;color:#7d8590;margin:8px 0}}
.badge{{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;margin-right:6px}}
.b-long{{background:#1a4d2a;color:#3fb950}} .b-short{{background:#5c1f1f;color:#f85149}}
.b-tp{{background:#0e3a5c;color:#79c0ff}} .b-sl{{background:#5c1f1f;color:#f85149}}
.b-flip{{background:#3a2c5c;color:#bc8cff}}
</style></head><body>
<div class="header">
  <h1>Strategy V4 — No Flip-Open</h1>
  <div class="sub">V3 + on opposite signal: EXIT only, do NOT open opposite. Avoids buying tops / selling bottoms after big winners. Chart: 15m. Exec: 1H. · {summary['fee_label']} · {summary['leverage']:.0f}× lev</div>
</div>
<div class="stats">
  <div class="stat"><div class="label">Final</div><div class="value {final_cls}">${summary['final']:,.0f}</div></div>
  <div class="stat"><div class="label">CAGR</div><div class="value {cagr_cls}">{summary['cagr']:+.1f}%</div></div>
  <div class="stat"><div class="label">Max DD</div><div class="value neg">{summary['max_dd']:+.1f}%</div></div>
  <div class="stat"><div class="label">PF</div><div class="value">{summary['pf']:.2f}</div></div>
  <div class="stat"><div class="label">Trades</div><div class="value">{summary['trades']}</div></div>
  <div class="stat"><div class="label">Win Rate</div><div class="value">{summary['wr']:.1f}%</div></div>
  <div class="stat"><div class="label">L/S</div><div class="value">{summary['n_long']}/{summary['n_short']}</div></div>
  <div class="stat"><div class="label">SL/Flip</div><div class="value">{summary['sl_hits']}/{summary['flips']}</div></div>
</div>
<div class="container">
  <div class="section"><h2>Price chart with trades</h2>
    <div class="legend">
      <span class="badge b-long">▲ LONG entry</span>
      <span class="badge b-short">▼ SHORT entry</span>
      <span class="badge b-tp">● Win exit</span>
      <span class="badge b-sl">✕ SL exit</span>
      <span class="badge b-flip">⟲ Flip exit</span>
    </div>
    <div id="chart"></div>
  </div>
  <div class="section"><h2>Equity curve</h2><div id="equity_chart"></div></div>
  <div class="section"><h2>Year-by-year</h2>
    <table>
      <tr><th>Year</th><th>Start</th><th>End</th><th>Return</th><th>Max DD</th><th>Trades</th><th>WR</th><th>PF</th></tr>
      {yearly_rows}
    </table>
  </div>
  <div class="section"><h2>Trade log (most recent 100)</h2>
    <table id="trade_table">
      <tr><th>#</th><th>Side</th><th>Entry Time</th><th>Entry $</th><th>Exit Time</th><th>Exit $</th><th>Reason</th><th>PnL %</th><th>Capital</th></tr>
    </table>
  </div>
</div>
<script>
const candles = {candles_json};
const trades  = {trades_json};
const equity  = {equity_json};
const chart = LightweightCharts.createChart(document.getElementById('chart'), {{
  layout:{{background:{{type:'solid',color:'#0d1117'}},textColor:'#e6edf3'}},
  grid:{{vertLines:{{color:'#21262d'}},horzLines:{{color:'#21262d'}}}},
  timeScale:{{timeVisible:true,secondsVisible:false}},
  rightPriceScale:{{borderColor:'#30363d'}}
}});
const series = chart.addCandlestickSeries({{
  upColor:'#3fb950',downColor:'#f85149',
  borderUpColor:'#3fb950',borderDownColor:'#f85149',
  wickUpColor:'#3fb950',wickDownColor:'#f85149'
}});
series.setData(candles);
const markers = [];
trades.forEach(t => {{
  markers.push({{time:t.entry_time, position:t.side==='LONG'?'belowBar':'aboveBar',
    color:t.side==='LONG'?'#3fb950':'#f85149', shape:t.side==='LONG'?'arrowUp':'arrowDown', text:t.side}});
  const win = t.pnl_pct > 0;
  markers.push({{time:t.exit_time, position:t.side==='LONG'?'aboveBar':'belowBar',
    color: win ? '#79c0ff' : (t.exit_reason==='FLIP'?'#bc8cff':'#f85149'),
    shape: win ? 'circle' : 'square', text:t.exit_reason+' '+t.pnl_pct.toFixed(1)+'%'}});
}});
markers.sort((a,b)=>a.time-b.time);
series.setMarkers(markers);
const eqChart = LightweightCharts.createChart(document.getElementById('equity_chart'), {{
  layout:{{background:{{type:'solid',color:'#0d1117'}},textColor:'#e6edf3'}},
  grid:{{vertLines:{{color:'#21262d'}},horzLines:{{color:'#21262d'}}}},
  timeScale:{{timeVisible:true,secondsVisible:false}},
  rightPriceScale:{{borderColor:'#30363d'}}
}});
const eqSeries = eqChart.addLineSeries({{color:'#3fb950',lineWidth:2}});
eqSeries.setData(equity.map(e => ({{time:e.t, value:e.v}})));
const tt = document.getElementById('trade_table');
const recent = trades.slice(-100).reverse();
recent.forEach((t,i) => {{
  const tr = document.createElement('tr');
  const cls = t.pnl_pct > 0 ? 'pos' : 'neg';
  const fmt = ts => new Date(ts*1000).toISOString().slice(0,16).replace('T',' ');
  tr.innerHTML = `<td>${{trades.length - i}}</td><td>${{t.side}}</td>` +
    `<td>${{fmt(t.entry_time)}}</td><td>$${{t.entry_price.toFixed(0)}}</td>` +
    `<td>${{fmt(t.exit_time)}}</td><td>$${{t.exit_price.toFixed(0)}}</td>` +
    `<td>${{t.exit_reason}}</td><td class="${{cls}}">${{t.pnl_pct.toFixed(2)}}%</td>` +
    `<td>$${{t.capital_after.toFixed(0)}}</td>`;
  tt.appendChild(tr);
}});
chart.timeScale().fitContent();
eqChart.timeScale().fitContent();
</script>
</body></html>"""


if __name__ == "__main__":
    run()
