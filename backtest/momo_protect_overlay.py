#!/usr/bin/env python3
"""momo_protect_overlay.py — can protective exits improve live momo_v1?

Goal (user 2026-06-12): cut losses / lock profits on the live bots. rsiscalp
is closed (9 exit families, all dead). momo_v1 has a real validated edge with
maxDD ~21% — overlays tested here, honestly:

  TRAIL x%   : exit when close < peak close since entry × (1−x%)
  ATR k      : chandelier — exit when close < peak close − k×ATR20
  SL y%      : hard stop — exit when close < entry × (1−y%)
  RATCHET a/b: once peak gain ≥ a%, exit if gain falls back to b%

Re-entry after a protective exit (the part that usually kills trails):
  R1 = immediate (momo signal still on → back in next day)
  R2 = fresh-trigger (need a NEW RSI14>70 day after the exit, plus close>SMA200)

Engine: decisions on closed daily bars, fills next open, 0.10% fee + 0.02%
slip per side, MTM drawdown on daily lows. IS (≤2023) / OOS (2024+) split
reported separately — an overlay must beat baseline on BOTH to count.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1d.csv"
FEE, SLIP = 0.0010, 0.0002
SMA, RSIL, TRIG, HOLD = 200, 14, 70.0, 7
SPLIT = pd.Timestamp("2024-01-01")


def wilder_rsi(c, n=14):
    d = c.diff()
    ag = d.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return 100 - 100/(1 + ag/al)


def load():
    df = pd.read_csv(CSV)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["sma"] = df["close"].rolling(SMA).mean()
    df["rsi"] = wilder_rsi(df["close"], RSIL)
    df["sig"] = (df["close"] > df["sma"]) & (df["rsi"].rolling(HOLD).max() > TRIG)
    df["rsi_hot"] = df["rsi"] > TRIG
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(20).mean()
    return df


def run(df, trail=None, atr_k=None, sl=None, ratchet=None, reentry="R2"):
    eq = 1.0
    pos = None
    last_hot_i = -1          # index of last closed bar with RSI>70
    blocked_until_hot_after = -1   # after a protective exit (R2): need hot bar > this idx
    trades = []
    rows = []
    O, H, L, C = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    SIGv, HOTv, ATRv = df["sig"].values, df["rsi_hot"].values, df["atr"].values
    TS = df["timestamp"].values

    for i in range(SMA + 1, len(df)):
        p = i - 1                       # last CLOSED bar drives the decision
        if HOTv[p]:
            last_hot_i = p

        if pos is None:
            ok = bool(SIGv[p])
            if ok and reentry == "R2" and blocked_until_hot_after >= 0:
                ok = last_hot_i > blocked_until_hot_after
            if ok:
                fill = O[i] * (1 + SLIP)
                qty = eq * (1 - FEE) / fill
                pos = {"qty": qty, "entry": fill, "cost": eq, "peak": C[p],
                       "entry_ts": TS[i]}
                blocked_until_hot_after = -1
        else:
            pos["peak"] = max(pos["peak"], C[p])
            gain_pct = (C[p] / pos["entry"] - 1) * 100
            peak_gain = (pos["peak"] / pos["entry"] - 1) * 100
            reason = None
            if not SIGv[p]:
                reason = "MOMO"                       # baseline exit
            elif trail is not None and C[p] < pos["peak"] * (1 - trail / 100):
                reason = "TRAIL"
            elif atr_k is not None and C[p] < pos["peak"] - atr_k * ATRv[p]:
                reason = "ATR"
            elif sl is not None and C[p] < pos["entry"] * (1 - sl / 100):
                reason = "SL"
            elif ratchet is not None and peak_gain >= ratchet[0] and gain_pct <= ratchet[1]:
                reason = "RATCHET"
            if reason:
                fill = O[i] * (1 - SLIP)
                eq = pos["qty"] * fill * (1 - FEE)
                trades.append({"net_pct": (eq / pos["cost"] - 1) * 100,
                               "exit_ts": TS[i], "reason": reason})
                pos = None
                if reason != "MOMO":
                    blocked_until_hot_after = p       # R2: need fresh hot bar
        cur = pos["qty"] * C[i] * (1 - FEE - SLIP) if pos else eq
        low = pos["qty"] * L[i] * (1 - FEE - SLIP) if pos else eq
        rows.append((TS[i], cur, low))
    return trades, pd.DataFrame(rows, columns=["ts", "eq", "eq_low"])


def stats(trades, curve, lo, hi):
    m = (curve["ts"] >= lo) & (curve["ts"] < hi)
    c = curve[m]
    if len(c) < 2:
        return "n/a"
    ret = c["eq"].iloc[-1] / c["eq"].iloc[0] - 1
    peak = c["eq"].cummax()
    dd = ((peak - c["eq_low"]) / peak).max()
    return ret * 100, dd * 100


def main():
    df = load()
    print(f"Data: {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()} "
          f"({len(df)} daily bars) | first tradable {df['timestamp'].iloc[SMA+1].date()}")
    VARIANTS = [
        ("baseline momo_v1",        dict()),
        ("trail 10% R1",            dict(trail=10, reentry="R1")),
        ("trail 10% R2",            dict(trail=10)),
        ("trail 15% R2",            dict(trail=15)),
        ("trail 20% R2",            dict(trail=20)),
        ("ATR 2x R2",               dict(atr_k=2)),
        ("ATR 3x R2",               dict(atr_k=3)),
        ("ATR 4x R2",               dict(atr_k=4)),
        ("SL 5% R2",                dict(sl=5)),
        ("SL 8% R2",                dict(sl=8)),
        ("SL 10% R2",               dict(sl=10)),
        ("ratchet 20/10 R2",        dict(ratchet=(20, 10))),
        ("ratchet 10/2 R2",         dict(ratchet=(10, 2))),
        ("SL8 + ATR3 R2",           dict(sl=8, atr_k=3)),
        ("SL8 + trail15 R2",        dict(sl=8, trail=15)),
    ]
    lo, hi = df["timestamp"].iloc[SMA + 1], df["timestamp"].iloc[-1] + pd.Timedelta(days=1)
    print(f"\n{'variant':<22}{'trades':>7}{'win%':>6}{'TOTAL%':>9}{'maxDD%':>8}"
          f"{'IS%':>8}{'ISDD%':>7}{'OOS%':>8}{'OOSDD%':>7}  worst trades")
    for name, kw in VARIANTS:
        trades, curve = run(df, **kw)
        tot, dd = stats(trades, curve, lo, hi)
        is_r, is_dd = stats(trades, curve, lo, SPLIT)
        oos_r, oos_dd = stats(trades, curve, SPLIT, hi)
        nets = [t["net_pct"] for t in trades]
        w = sum(1 for x in nets if x > 0)
        worst = sorted(nets)[:3]
        print(f"{name:<22}{len(nets):>7}{w/max(len(nets),1)*100:>6.0f}{tot:>9.1f}{dd:>8.1f}"
              f"{is_r:>8.1f}{is_dd:>7.1f}{oos_r:>8.1f}{oos_dd:>7.1f}  "
              + " ".join(f"{x:+.1f}" for x in worst))


if __name__ == "__main__":
    main()
