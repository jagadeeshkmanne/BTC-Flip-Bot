#!/usr/bin/env python3
"""weakness_rectangle.py — honest test of the 'weakness rectangle' sweep-reversal
(YouTube transcript, user 2026-06-12), mechanical core on BTCUSDT.

Setup (long side; short mirrored):
  - 15m signal bar: low takes out the prior swing low (rolling N-bar low),
    candle is BEARISH (close<open), and close is back ABOVE the swept low.
  - Rectangle = [signal close .. swept low].
  - Execution on 5m: within the next EXEC_WIN bars, enter when a 5m bar CLOSES
    above the rectangle top (signal close); invalidate if a 5m close goes
    below the swept low first.
  - Entry at next 5m open (+slip, taker). SL = swept low − buffer; honest stop
    fill = worse(stop, bar open) + slip. TP = R_MULT × risk (limit, fill at
    price, maker leg ignored — taker charged both sides, conservative).
  - Time stop 24h at close. One position at a time.

Variants: R target 3/5; small-rectangle filter (tip #1); 1h EMA200 direction
alignment (tip: trade with higher-TF direction). Fees 0.055%/side + 0.02%
slip on market fills; halves + yearly. Checklist: closed bars only, no
lookahead, pessimistic stop-before-TP in the same bar.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
SWING_N = 24          # 15m bars (~6h) rolling extreme = "the low/high"
EXEC_WIN = 12         # 5m bars (1h) to trigger after signal
TIME_STOP = 288       # 5m bars (24h)
SL_BUF = 0.0002       # stop buffer beyond the wick
FEE, SLIP = 0.00055, 0.0002
SPLIT = np.datetime64("2023-01-01")


def prep():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    dfx = df.set_index("timestamp")
    f15 = dfx[["open", "high", "low", "close"]].resample(
        "15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    f15["swing_lo"] = f15["low"].shift(1).rolling(SWING_N).min()
    f15["swing_hi"] = f15["high"].shift(1).rolling(SWING_N).max()
    h1 = dfx["close"].resample("1h", label="left", closed="left").last().dropna()
    ema200 = h1.ewm(span=200, adjust=False, min_periods=200).mean()
    up1h = pd.DataFrame({"closed_at": h1.index + pd.Timedelta(hours=1),
                         "up": (h1 > ema200).astype(float)})
    up1h.loc[ema200.isna(), "up"] = np.nan
    f15 = f15.reset_index()
    f15["closed_at15"] = f15["timestamp"] + pd.Timedelta(minutes=15)
    f15 = pd.merge_asof(f15, up1h.sort_values("closed_at"),
                        left_on="closed_at15", right_on="closed_at",
                        direction="backward", allow_exact_matches=True)
    return df, f15


def signals(f15):
    out = []
    for r in f15.itertuples():
        if np.isnan(r.swing_lo) or np.isnan(r.swing_hi):
            continue
        # long: sweep prior low, bearish candle, close back above the low
        if r.low < r.swing_lo and r.close < r.open and r.close > r.swing_lo:
            out.append(dict(side=1, t=r.closed_at15, rect_top=r.close,
                            wick=r.low, up1h=r.up))
        # short: sweep prior high, bullish candle, close back below the high
        elif r.high > r.swing_hi and r.close > r.open and r.close < r.swing_hi:
            out.append(dict(side=-1, t=r.closed_at15, rect_top=r.close,
                            wick=r.high, up1h=r.up))
    return out


def run(df5, sigs, r_mult, max_rect_pct=None, trend_align=False):
    ts5 = df5["timestamp"].values
    O, H, L, C = (df5[c].values for c in ("open", "high", "low", "close"))
    n = len(df5)
    idx_of = {int(t): i for i, t in enumerate(ts5.astype("datetime64[ns]").astype(np.int64))}
    trades = []
    busy_until = -1
    for s in sigs:
        i0 = idx_of.get(int(pd.Timestamp(s["t"]).value))
        if i0 is None or i0 <= busy_until:
            continue
        side, rect_top, wick = s["side"], s["rect_top"], s["wick"]
        rect_pct = abs(rect_top - wick) / rect_top * 100
        if max_rect_pct is not None and rect_pct > max_rect_pct:
            continue
        if trend_align:
            if np.isnan(s["up1h"]) or (side == 1) != (s["up1h"] == 1.0):
                continue
        # execution scan: 5m close beyond rectangle top before close beyond wick
        ent = None
        for j in range(i0, min(i0 + EXEC_WIN, n - 1)):
            if (C[j] < wick) if side == 1 else (C[j] > wick):
                break                                  # invalidated
            if (C[j] > rect_top) if side == 1 else (C[j] < rect_top):
                ent = j + 1
                break
        if ent is None:
            continue
        e = O[ent] * (1 + SLIP * side)
        sl = wick * (1 - SL_BUF * side)
        risk = abs(e - sl)
        tp = e + r_mult * risk * side
        fees_in = e * FEE
        res = None
        for j in range(ent, min(ent + TIME_STOP, n)):
            sl_hit = (L[j] <= sl) if side == 1 else (H[j] >= sl)
            tp_hit = (H[j] >= tp) if side == 1 else (L[j] <= tp)
            if sl_hit:                                  # pessimistic first
                fill = (min(sl, O[j]) if side == 1 else max(sl, O[j]))
                fill *= (1 - SLIP * side)
                res = (fill - e) * side - fill * FEE - fees_in
                break
            if tp_hit:
                res = (tp - e) * side - tp * FEE - fees_in
                break
        else:
            j = min(ent + TIME_STOP, n - 1)
            fill = C[j] * (1 - SLIP * side)
            res = (fill - e) * side - fill * FEE - fees_in
        busy_until = j
        trades.append(dict(r=res / (risk + e * (FEE + SLIP) * 2) if risk > 0 else 0,
                           pct=res / e * 100, t=ts5[ent], rect=rect_pct))
    return trades


def report(label, tr):
    if not tr:
        print(f"{label:<34} no trades"); return
    pct = np.array([t["pct"] for t in tr])
    w = (pct > 0).mean() * 100
    ts = np.array([t["t"] for t in tr])
    h1 = pct[ts < SPLIT]; h2 = pct[ts >= SPLIT]
    gp, gl = pct[pct > 0].sum(), pct[pct < 0].sum()
    pf = abs(gp / gl) if gl < 0 else float("inf")
    print(f"{label:<34}{len(pct):>6,}{w:>7.1f}{pf:>7.2f}{pct.mean():>9.3f}"
          f"{pct.sum():>9.1f}{h1.sum() if len(h1) else 0:>9.1f}{h2.sum() if len(h2) else 0:>9.1f}"
          f"  med-rect {np.median([t['rect'] for t in tr]):.2f}%")


def main():
    df5, f15 = prep()
    sigs = signals(f15)
    print(f"Data {df5['timestamp'].iloc[0].date()} -> {df5['timestamp'].iloc[-1].date()} | "
          f"15m sweep-reject signals: {len(sigs):,} "
          f"(long {sum(1 for s in sigs if s['side']==1):,} / short {sum(1 for s in sigs if s['side']==-1):,})")
    print(f"\n{'variant':<34}{'N':>6}{'win%':>7}{'PF':>7}{'avg%/tr':>9}"
          f"{'tot%':>9}{'19-22%':>9}{'23-26%':>9}")
    for rm in (3, 5):
        report(f"R={rm} all signals", run(df5, sigs, rm))
        report(f"R={rm} small-rect <=0.30%", run(df5, sigs, rm, max_rect_pct=0.30))
        report(f"R={rm} 1h-trend aligned", run(df5, sigs, rm, trend_align=True))
        report(f"R={rm} small-rect + aligned", run(df5, sigs, rm, max_rect_pct=0.30,
                                                   trend_align=True))
    # zero-fee diagnostic on the composite (is there ANY gross edge?)
    global FEE, SLIP
    f, s = FEE, SLIP
    FEE = SLIP = 0.0
    print("\nZERO-FEE diagnostic:")
    for rm in (3, 5):
        report(f"R={rm} all signals (0 fees)", run(df5, sigs, rm))
        report(f"R={rm} small+aligned (0 fees)", run(df5, sigs, rm, max_rect_pct=0.30,
                                                     trend_align=True))
    FEE, SLIP = f, s


if __name__ == "__main__":
    main()
