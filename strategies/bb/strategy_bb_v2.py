"""
strategy_bb_v2.py — BB Confluence V2 (day trading, mean reversion)

Buy at 1H BB lower + near 4H BB lower + RSI < 30 + volume spike.
Short at 1H BB upper + near 4H BB upper + RSI > 70 + volume spike.
TP: 4H BB mid. SL: 4H BB band ± ATR. Max hold 24h. DD halt at -25%.

Separate from swing strategy. Both long AND short (mean reversion).

V2 changes from V1:
  - RSI threshold: 40 → 30 (deeper oversold/overbought, sweep-verified)
  - Max hold: none → 24h (force quick exits, prevents bag-holding)
  - Volume filter: 1.3× SMA20 required

V2 backtest: $5K → $2.21M | +238% CAGR | -12.7% DD | PF 2.40 | 688 trades | 67% WR
"""
import os, json
import numpy as np, pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))

# ─── Config ───
START_CAPITAL = 5_000.0
LEVERAGE      = 2.0
TAKER_FEE     = 0.0004
SLIPPAGE      = 0.0003

# BB
BB_PERIOD_1H  = 20
BB_STD_1H     = 2.0
BB_PERIOD_4H  = 20
BB_STD_4H     = 2.0

# Entry filters
RSI_OVERSOLD  = 30            # V2: tighter than V1's 35 (sweep: +26pp CAGR, +3pp DD improvement)
RSI_OVERBOUGHT = 70
VOL_SPIKE     = 1.3
NEAR_4H_PCT   = 0.01       # within 1% of 4H BB band

# Exit
MAX_HOLD_BARS = 24          # 24h max hold
DD_HALT_PCT   = 0.25
DD_HALT_BARS  = 168
COOLDOWN_BARS = 4


def bb(s, n=20, k=2):
    m = s.rolling(n).mean(); std = s.rolling(n).std()
    return m, m + k * std, m - k * std

def rsi_calc(s, n=14):
    d = s.diff(); g = d.clip(lower=0).rolling(n).mean(); l = -d.clip(upper=0).rolling(n).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def atr_calc(df, n=14):
    hl = df["high"] - df["low"]; hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(n).mean()

def htf_map(htf_df, htf_rule, base_ts, value_col):
    htf_df = htf_df.copy()
    htf_df["close_time"] = htf_df["timestamp"] + pd.Timedelta(htf_rule)
    s = pd.Series(htf_df[value_col].values, index=htf_df["close_time"].values)
    s = s[~s.index.duplicated(keep="last")]
    return s.reindex(s.index.union(base_ts)).sort_index().ffill().reindex(base_ts).fillna(np.nan).values


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
    df["bb_mid"], df["bb_up"], df["bb_lo"] = bb(df["close"], BB_PERIOD_1H, BB_STD_1H)
    df["rsi"] = rsi_calc(df["close"])
    df["atr"] = atr_calc(df)
    df["vol_sma"] = df["volume"].rolling(20).mean()

    # 4H BB
    df_4h["bb_mid"], df_4h["bb_up"], df_4h["bb_lo"] = bb(df_4h["close"], BB_PERIOD_4H, BB_STD_4H)

    # Map 4H to 1H
    base_ts = pd.DatetimeIndex(df["timestamp"])
    df["h4_bb_lo"] = htf_map(df_4h, "4h", base_ts, "bb_lo")
    df["h4_bb_up"] = htf_map(df_4h, "4h", base_ts, "bb_up")
    df["h4_bb_mid"] = htf_map(df_4h, "4h", base_ts, "bb_mid")

    # ─── Backtest ───
    capital = START_CAPITAL; peak = capital; max_dd = 0.0; halt_until = -1
    cooldown = 0; trades = []; yearly = {}; trade_log = []; equity_curve = []
    pos = 0; entry_price = 0.0; sl = 0.0; tp = 0.0; entry_bar = 0
    n_long = n_short = wins = losses = 0

    for i in range(100, len(df)):
        row = df.iloc[i]; price = row["close"]; ts = row["timestamp"]; yr = ts.year
        if yr not in yearly:
            yearly[yr] = {"start": capital, "end": capital, "t": [], "peak": capital, "dd": 0.0}
        yr_r = yearly[yr]

        if i < halt_until: continue
        if cooldown > 0: cooldown -= 1

        def close_trade(exit_px, reason):
            nonlocal capital, pos, cooldown, wins, losses
            pmp = ((exit_px - entry_price) / entry_price if pos == 1
                   else (entry_price - exit_px) / entry_price)
            net = pmp * LEVERAGE - (TAKER_FEE + SLIPPAGE) * 2 * LEVERAGE
            capital *= (1 + net)
            trades.append(net * 100); yr_r["t"].append(net * 100)
            if net > 0: wins += 1
            else: losses += 1
            trade_log.append({
                "entry_time": int(df.iloc[entry_bar]["timestamp"].timestamp()),
                "exit_time": int(ts.timestamp()),
                "side": "LONG" if pos == 1 else "SHORT",
                "entry_price": float(entry_price), "exit_price": float(exit_px),
                "exit_reason": reason, "pnl_pct": float(net * 100),
                "capital_after": float(capital)
            })
            pos = 0; cooldown = COOLDOWN_BARS

        # Max hold 24h
        if pos != 0 and (i - entry_bar) >= MAX_HOLD_BARS:
            close_trade(price, "TIME"); continue

        # TP / SL — check SL FIRST (conservative: assume worst fill if both hit same bar)
        if pos == 1:
            if row["low"] <= sl: close_trade(sl, "SL"); continue
            if row["high"] >= tp: close_trade(tp, "TP"); continue
        elif pos == -1:
            if row["high"] >= sl: close_trade(sl, "SL"); continue
            if row["low"] <= tp: close_trade(tp, "TP"); continue

        # Entry
        if pos == 0 and cooldown == 0:
            bb_lo = row["bb_lo"]; bb_up = row["bb_up"]
            h4_lo = row["h4_bb_lo"]; h4_up = row["h4_bb_up"]; h4_mid = row["h4_bb_mid"]
            atr = row["atr"]; rsi_v = row["rsi"]
            vol_ok = row["volume"] > VOL_SPIKE * row["vol_sma"] if not pd.isna(row["vol_sma"]) else False

            if pd.isna(bb_lo) or pd.isna(h4_lo) or pd.isna(h4_up) or pd.isna(h4_mid) or pd.isna(atr) or pd.isna(rsi_v):
                pass
            elif h4_lo <= 0 or h4_up <= 0:
                pass
            else:
                near_h4_lo = (price - h4_lo) / h4_lo < NEAR_4H_PCT
                near_h4_up = (h4_up - price) / h4_up < NEAR_4H_PCT

                # LONG: BB confluence + RSI oversold + volume
                if price <= bb_lo and near_h4_lo and rsi_v < RSI_OVERSOLD and vol_ok:
                    entry_price = price; tp = h4_mid; sl = h4_lo - atr
                    if abs(entry_price - sl) / entry_price > 0.003:
                        pos = 1; entry_bar = i; n_long += 1

                # SHORT: BB confluence + RSI overbought + volume
                elif price >= bb_up and near_h4_up and rsi_v > RSI_OVERBOUGHT and vol_ok:
                    entry_price = price; tp = h4_mid; sl = h4_up + atr
                    if abs(entry_price - sl) / entry_price > 0.003:
                        pos = -1; entry_bar = i; n_short += 1

        # DD tracking (all in decimal, not %)
        peak = max(peak, capital)
        dd_dec = (capital - peak) / peak if peak > 0 else 0
        dd_pct = dd_dec * 100
        if dd_pct < max_dd: max_dd = dd_pct
        if dd_dec <= -DD_HALT_PCT:
            halt_until = i + DD_HALT_BARS
            if pos != 0: close_trade(price, "DD-HALT")
            peak = capital

        yr_r["end"] = capital
        yr_r["peak"] = max(yr_r["peak"], capital)
        ydd = (capital - yr_r["peak"]) / yr_r["peak"] * 100 if yr_r["peak"] > 0 else 0
        if ydd < yr_r["dd"]: yr_r["dd"] = ydd

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
    print(f"  BB CONFLUENCE V2 — 4H + 1H Bollinger Band mean reversion")
    print(f"  Entry: 1H BB band + near 4H BB band + RSI < {RSI_OVERSOLD}/{RSI_OVERBOUGHT} + vol {VOL_SPIKE}×")
    print(f"  TP: 4H BB mid | SL: 4H BB band ± ATR | Max hold: {MAX_HOLD_BARS}h")
    print(f"  lev={LEVERAGE}x | DD halt at -{DD_HALT_PCT * 100:.0f}%")
    print(f"{'=' * 68}")
    print(f"  Start → Final:  ${START_CAPITAL:,.0f} → ${capital:,.2f}  ({(capital / START_CAPITAL - 1) * 100:+.1f}%)")
    print(f"  CAGR:           {cagr:+.1f}%")
    print(f"  Max drawdown:   {max_dd:+.1f}%")
    print(f"  Trades:         {n}  (~{n / yrs:.0f}/yr)  Long {n_long}  Short {n_short}")
    print(f"  Win rate:       {wr:.1f}%  ({wins}W/{losses}L)")
    print(f"  Profit factor:  {pf:.2f}")
    print(f"  Calmar:         {calmar:.2f}")

    # Exit reasons
    sl_count = sum(1 for t in trade_log if t["exit_reason"] == "SL")
    tp_count = sum(1 for t in trade_log if t["exit_reason"] == "TP")
    time_count = sum(1 for t in trade_log if t["exit_reason"] == "TIME")
    dd_count = sum(1 for t in trade_log if t["exit_reason"] == "DD-HALT")
    print(f"  Exits:          TP={tp_count}  SL={sl_count}  TIME={time_count}  DD-HALT={dd_count}")

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
