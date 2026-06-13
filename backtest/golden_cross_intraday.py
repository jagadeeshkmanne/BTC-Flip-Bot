"""golden_cross_intraday.py — EMA50/200 golden cross on 5m & 15m, SL-tuned.

User (2026-06-12): the daily golden cross worked; try it on 5m and 15m and
tune the SL only.

Strategy (long/flat spot, trend-following):
  - Enter LONG at next bar open when EMA50 crosses ABOVE EMA200 (golden cross).
  - Exit on the FIRST of:
      * death cross (EMA50 < EMA200)  -> exit next bar open
      * SL: price falls SL% below entry -> exit at the SL price (honest fill)
  - After an SL exit, stay flat until a FRESH golden cross re-arms (no instant
    re-entry while still above EMA200).
  - Honest fills (adverse gap fills at open), fees 0.055%/side + 0.02% slip.
  - Spot 1x. Compounded. IS/OOS split at the midpoint.
SL swept incl. "none" (death-cross-only exit). Compared to buy & hold and to
the daily golden cross on the same data.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
FEE, SLIP = 0.00055, 0.0002
SIDE_COST = FEE + SLIP
SL_GRID = [None, 0.005, 0.01, 0.02, 0.03, 0.05]
ANN_BARS = {"5min": 105120.0, "15min": 35040.0, "1D": 365.0}


def frame(df, rule):
    g = pd.DataFrame({"open": df["open"].resample(rule).first(),
                      "high": df["high"].resample(rule).max(),
                      "low": df["low"].resample(rule).min(),
                      "close": df["close"].resample(rule).last()}).dropna()
    g["e50"] = g["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    g["e200"] = g["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    return g.dropna().reset_index()


def backtest(g, sl):
    o = g["open"].values; h = g["high"].values; lo = g["low"].values
    e50 = g["e50"].values; e200 = g["e200"].values
    up = e50 > e200
    n = len(g)
    in_pos = False; entry = 0.0; armed = True
    rets = []; tstamps = []; holds = []
    i = 1
    while i < n - 1:
        if not in_pos:
            golden = up[i] and not up[i-1]            # fresh cross this bar
            if golden and armed:
                entry = o[i+1] * (1 + SLIP)            # buy next open + slip
                in_pos = True; start = i+1; i += 1; continue
            if not up[i]:
                armed = True                          # reset arm once back below
            i += 1; continue
        # in position: check SL then death cross, bar by bar from start
        sl_px = entry * (1 - sl) if sl else None
        if sl_px is not None and lo[i] <= sl_px:
            fill = min(o[i], sl_px)                    # adverse gap -> open
            gross = fill * (1 - SLIP) / entry - 1
            rets.append(gross - 2*FEE); tstamps.append(g["timestamp"].iloc[i]); holds.append(i-start)
            in_pos = False; armed = False              # wait for fresh cross
            i += 1; continue
        if not up[i]:                                  # death cross -> exit next open
            fill = o[i+1] * (1 - SLIP)
            gross = fill / entry - 1
            rets.append(gross - 2*FEE); tstamps.append(g["timestamp"].iloc[i+1]); holds.append(i+1-start)
            in_pos = False; armed = True; i += 1; continue
        i += 1
    return pd.DataFrame({"t": tstamps, "ret": rets, "hold": holds})


def stats(tr, ann, label, mid):
    if len(tr) == 0:
        print(f"  {label:14s}  no trades"); return
    eq = (1 + tr["ret"]).cumprod()
    tot = eq.iloc[-1] - 1
    wr = (tr["ret"] > 0).mean() * 100
    # crude maxDD on the trade-equity curve
    dd = (eq / eq.cummax() - 1).min()
    is_ = tr[tr["t"] < mid]["ret"]; oos = tr[tr["t"] >= mid]["ret"]
    ist = (1+is_).prod()-1 if len(is_) else 0.0
    oost = (1+oos).prod()-1 if len(oos) else 0.0
    print(f"  {label:14s}  N={len(tr):5d}  WR={wr:4.1f}%  net_total={tot*100:+8.1f}%  "
          f"maxDD={dd*100:5.0f}%  medHold={tr['hold'].median():4.0f}bars  "
          f"IS={ist*100:+7.1f}%  OOS={oost*100:+7.1f}%")


if __name__ == "__main__":
    raw = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").set_index("timestamp")
    for rule in ("5min", "15min", "1D"):
        g = frame(raw, rule)
        mid = g["timestamp"].iloc[len(g)//2]
        bh = g["close"].iloc[-1]/g["close"].iloc[0] - 1
        print(f"\n===== {rule}  bars={len(g)}  {g['timestamp'].iloc[0].date()}->{g['timestamp'].iloc[-1].date()}"
              f"   BUY&HOLD {bh*100:+.0f}%   IS/OOS@{mid.date()} =====")
        for sl in SL_GRID:
            tr = backtest(g, sl)
            stats(tr, ANN_BARS[rule], f"SL={'none' if sl is None else f'{sl*100:.1f}%'}", mid)
