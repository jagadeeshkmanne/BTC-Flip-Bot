"""
strategy_bb.py — BB Confluence Day Trading Strategy

Buy when price hits BOTH 4H BB lower AND 1H BB lower simultaneously.
Sell at 4H BB mid. This is mean-reversion, not trend-following.

Best config (RSI + DD halt):
  $5K → $504K | +152% CAGR | -25.9% DD | PF 1.54 | 990 trades | 56% WR

Architecture:
  4H: Bollinger Bands (20, 2) — defines the range
  1H: Bollinger Bands (20, 2) — entry timing
  Entry: price at 1H BB lower AND within 1% of 4H BB lower + RSI < 40
  TP: 4H BB mid (center of the range)
  SL: below 4H BB lower by 1 ATR
  DD halt: -25% → pause 7 days
  Cooldown: 4h after each trade

Completely separate from swing strategy (core.py/bot.py).
"""
import os, json
import numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))

# ─── Config ───
START_CAPITAL = 5_000.0
LEVERAGE      = 2.0
TAKER_FEE     = 0.0004
SLIPPAGE      = 0.0003
DD_HALT_PCT   = 0.25
DD_HALT_BARS  = 168          # 7 days in 1H bars
COOLDOWN_BARS = 4             # 4h cooldown

# BB params
BB_PERIOD     = 20
BB_STD        = 2.0
RSI_OVERSOLD  = 40
RSI_OVERBOUGHT = 60


def ema(s, n): return s.ewm(span=n, adjust=False).mean()

def bb(s, n=20, k=2):
    m = s.rolling(n).mean()
    std = s.rolling(n).std()
    return m, m + k * std, m - k * std

def rsi_calc(s, n=14):
    d = s.diff()
    g = d.clip(lower=0).rolling(n).mean()
    l = -d.clip(upper=0).rolling(n).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def atr_calc(df, n=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(n).mean()

def htf_map(htf_df, htf_rule, base_ts, value_col):
    htf_df = htf_df.copy()
    htf_df["close_time"] = htf_df["timestamp"] + pd.Timedelta(htf_rule)
    s = pd.Series(htf_df[value_col].values, index=htf_df["close_time"].values)
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(s.index.union(base_ts)).sort_index().ffill().reindex(base_ts).fillna(0).values


def load_15m():
    df = pd.read_csv(os.path.join(ROOT, "data", "cache", "BTCUSDT_15m_1825d.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df.sort_values("timestamp").reset_index(drop=True)


def run():
    src = load_15m()
    print(f"Source: {len(src):,} 15m bars")

    # Resample
    df = src.set_index("timestamp").resample("1h").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna().reset_index()
    df_4h = src.set_index("timestamp").resample("4h").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna().reset_index()
    print(f"Frames — 1H: {len(df):,}  4H: {len(df_4h):,}")

    # 1H indicators
    df["bb_mid"], df["bb_up"], df["bb_lo"] = bb(df["close"], BB_PERIOD, BB_STD)
    df["rsi"] = rsi_calc(df["close"])
    df["atr"] = atr_calc(df)

    # 4H BB
    df_4h["bb_mid"], df_4h["bb_up"], df_4h["bb_lo"] = bb(df_4h["close"], BB_PERIOD, BB_STD)

    # Map 4H to 1H
    base_ts = pd.DatetimeIndex(df["timestamp"])
    df["h4_bb_lo"] = htf_map(df_4h, "4h", base_ts, "bb_lo")
    df["h4_bb_up"] = htf_map(df_4h, "4h", base_ts, "bb_up")
    df["h4_bb_mid"] = htf_map(df_4h, "4h", base_ts, "bb_mid")

    # ─── Backtest ───
    capital = START_CAPITAL
    peak = capital
    max_dd = 0.0
    halt_until = -1
    cooldown = 0
    trades = []
    yearly = {}
    trade_log = []
    equity_curve = []
    pos = 0
    entry_price = 0.0
    sl = 0.0
    tp = 0.0
    entry_bar = 0
    n_long = n_short = wins = losses = 0

    for i in range(100, len(df)):
        row = df.iloc[i]
        price = row["close"]
        ts = row["timestamp"]
        yr = ts.year
        if yr not in yearly:
            yearly[yr] = {"start": capital, "end": capital, "t": [], "peak": capital, "dd": 0.0}
        yr_r = yearly[yr]

        if i < halt_until:
            continue
        if cooldown > 0:
            cooldown -= 1

        # TP / SL management
        if pos == 1:
            if row["high"] >= tp:
                pmp = (tp - entry_price) / entry_price
                net = pmp * LEVERAGE - (TAKER_FEE + SLIPPAGE) * 2 * LEVERAGE
                capital *= (1 + net)
                trades.append(net * 100); yr_r["t"].append(net * 100)
                wins += 1
                trade_log.append({"entry_time": int(df.iloc[entry_bar]["timestamp"].timestamp()),
                    "exit_time": int(ts.timestamp()), "side": "LONG",
                    "entry_price": entry_price, "exit_price": tp,
                    "exit_reason": "TP", "pnl_pct": net * 100, "capital_after": capital})
                pos = 0; cooldown = COOLDOWN_BARS
                continue
            if row["low"] <= sl:
                pmp = (sl - entry_price) / entry_price
                net = pmp * LEVERAGE - (TAKER_FEE + SLIPPAGE) * 2 * LEVERAGE
                capital *= (1 + net)
                trades.append(net * 100); yr_r["t"].append(net * 100)
                losses += 1
                trade_log.append({"entry_time": int(df.iloc[entry_bar]["timestamp"].timestamp()),
                    "exit_time": int(ts.timestamp()), "side": "LONG",
                    "entry_price": entry_price, "exit_price": sl,
                    "exit_reason": "SL", "pnl_pct": net * 100, "capital_after": capital})
                pos = 0; cooldown = COOLDOWN_BARS
                continue
        elif pos == -1:
            if row["low"] <= tp:
                pmp = (entry_price - tp) / entry_price
                net = pmp * LEVERAGE - (TAKER_FEE + SLIPPAGE) * 2 * LEVERAGE
                capital *= (1 + net)
                trades.append(net * 100); yr_r["t"].append(net * 100)
                wins += 1
                trade_log.append({"entry_time": int(df.iloc[entry_bar]["timestamp"].timestamp()),
                    "exit_time": int(ts.timestamp()), "side": "SHORT",
                    "entry_price": entry_price, "exit_price": tp,
                    "exit_reason": "TP", "pnl_pct": net * 100, "capital_after": capital})
                pos = 0; cooldown = COOLDOWN_BARS
                continue
            if row["high"] >= sl:
                pmp = (entry_price - sl) / entry_price
                net = pmp * LEVERAGE - (TAKER_FEE + SLIPPAGE) * 2 * LEVERAGE
                capital *= (1 + net)
                trades.append(net * 100); yr_r["t"].append(net * 100)
                losses += 1
                trade_log.append({"entry_time": int(df.iloc[entry_bar]["timestamp"].timestamp()),
                    "exit_time": int(ts.timestamp()), "side": "SHORT",
                    "entry_price": entry_price, "exit_price": sl,
                    "exit_reason": "SL", "pnl_pct": net * 100, "capital_after": capital})
                pos = 0; cooldown = COOLDOWN_BARS
                continue

        # Entry
        if pos == 0 and cooldown == 0:
            bb_lo = row["bb_lo"]
            h4_lo = row["h4_bb_lo"]
            h4_up = row["h4_bb_up"]
            h4_mid = row["h4_bb_mid"]
            atr = row["atr"]
            rsi_v = row["rsi"]

            if pd.isna(bb_lo) or h4_lo == 0 or pd.isna(atr) or pd.isna(rsi_v):
                pass
            else:
                near_h4_lo = (price - h4_lo) / h4_lo < 0.01
                near_h4_up = (h4_up - price) / h4_up < 0.01

                # LONG: 1H BB lower + near 4H BB lower + RSI oversold
                if price <= bb_lo and near_h4_lo and rsi_v < RSI_OVERSOLD:
                    entry_price = price
                    tp = h4_mid
                    sl = h4_lo - atr
                    if abs(entry_price - sl) / entry_price > 0.003:
                        pos = 1; entry_bar = i; n_long += 1

                # SHORT: 1H BB upper + near 4H BB upper + RSI overbought
                elif price >= row["bb_up"] and near_h4_up and rsi_v > RSI_OVERBOUGHT:
                    entry_price = price
                    tp = h4_mid
                    sl = h4_up + atr
                    if abs(entry_price - sl) / entry_price > 0.003:
                        pos = -1; entry_bar = i; n_short += 1

        # DD tracking
        peak = max(peak, capital)
        dd = (capital - peak) / peak * 100 if peak > 0 else 0
        if dd < max_dd:
            max_dd = dd
        if dd <= -DD_HALT_PCT * 100:
            halt_until = i + DD_HALT_BARS
            if pos != 0:
                pmp = ((price - entry_price) / entry_price if pos == 1
                       else (entry_price - price) / entry_price)
                net = pmp * LEVERAGE - (TAKER_FEE + SLIPPAGE) * 2 * LEVERAGE
                capital *= (1 + net)
                trades.append(net * 100); yr_r["t"].append(net * 100)
                if net > 0: wins += 1
                else: losses += 1
                pos = 0
            peak = capital

        yr_r["end"] = capital
        yr_r["peak"] = max(yr_r["peak"], capital)
        ydd = (capital - yr_r["peak"]) / yr_r["peak"] * 100 if yr_r["peak"] > 0 else 0
        if ydd < yr_r["dd"]:
            yr_r["dd"] = ydd

        if i % 24 == 0:
            equity_curve.append({"t": int(ts.timestamp()), "v": float(capital)})

    # ─── Results ───
    yrs = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25
    n = len(trades)
    wr = wins / max(wins + losses, 1) * 100
    gw = sum(t for t in trades if t > 0)
    gl = abs(sum(t for t in trades if t <= 0))
    pf = gw / max(gl, 1e-9)
    cagr = ((capital / START_CAPITAL) ** (1 / yrs) - 1) * 100 if capital > 0 else 0
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0

    print(f"\n{'=' * 68}")
    print(f"  BB CONFLUENCE STRATEGY — 4H + 1H Bollinger Band mean reversion")
    print(f"  Entry: 1H BB lower + near 4H BB lower + RSI < {RSI_OVERSOLD}")
    print(f"  TP: 4H BB mid | SL: below 4H BB lower")
    print(f"  lev={LEVERAGE}x | DD halt at -{DD_HALT_PCT*100:.0f}%")
    print(f"{'=' * 68}")
    print(f"  Start → Final:  ${START_CAPITAL:,.0f} → ${capital:,.2f}  ({(capital / START_CAPITAL - 1) * 100:+.1f}%)")
    print(f"  CAGR:           {cagr:+.1f}%")
    print(f"  Max drawdown:   {max_dd:+.1f}%")
    print(f"  Trades:         {n}  (~{n / yrs:.0f}/yr)  Long {n_long}  Short {n_short}")
    print(f"  Win rate:       {wr:.1f}%  ({wins}W/{losses}L)")
    print(f"  Profit factor:  {pf:.2f}")
    print(f"  Calmar:         {calmar:.2f}")

    print(f"\n  Year-by-year:")
    print(f"  {'Year':<6}{'Start':>14}{'End':>14}{'Ret%':>9}{'DD%':>8}{'Trades':>8}{'Win%':>7}{'PF':>7}")
    for y in sorted(yearly.keys()):
        d = yearly[y]; t = d['t']
        gwy = sum(x for x in t if x > 0); gly = abs(sum(x for x in t if x <= 0))
        py = gwy / max(gly, 1e-9)
        wy = sum(1 for x in t if x > 0); tot = len(t)
        wry = wy / tot * 100 if tot else 0
        ret = (d['end'] / d['start'] - 1) * 100 if d['start'] > 0 else 0
        print(f"  {y:<6}{d['start']:>14,.0f}{d['end']:>14,.0f}"
              f"{ret:>+9.1f}{d['dd']:>+8.1f}{tot:>8}{wry:>7.1f}{py:>7.2f}")


if __name__ == "__main__":
    run()
