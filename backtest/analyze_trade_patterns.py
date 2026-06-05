"""Mine the rsiscalp_trend backtest trade log for patterns.

Goal: instead of testing speculative improvements, look at WHAT actually
differentiates winning from losing trades in the 29-month OOS sample. Then
we can build filters around real observed patterns rather than theory.

For each trade captures features at ENTRY bar:
  - hour of day (UTC), day of week
  - RSI at entry
  - volume / SMA(20) ratio
  - BB width (%)
  - BBW change vs 1 hour ago (expanding or contracting?)
  - ATR%
  - distance from EMA200 (price's position vs trend)
  - distance from opposite BB band (how stretched into extreme?)
  - 15m trend direction (matches or against?)

Then aggregates: WR + avg PnL by feature bucket.

Conservative: same fill model as rsiscalp_backtest.py (SL-wins-ties intrabar,
0.04% commission, no slippage).
"""
from __future__ import annotations
import os
from datetime import datetime
import numpy as np
import pandas as pd

CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "cache")

# Match live rsiscalp_trend config exactly
INITIAL    = 5000.0
RISK_PCT   = 0.005
LEVERAGE   = 3.0
COMMISSION = 0.0004
RSI_PERIOD = 9
RSI_OS, RSI_OB = 30, 70
DCA_SPACING = 0.005
DCA_LEVELS  = 2
TP_SINGLE   = 0.005
TP_DCA      = 0.0025
SL_FROM_WORST = 0.01
BREAKER_BARS = 3   # 1-loss / 15-min


def rsi_series(closes, n=9):
    s = pd.Series(closes)
    d = s.diff()
    g = d.where(d > 0, 0.0).ewm(alpha=1/n, adjust=False).mean()
    l = (-d.where(d < 0, 0.0)).ewm(alpha=1/n, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    return (100 - 100/(1+rs)).to_numpy()


def main():
    print("Loading 5m data...")
    df = pd.read_csv(os.path.join(CACHE, "BTCUSDT_5m.csv"), parse_dates=["timestamp"])
    df = df[df.timestamp >= "2024-01-01"].reset_index(drop=True)
    print(f"  → {len(df):,} bars ({df.timestamp.iloc[0]} → {df.timestamp.iloc[-1]})")

    # Indicators
    print("Computing indicators...")
    df["rsi"] = rsi_series(df.close, RSI_PERIOD)
    df["ema200"] = df.close.ewm(span=200, adjust=False).mean()
    mid = df.close.rolling(20).mean()
    sd  = df.close.rolling(20).std(ddof=0)
    df["bb_low"] = mid - 2*sd
    df["bb_up"]  = mid + 2*sd
    df["bb_mid"] = mid
    df["bbw"]    = (4*sd) / mid
    df["vol_sma20"] = df.volume.rolling(20).mean()
    tr = pd.concat([df.high - df.low,
                    (df.high - df.close.shift()).abs(),
                    (df.low  - df.close.shift()).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.ewm(alpha=1/14, adjust=False).mean() / df.close
    # 15m trend gate (matches live bot)
    d15 = df.set_index("timestamp")["close"].resample("15min").last().dropna()
    e20 = d15.ewm(span=20, adjust=False).mean()
    e50 = d15.ewm(span=50, adjust=False).mean()
    df["trend_up"] = (e20 > e50).reindex(df.timestamp, method="ffill").values

    # Backtest run (capturing entry features)
    print("Running backtest...")
    c, h, l, o, v = df.close.values, df.high.values, df.low.values, df.open.values, df.volume.values
    rsi = df.rsi.values; ema200 = df.ema200.values
    bb_low, bb_up, bb_mid, bbw = df.bb_low.values, df.bb_up.values, df.bb_mid.values, df.bbw.values
    vol_sma = df.vol_sma20.values; atr_pct = df.atr_pct.values
    trend_up = df.trend_up.values
    ts = df.timestamp.values

    bal = INITIAL
    pos = None
    pause_until = -1
    trades = []

    def per_leg_qty(b, p): return (b * 0.95 * LEVERAGE / p) / DCA_LEVELS

    for i in range(220, len(df)):
        if np.isnan(rsi[i]): continue

        # Manage open position
        if pos:
            side = pos["side"]
            # DCA check
            if pos["filled"] < DCA_LEVELS:
                trig = pos["worst"] * (1 - DCA_SPACING) if side == "LONG" else pos["worst"] * (1 + DCA_SPACING)
                hit = (side == "LONG" and l[i] <= trig) or (side == "SHORT" and h[i] >= trig)
                if hit:
                    q = per_leg_qty(bal, trig)
                    bal -= trig * q * COMMISSION
                    pos["entries"].append((trig, q))
                    pos["worst"] = min(pos["worst"], trig) if side == "LONG" else max(pos["worst"], trig)
                    pos["qty"] = sum(e[1] for e in pos["entries"])
                    pos["filled"] += 1
            avg = sum(p*q for p, q in pos["entries"]) / pos["qty"]
            slp = pos["worst"] * (1 - SL_FROM_WORST) if side == "LONG" else pos["worst"] * (1 + SL_FROM_WORST)
            tp_pct = TP_SINGLE if pos["filled"] <= 1 else TP_DCA
            tpp = avg * (1 + tp_pct) if side == "LONG" else avg * (1 - tp_pct)

            sl_hit = (side == "LONG" and l[i] <= slp) or (side == "SHORT" and h[i] >= slp)
            tp_hit = (side == "LONG" and h[i] >= tpp) or (side == "SHORT" and l[i] <= tpp)
            if sl_hit:
                exit_px, reason = slp, "SL"
            elif tp_hit:
                exit_px, reason = tpp, "TP"
            else:
                continue

            # Booked exit
            gross = (exit_px - avg) * pos["qty"] if side == "LONG" else (avg - exit_px) * pos["qty"]
            fees = (avg + exit_px) * pos["qty"] * COMMISSION
            net = gross - fees
            bal_before = bal
            bal += net
            trades.append({
                **pos["features"],   # entry-bar features captured at open
                "side": side,
                "filled_legs": pos["filled"],
                "exit_reason": reason,
                "pnl_usd": net,
                "pnl_pct": net / bal_before * 100,
                "won": net > 0,
                "bars_held": i - pos["entry_i"],
            })
            if net <= 0: pause_until = i + BREAKER_BARS
            pos = None
            continue

        # Entry check
        if i < pause_until: continue
        if rsi[i] <= RSI_OS:
            sig = "LONG"
            if not trend_up[i]: continue
        elif rsi[i] >= RSI_OB:
            sig = "SHORT"
            if trend_up[i]: continue
        else:
            continue

        if i + 1 >= len(df): break
        entry_px = o[i+1]
        q = per_leg_qty(bal, entry_px)
        bal -= entry_px * q * COMMISSION

        # ENTRY FEATURES — captured at the entry bar (i)
        hour = pd.Timestamp(ts[i]).hour
        dow  = pd.Timestamp(ts[i]).dayofweek  # 0=Mon ... 6=Sun
        vol_ratio = v[i] / vol_sma[i] if vol_sma[i] > 0 else 1.0
        bbw_pct = bbw[i] * 100 if not np.isnan(bbw[i]) else 0
        bbw_change_1h = ((bbw[i] / bbw[i-12]) - 1) * 100 if i >= 12 and bbw[i-12] > 0 else 0
        atr_pct_val = atr_pct[i] * 100 if not np.isnan(atr_pct[i]) else 0
        dist_ema200_pct = ((c[i] - ema200[i]) / ema200[i]) * 100 if not np.isnan(ema200[i]) else 0
        # distance from the BAND we'd potentially exit toward
        if sig == "LONG":
            dist_opp_band = ((bb_up[i] - c[i]) / c[i]) * 100 if not np.isnan(bb_up[i]) else 0
        else:
            dist_opp_band = ((c[i] - bb_low[i]) / c[i]) * 100 if not np.isnan(bb_low[i]) else 0

        pos = {
            "side": sig, "entries": [(entry_px, q)], "qty": q, "worst": entry_px,
            "filled": 1, "entry_i": i+1,
            "features": {
                "entry_time": pd.Timestamp(ts[i]).isoformat(),
                "rsi_entry": rsi[i],
                "vol_ratio": vol_ratio,
                "bbw_pct": bbw_pct,
                "bbw_change_1h_pct": bbw_change_1h,
                "atr_pct": atr_pct_val,
                "dist_ema200_pct": dist_ema200_pct,
                "dist_opp_band_pct": dist_opp_band,
                "hour_utc": hour,
                "day_of_week": dow,
            },
        }

    df_tr = pd.DataFrame(trades)
    print(f"\n{len(df_tr):,} trades captured")
    print(f"  Wins: {df_tr.won.sum()} ({df_tr.won.mean()*100:.1f}%)")
    print(f"  Net: ${df_tr.pnl_usd.sum():.2f}  (final bal ~${INITIAL + df_tr.pnl_usd.sum():.2f})")

    # Save raw trades
    out_csv = os.path.join(CACHE, "..", "rsiscalp_trades_features.csv")
    df_tr.to_csv(out_csv, index=False)
    print(f"  → saved: {out_csv}\n")

    # ── Pattern analysis ──
    def bucket(name, feature, edges, df=df_tr):
        print(f"\n══════ {name} ══════")
        df = df.copy()
        df["bucket"] = pd.cut(df[feature], bins=edges, include_lowest=True)
        g = df.groupby("bucket", observed=True).agg(
            n=("won", "size"),
            wr=("won", "mean"),
            avg_pnl=("pnl_usd", "mean"),
            sum_pnl=("pnl_usd", "sum"),
            avg_win=("pnl_usd", lambda s: s[s > 0].mean() if (s > 0).any() else 0),
            avg_loss=("pnl_usd", lambda s: s[s <= 0].mean() if (s <= 0).any() else 0),
        )
        g["wr"] = (g["wr"] * 100).round(1)
        g["avg_pnl"] = g["avg_pnl"].round(2)
        g["sum_pnl"] = g["sum_pnl"].round(0)
        g["avg_win"] = g["avg_win"].round(2)
        g["avg_loss"] = g["avg_loss"].round(2)
        print(g.to_string())

    # ── Vol ratio ──
    bucket("Volume / SMA(20) at entry",
           "vol_ratio",
           [0, 0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 3.0, 100])

    # ── BBW % ──
    bucket("BB Width % at entry",
           "bbw_pct",
           [0, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0, 100])

    # ── BBW 1h change ──
    bucket("BBW change vs 1h ago (% — positive = expanding)",
           "bbw_change_1h_pct",
           [-100, -30, -10, 0, 10, 30, 60, 100, 500])

    # ── ATR % ──
    bucket("ATR % at entry",
           "atr_pct",
           [0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.50, 100])

    # ── Distance from EMA200 ──
    bucket("Distance from EMA200 (%, signed)",
           "dist_ema200_pct",
           [-100, -3, -1, -0.3, 0, 0.3, 1, 3, 100])

    # ── Distance from opposite BB band ──
    bucket("Distance from opposite BB band (%, smaller = more stretched)",
           "dist_opp_band_pct",
           [-5, 0, 0.5, 1.0, 1.5, 2.0, 3.0, 100])

    # ── Hour of day ──
    print("\n══════ Hour of day (UTC) ══════")
    g = df_tr.groupby("hour_utc").agg(n=("won", "size"), wr=("won", "mean"), sum_pnl=("pnl_usd", "sum"))
    g["wr"] = (g["wr"] * 100).round(1)
    g["sum_pnl"] = g["sum_pnl"].round(0)
    print(g.to_string())

    # ── Day of week ──
    print("\n══════ Day of week (0=Mon, 6=Sun) ══════")
    g = df_tr.groupby("day_of_week").agg(n=("won", "size"), wr=("won", "mean"), sum_pnl=("pnl_usd", "sum"))
    g["wr"] = (g["wr"] * 100).round(1)
    g["sum_pnl"] = g["sum_pnl"].round(0)
    print(g.to_string())

    # ── DCA vs no-DCA outcomes ──
    print("\n══════ DCA L2 fill status ══════")
    g = df_tr.groupby("filled_legs").agg(n=("won", "size"), wr=("won", "mean"),
                                          sum_pnl=("pnl_usd", "sum"),
                                          avg_pnl=("pnl_usd", "mean"))
    g["wr"] = (g["wr"] * 100).round(1)
    print(g.to_string())

    # ── Worst 10 losses — what were their features? ──
    print("\n══════ 10 WORST losing trades (and their entry features) ══════")
    worst = df_tr.nsmallest(10, "pnl_usd")[["entry_time", "side", "filled_legs", "exit_reason",
                                              "pnl_usd", "vol_ratio", "bbw_pct", "bbw_change_1h_pct",
                                              "atr_pct", "dist_ema200_pct"]]
    print(worst.to_string(index=False))


if __name__ == "__main__":
    main()
