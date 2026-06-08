"""
Independent Python backtester for BTC-Flip-Bot v1.1 SMART strategy.
Built from scratch from spec. Cross-check vs TypeScript implementation.
"""
import pandas as pd
import numpy as np
from datetime import timedelta

CSV_PATH = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"

# ---- Config (v1.1 SMART, as deployed) -------------------------------
INITIAL_BAL   = 5000.0
LEVERAGE      = 3
DCA_LEGS      = 2
BASE_MARGIN_PER_LEG = INITIAL_BAL * 0.95 * LEVERAGE / 3 / DCA_LEGS  # 2375
BASE_NOTIONAL_PER_LEG = BASE_MARGIN_PER_LEG * LEVERAGE  # not used; per-leg notional = margin*lev? Spec says margin*3=7125 (lev already in)
# Per spec: per-leg notional = $2,375 × 3 = $7,125
PER_LEG_NOTIONAL = 2375.0 * 3   # =7125 USD notional per leg

RSI_LEN       = 9
RSI_LONG      = 30
RSI_SHORT     = 70

EMA_FAST      = 20
EMA_SLOW      = 50
GAP_MIN       = 0.0025   # 0.25%
ATR_LEN       = 14
ATR_MAX_PCT   = 0.006    # 0.60%

DCA_TRIGGER   = 0.005    # 0.5% adverse
TP_L1         = 0.005    # 0.5%
TP_DCA        = 0.0025   # 0.25%
SL_L1         = 0.006    # 0.6% from worst entry
# SL after DCA = avg entry (BE)

FEE_RATE = 0.00055   # 0.04% taker per side
SLIPPAGE      = 0.0002   # 0.02% slippage per side

COOLDOWN_MIN  = 15
DAILY_STOP    = -200.0

TIME_SL_BARS  = 72       # 6h on 5m
WEEKEND_MULT  = 2.0


# ---- Indicators -----------------------------------------------------
def wilder_rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    # Wilder smoothing = EMA with alpha = 1/length
    avg_gain = gain.ewm(alpha=1.0/length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1.0/length, adjust=False, min_periods=length).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def ema(s: pd.Series, length: int) -> pd.Series:
    return s.ewm(span=length, adjust=False, min_periods=length).mean()


def wilder_atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0/length, adjust=False, min_periods=length).mean()
    return atr


# ---- Load + prep data ----------------------------------------------
print("Loading data...")
df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
df = df.sort_values("timestamp").reset_index(drop=True)
print(f"Total rows: {len(df)}, last ts: {df['timestamp'].iloc[-1]}")

# Take last 60 days
last_ts = df["timestamp"].iloc[-1]
cutoff = last_ts - timedelta(days=60)
df60 = df[df["timestamp"] >= cutoff].copy().reset_index(drop=True)
print(f"60-day window: {df60['timestamp'].iloc[0]} -> {df60['timestamp'].iloc[-1]}  ({len(df60)} 5m bars)")

# We need indicators warmed up. For RSI/ATR we can compute on full df then slice,
# but easier: load extra warmup. Let's compute on a slightly larger window for safety.
warmup_cut = last_ts - timedelta(days=65)
df_calc = df[df["timestamp"] >= warmup_cut].copy().reset_index(drop=True)

# 5m indicators
df_calc["rsi5"] = wilder_rsi(df_calc["close"], RSI_LEN)
df_calc["atr5"] = wilder_atr(df_calc["high"], df_calc["low"], df_calc["close"], ATR_LEN)
df_calc["atr_pct"] = df_calc["atr5"] / df_calc["close"]

# 15m indicators: resample
df_calc_ix = df_calc.set_index("timestamp")
df15 = df_calc_ix[["open", "high", "low", "close", "volume"]].resample("15min", label="left", closed="left").agg({
    "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
}).dropna()
df15["ema20"] = ema(df15["close"], EMA_FAST)
df15["ema50"] = ema(df15["close"], EMA_SLOW)
# Trend: +1 up, -1 down, 0 unknown
df15["trend"] = np.where(df15["ema20"] > df15["ema50"], 1,
                  np.where(df15["ema20"] < df15["ema50"], -1, 0))
df15["gap_pct"] = (df15["ema20"] - df15["ema50"]).abs() / df15["ema50"]

# IMPORTANT: only use 15m bar AFTER it has closed.
# A 5m bar at time t can only see 15m bar whose close <= t (strictly).
# Standard practice: 15m bar with timestamp T (label=left) covers [T, T+15).
# It "closes" at T+15. So at 5m bar time t, the latest fully-closed 15m bar
# is the one with timestamp T where T+15 <= t, i.e. T <= t-15.
df15 = df15.reset_index().rename(columns={"timestamp": "ts15"})
# We'll merge_asof: for each 5m bar t, find the 15m bar with (ts15 + 15min) <= t
df15["closed_at"] = df15["ts15"] + pd.Timedelta(minutes=15)

df_calc = df_calc.sort_values("timestamp")
merged = pd.merge_asof(
    df_calc,
    df15[["closed_at", "ema20", "ema50", "trend", "gap_pct"]].sort_values("closed_at"),
    left_on="timestamp", right_on="closed_at", direction="backward"
)

# Slice to last 60 days
bt = merged[merged["timestamp"] >= cutoff].reset_index(drop=True)
print(f"Backtest rows after merge: {len(bt)}")
print(f"NaN check: rsi5={bt['rsi5'].isna().sum()}, atr5={bt['atr5'].isna().sum()}, ema20={bt['ema20'].isna().sum()}")


# ---- Backtest engine -----------------------------------------------
balance = INITIAL_BAL
peak    = INITIAL_BAL
max_dd  = 0.0

position = None     # dict or None
cooldown_until = None  # timestamp
daily_pnl = {}        # date -> cumulative pnl

trades = []           # list of closed trade dicts
exit_reasons = {"TP": 0, "BE-DCA": 0, "SL": 0, "TREND_FLIP": 0, "TIME_SL": 0}

def compute_pnl(side: str, avg_entry: float, exit_px: float, qty: float):
    """Return (gross_pnl, fees, net_pnl). Slippage applied to exit price."""
    # slippage worsens the exit
    if side == "LONG":
        eff_exit = exit_px * (1 - SLIPPAGE)
        gross = (eff_exit - avg_entry) * qty
    else:
        eff_exit = exit_px * (1 + SLIPPAGE)
        gross = (avg_entry - eff_exit) * qty
    # Fees: taker on both legs (entry was charged at entry, exit charged here),
    # we'll account for entire round-trip here per trade for simplicity.
    notional_in  = avg_entry * qty
    notional_out = eff_exit * qty
    fees = (notional_in + notional_out) * FEE_RATE
    return gross, fees, gross - fees


def open_position(ts, side, price, weekend):
    """Open L1."""
    mult = WEEKEND_MULT if weekend else 1.0
    bal_scale = balance / INITIAL_BAL
    # entry slippage worsens entry
    if side == "LONG":
        eff_entry = price * (1 + SLIPPAGE)
    else:
        eff_entry = price * (1 - SLIPPAGE)
    leg_notional = PER_LEG_NOTIONAL * mult * bal_scale
    qty = leg_notional / eff_entry
    return {
        "side": side,
        "open_ts": ts,
        "open_bar_idx": None,   # filled by caller
        "entries": [(eff_entry, qty)],   # list of (price, qty)
        "avg_entry": eff_entry,
        "total_qty": qty,
        "weekend": weekend,
        "bal_at_open": balance,
        "entry_trend": None,   # set by caller from 15m
        "l1_price": eff_entry,
        "raw_l1_signal_px": price,  # pre-slip
    }


def add_dca(pos, fill_price):
    """Add L2 at fill_price (slippage applied)."""
    side = pos["side"]
    if side == "LONG":
        eff = fill_price * (1 + SLIPPAGE)
    else:
        eff = fill_price * (1 - SLIPPAGE)
    # Same notional as L1 (scaled)
    mult = WEEKEND_MULT if pos["weekend"] else 1.0
    bal_scale = pos["bal_at_open"] / INITIAL_BAL
    leg_notional = PER_LEG_NOTIONAL * mult * bal_scale
    qty = leg_notional / eff
    pos["entries"].append((eff, qty))
    pos["total_qty"] += qty
    # avg = (p1*q1 + p2*q2)/(q1+q2); but spec says simple (L1+L2)/2
    # Per spec: avg entry = (L1 + L2) / 2
    p1 = pos["entries"][0][0]
    p2 = pos["entries"][1][0]
    pos["avg_entry"] = (p1 + p2) / 2
    return pos


def close_position(pos, exit_ts, exit_px, reason):
    global balance, peak, max_dd, cooldown_until
    gross, fees, net = compute_pnl(pos["side"], pos["avg_entry"], exit_px, pos["total_qty"])
    balance += net
    if balance > peak:
        peak = balance
    dd = (peak - balance) / peak
    if dd > max_dd:
        max_dd = dd
    # Track daily pnl
    d = exit_ts.date()
    daily_pnl[d] = daily_pnl.get(d, 0.0) + net
    # Cooldown after loss
    if net < 0:
        cooldown_until = exit_ts + timedelta(minutes=COOLDOWN_MIN)
    hold_hours = (exit_ts - pos["open_ts"]).total_seconds() / 3600.0
    trades.append({
        "open_ts": pos["open_ts"],
        "close_ts": exit_ts,
        "side": pos["side"],
        "avg_entry": pos["avg_entry"],
        "exit_px": exit_px,
        "qty": pos["total_qty"],
        "legs": len(pos["entries"]),
        "gross": gross, "fees": fees, "net": net,
        "reason": reason,
        "hold_hours": hold_hours,
    })
    exit_reasons[reason] += 1


# Pre-extract numpy arrays for speed
ts_arr     = bt["timestamp"].values
open_arr   = bt["open"].values
high_arr   = bt["high"].values
low_arr    = bt["low"].values
close_arr  = bt["close"].values
rsi_arr    = bt["rsi5"].values
rsi_prev_arr = bt["rsi5"].shift(1).values
atrp_arr   = bt["atr_pct"].values
trend_arr  = bt["trend"].values
gap_arr    = bt["gap_pct"].values

# Spec says RSI <= 30 / >= 70 as the LEVEL trigger.
# A strict-spec read = level (USE_CROSSOVER=False).
# Deployed bots usually fire on first crossing only (otherwise re-fires every bar inside the zone).
# We'll show both via env var; default = level per spec literal.
import os
USE_CROSSOVER         = os.environ.get("CROSSOVER", "0") == "1"
ENTRY_ON_15M_CLOSE_ONLY = os.environ.get("GATE15M", "0") == "1"
EXITS_ON_CLOSE_ONLY    = os.environ.get("EXIT_CLOSE", "0") == "1"

n = len(bt)
print(f"Running backtest over {n} bars...")

# Pending entry: signal fired this bar, fill at NEXT bar's open
pending_entry = None  # (side, weekend, entry_trend)

for i in range(n):
    ts    = pd.Timestamp(ts_arr[i])
    o, h, l, c = open_arr[i], high_arr[i], low_arr[i], close_arr[i]
    rsi   = rsi_arr[i]
    atrp  = atrp_arr[i]
    trend = trend_arr[i]
    gap   = gap_arr[i]

    # ---- 0) Fill pending entry at this bar's OPEN ----
    if position is None and pending_entry is not None:
        side, weekend, entry_trend = pending_entry
        pending_entry = None
        position = open_position(ts, side, o, weekend)
        position["open_bar_idx"] = i
        position["entry_trend"]  = entry_trend

    # ---- 1) Manage open position ----
    if position is not None:
        side = position["side"]
        legs = len(position["entries"])
        l2_filled_this_bar = False
        # 1a) DCA L2 fill check (legs == 1)
        if legs == 1:
            l1 = position["entries"][0][0]
            if side == "LONG":
                trigger = l1 * (1 - DCA_TRIGGER)
                if l <= trigger:
                    add_dca(position, trigger)
                    legs = 2
                    l2_filled_this_bar = True
            else:
                trigger = l1 * (1 + DCA_TRIGGER)
                if h >= trigger:
                    add_dca(position, trigger)
                    legs = 2
                    l2_filled_this_bar = True

        # 1b) Compute TP & SL
        avg = position["avg_entry"]
        if legs == 1:
            tp_dist = TP_L1
            worst = position["entries"][0][0]
            if side == "LONG":
                tp_px = avg * (1 + tp_dist)
                sl_px = worst * (1 - SL_L1)
            else:
                tp_px = avg * (1 - tp_dist)
                sl_px = worst * (1 + SL_L1)
        else:
            tp_dist = TP_DCA
            if side == "LONG":
                tp_px = avg * (1 + tp_dist)
                sl_px = avg
            else:
                tp_px = avg * (1 - tp_dist)
                sl_px = avg

        exited = False
        # 1c) Check TP/SL — but if L2 just filled this bar, don't allow same-bar exit
        #     (avoids intra-bar lookahead per memory note)
        if not l2_filled_this_bar:
            if EXITS_ON_CLOSE_ONLY:
                if side == "LONG":
                    if c >= tp_px:
                        close_position(position, ts, c, "TP")
                        position = None; exited = True
                    elif c <= sl_px:
                        reason = "SL" if legs == 1 else "BE-DCA"
                        close_position(position, ts, c, reason)
                        position = None; exited = True
                else:
                    if c <= tp_px:
                        close_position(position, ts, c, "TP")
                        position = None; exited = True
                    elif c >= sl_px:
                        reason = "SL" if legs == 1 else "BE-DCA"
                        close_position(position, ts, c, reason)
                        position = None; exited = True
            else:
                if side == "LONG":
                    if h >= tp_px:
                        close_position(position, ts, tp_px, "TP")
                        position = None
                        exited = True
                    elif l <= sl_px:
                        reason = "SL" if legs == 1 else "BE-DCA"
                        close_position(position, ts, sl_px, reason)
                        position = None
                        exited = True
                else:  # SHORT
                    if l <= tp_px:
                        close_position(position, ts, tp_px, "TP")
                        position = None
                        exited = True
                    elif h >= sl_px:
                        reason = "SL" if legs == 1 else "BE-DCA"
                        close_position(position, ts, sl_px, reason)
                        position = None
                        exited = True

        # 1d) Trend-flip exit
        if not exited and position is not None and not np.isnan(trend) and trend != 0:
            entry_trend = position["entry_trend"]
            if side == "LONG" and entry_trend == 1 and trend == -1:
                close_position(position, ts, c, "TREND_FLIP")
                position = None
                exited = True
            elif side == "SHORT" and entry_trend == -1 and trend == 1:
                close_position(position, ts, c, "TREND_FLIP")
                position = None
                exited = True

        # 1e) Smart 6h Time-SL
        if not exited and position is not None:
            bars_open = i - position["open_bar_idx"]
            if bars_open >= TIME_SL_BARS:
                _g, _f, net = compute_pnl(side, position["avg_entry"], c, position["total_qty"])
                if net < 0:
                    close_position(position, ts, c, "TIME_SL")
                    position = None
                    exited = True

    # ---- 2) Look for new entry signal at bar close (fill next bar open) ----
    if position is None and pending_entry is None:
        # 15m close gate: ts.minute in {0,15,30,45} means a 15m bar just closed at ts
        if ENTRY_ON_15M_CLOSE_ONLY and (ts.minute % 15) != 0:
            continue
        # Cooldown?
        if cooldown_until is not None and ts < cooldown_until:
            continue
        # Daily stop?
        d = ts.date()
        if daily_pnl.get(d, 0.0) <= DAILY_STOP:
            continue
        if np.isnan(rsi) or np.isnan(atrp) or np.isnan(trend) or np.isnan(gap):
            continue
        if atrp > ATR_MAX_PCT:
            continue
        if gap < GAP_MIN:
            continue
        side = None
        rsi_prev = rsi_prev_arr[i]
        if USE_CROSSOVER:
            long_sig  = (rsi <= RSI_LONG)  and (not np.isnan(rsi_prev)) and (rsi_prev > RSI_LONG)
            short_sig = (rsi >= RSI_SHORT) and (not np.isnan(rsi_prev)) and (rsi_prev < RSI_SHORT)
        else:
            long_sig  = (rsi <= RSI_LONG)
            short_sig = (rsi >= RSI_SHORT)
        if long_sig and trend == 1:
            side = "LONG"
        elif short_sig and trend == -1:
            side = "SHORT"
        if side is None:
            continue
        weekend = ts.weekday() >= 5
        pending_entry = (side, weekend, int(trend))

# Close any open position at end at close price
if position is not None:
    last_ts2 = pd.Timestamp(ts_arr[-1])
    side = position["side"]
    close_position(position, last_ts2, close_arr[-1], "TIME_SL")  # mark as time-sl
    position = None


# ---- Report --------------------------------------------------------
def summarize():
    n_trades = len(trades)
    if n_trades == 0:
        print("NO TRADES")
        return
    wins   = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    n_w, n_l = len(wins), len(losses)
    wr = 100.0 * n_w / n_trades
    gross_w = sum(t["net"] for t in wins)
    gross_l = sum(t["net"] for t in losses)
    pf = (gross_w / abs(gross_l)) if gross_l < 0 else float("inf")
    net = sum(t["net"] for t in trades)
    ret_pct = 100.0 * net / INITIAL_BAL
    avg_hold = sum(t["hold_hours"] for t in trades) / n_trades

    print("\n" + "=" * 60)
    print(" PYTHON v1.1 SMART BACKTEST RESULTS (60 days)")
    print("=" * 60)
    print(f" Total trades       : {n_trades}")
    print(f" Wins / Losses      : {n_w} / {n_l}")
    print(f" Win rate           : {wr:.2f}%")
    print(f" Net profit         : ${net:,.2f}")
    print(f" Return             : {ret_pct:.2f}%")
    print(f" Max drawdown       : {max_dd*100:.2f}%")
    print(f" Profit factor      : {pf:.2f}")
    print(f" Avg hold (hours)   : {avg_hold:.2f}")
    print(f" Final balance      : ${balance:,.2f}")
    print(" Exit reasons:")
    for k, v in exit_reasons.items():
        print(f"   {k:12s}: {v}")
    print("=" * 60)
    # Comparison
    print("\n COMPARISON vs TypeScript:")
    print(f"   Trades : Py={n_trades}   TS=41    diff={n_trades-41}")
    print(f"   WR%    : Py={wr:.1f}%   TS=65.9% diff={wr-65.9:+.1f}pp")
    print(f"   Net$   : Py=${net:.2f}  TS=$931.97 diff=${net-931.97:+.2f}")
    print(f"   DD%    : Py={max_dd*100:.2f}% TS=0.46%  diff={max_dd*100-0.46:+.2f}pp")
    print(f"   PF     : Py={pf:.2f}   TS=8.48  diff={pf-8.48:+.2f}")
    print(f"   Exits  : Py TP={exit_reasons['TP']} BE={exit_reasons['BE-DCA']} TF={exit_reasons['TREND_FLIP']}")
    print(f"           TS TP=27 BE=13 TF=1")

summarize()
