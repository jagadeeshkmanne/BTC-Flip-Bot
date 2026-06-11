"""User 2026-06-12: 'rarely trades but BTC volatility triggers the SL.'
Tests whether volatility-adaptive exits beat the Donchian trail:
  donch_half : prior (N/2)-day extreme trail (the reconstruction baseline)
  donch_full : prior N-day extreme trail (wider — fewer whipsaws, more giveback)
  chand_2.5  : chandelier 2.5*ATR(14) from peak close, ratcheting
  chand_3.5  : chandelier 3.5*ATR(14) from peak close, ratcheting
Entries identical (EMA50 bias + N-day structure break + SL-flip). REAL fees.
Also reports whipsaw rate: % of losers stopped within 5 bars of entry.
"""
import pandas as pd
import numpy as np

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1d.csv"
INITIAL = 5000.0
FEE, SLIP = 0.00055, 0.0002


def run(df, N, exit_mode):
    ema50 = df["close"].ewm(span=50, adjust=False).mean().values
    hi_n = df["high"].rolling(N).max().shift(1).values
    lo_n = df["low"].rolling(N).min().shift(1).values
    M = max(N // 2, 2)
    ex_lo_h = df["low"].rolling(M).min().shift(1).values
    ex_hi_h = df["high"].rolling(M).max().shift(1).values
    ex_lo_f = df["low"].rolling(N).min().shift(1).values
    ex_hi_f = df["high"].rolling(N).max().shift(1).values
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().shift(1).values
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values

    k = {"chand_2.5": 2.5, "chand_3.5": 3.5}.get(exit_mode)
    bal = INITIAL; pos = None; pend = None
    peak_mtm = INITIAL; max_dd = 0.0
    trades = []

    for i in range(len(df)):
        if np.isnan(hi_n[i]) or np.isnan(ema50[i]) or np.isnan(atr[i]):
            continue
        if pos is None and pend is not None:
            side = pend; pend = None
            eff = o[i] * (1 + SLIP) if side == "L" else o[i] * (1 - SLIP)
            qty = bal / eff
            bal -= eff * qty * FEE
            if exit_mode == "donch_half":
                stop = ex_lo_h[i] if side == "L" else ex_hi_h[i]
            elif exit_mode == "donch_full":
                stop = ex_lo_f[i] if side == "L" else ex_hi_f[i]
            else:
                stop = eff - k * atr[i] if side == "L" else eff + k * atr[i]
            pos = {"side": side, "qty": qty, "avg": eff, "stop": stop,
                   "bar": i, "peak": eff}

        if pos is not None:
            if exit_mode == "donch_half":
                new = ex_lo_h[i] if pos["side"] == "L" else ex_hi_h[i]
            elif exit_mode == "donch_full":
                new = ex_lo_f[i] if pos["side"] == "L" else ex_hi_f[i]
            else:
                pos["peak"] = (max(pos["peak"], c[i]) if pos["side"] == "L"
                               else min(pos["peak"], c[i]))
                new = (pos["peak"] - k * atr[i] if pos["side"] == "L"
                       else pos["peak"] + k * atr[i])
            pos["stop"] = max(pos["stop"], new) if pos["side"] == "L" else min(pos["stop"], new)

            hit = l[i] <= pos["stop"] if pos["side"] == "L" else h[i] >= pos["stop"]
            if hit:
                fill = min(pos["stop"], o[i]) if pos["side"] == "L" else max(pos["stop"], o[i])
                eff = fill * (1 - SLIP) if pos["side"] == "L" else fill * (1 + SLIP)
                gross = ((eff - pos["avg"]) * pos["qty"] if pos["side"] == "L"
                         else (pos["avg"] - eff) * pos["qty"])
                bal += gross - eff * pos["qty"] * FEE
                trades.append({"net": gross, "bars": i - pos["bar"]})
                old = pos["side"]; pos = None
                if old == "L" and c[i] < ema50[i]:
                    pend = "S"
                elif old == "S" and c[i] > ema50[i]:
                    pend = "L"
            else:
                adv = l[i] if pos["side"] == "L" else h[i]
                unreal = ((adv - pos["avg"]) * pos["qty"] if pos["side"] == "L"
                          else (pos["avg"] - adv) * pos["qty"])
                fav = h[i] if pos["side"] == "L" else l[i]
                ufav = ((fav - pos["avg"]) * pos["qty"] if pos["side"] == "L"
                        else (pos["avg"] - fav) * pos["qty"])
                peak_mtm = max(peak_mtm, bal + ufav)
                max_dd = max(max_dd, (peak_mtm - (bal + unreal)) / peak_mtm)
        else:
            peak_mtm = max(peak_mtm, bal)
            max_dd = max(max_dd, (peak_mtm - bal) / peak_mtm)

        if pos is None and pend is None:
            if c[i] > ema50[i] and c[i] > hi_n[i]:
                pend = "L"
            elif c[i] < ema50[i] and c[i] < lo_n[i]:
                pend = "S"

    wins = [t for t in trades if t["net"] > 0]
    losers = [t for t in trades if t["net"] <= 0]
    whip = sum(1 for t in losers if t["bars"] <= 5)
    gw = sum(t["net"] for t in wins); gl = sum(t["net"] for t in losers)
    yrs = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25
    return {"ret": (bal / INITIAL - 1) * 100,
            "cagr": ((bal / INITIAL) ** (1 / yrs) - 1) * 100 if bal > 0 else -100,
            "pf": abs(gw / gl) if gl < 0 else float("inf"), "dd": max_dd * 100,
            "n": len(trades), "wr": len(wins) / len(trades) * 100 if trades else 0,
            "whip": whip / len(losers) * 100 if losers else 0,
            "avg_hold": np.mean([t["bars"] for t in trades]) if trades else 0}


df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
print(f"Data: {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}  REAL fees, flip=on")
print(f"{'N':>4} {'exit':>11} {'trades':>7} {'WR':>5} {'PF':>6} {'return':>9} {'CAGR':>7} "
      f"{'maxDD':>6} {'whipsaw%':>9} {'hold(d)':>8}")
for N in (20, 55):
    for mode in ("donch_half", "donch_full", "chand_2.5", "chand_3.5"):
        r = run(df, N, mode)
        print(f"{N:>4} {mode:>11} {r['n']:>7} {r['wr']:>4.0f}% {r['pf']:>6.2f} {r['ret']:>+8.0f}% "
              f"{r['cagr']:>+6.1f}% {r['dd']:>5.1f}% {r['whip']:>8.0f}% {r['avg_hold']:>8.1f}")
