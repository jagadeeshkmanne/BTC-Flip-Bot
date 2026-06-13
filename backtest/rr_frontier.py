"""rr_frontier.py — find the BEST R:R for the live rsiscalp entries (2026-06-12).

User goal: "find best R:R". Sweep TP x SL barrier geometries on the exact
live entry signal (RSI9 35/65 + 15m EMA20/50 gap >= 0.20% + ATR(14) <= 0.80%),
both COUNTER-trend (live config) and WITH-trend direction, single position,
no DCA (isolates R:R from basket effects), honest engine:

  - signal on closed bar, entry next bar OPEN
  - TP = resting limit at entry*(1+tp): fills on wick touch at tp price
  - SL = stop: trigger on extreme, fill at worse(stop, open) + slip
  - TP and SL same bar -> SL (pessimistic)
  - time barrier: exit at close after MAX_HOLD_BARS (24h) if neither hit

Per-cell we record gross price-move %, then report net expectancy per trade
under three cost modes (round trip): ZERO, MAKER 2x0.02%, TAKER 2x0.055%+slip.
Also prints the random-walk barrier prediction WR* = SL/(TP+SL) next to the
measured WR — if WR tracks WR*, the entries carry no information at that
geometry and no R:R can be profitable after costs.

Leverage note: results are in price-move % per trade; at 5x, equity % = 5x
price %. Leverage scales wins AND losses — it cannot change the sign.
"""
import numpy as np
import pandas as pd

CSV_PATH = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"
RSI_LEN = 9
RSI_LONG, RSI_SHORT = 35, 65
GAP_MIN = 0.0020
ATR_MAX = 0.008
MAX_HOLD_BARS = 288          # 24h on 5m
SLIP = 0.0002                # market-fill slippage (entry + stop exits)

TPS = [0.25, 0.5, 1.0, 1.5, 2.0, 3.0]   # %
SLS = [0.25, 0.5, 1.0, 2.0]             # %

COSTS = {                    # round-trip, % of notional
    "ZERO":  0.0,
    "MAKER": 2 * 0.02,
    "TAKER": 2 * 0.055 + 2 * SLIP * 100,
}


def wilder_rsi(close, length):
    d = close.diff()
    gain = d.clip(lower=0.0); loss = (-d).clip(lower=0.0)
    ag = gain.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    al = loss.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()
    return 100 - (100 / (1 + ag / al))


def prep():
    df = pd.read_csv(CSV_PATH, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["rsi"] = wilder_rsi(df["close"], RSI_LEN)
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    df["atr_pct"] = tr.rolling(14).mean() / df["close"]
    dfix = df.set_index("timestamp")
    df15 = dfix[["open", "high", "low", "close"]].resample(
        "15min", label="left", closed="left"
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    e20 = df15["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    e50 = df15["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    df15["trend"] = np.where(e20 > e50, 1.0, -1.0)
    df15.loc[e50.isna() | e20.isna(), "trend"] = np.nan
    df15["gap"] = (e20 - e50).abs() / e50
    df15 = df15.reset_index().rename(columns={"timestamp": "ts15"})
    df15["closed_at"] = df15["ts15"] + pd.Timedelta(minutes=15)
    merged = pd.merge_asof(
        df.sort_values("timestamp"),
        df15[["closed_at", "trend", "gap"]].sort_values("closed_at"),
        left_on="timestamp", right_on="closed_at", direction="backward")
    return merged


def signals(bt, counter_trend):
    """Bar index where a signal fires (entry at next bar open) + side."""
    rsi, atr = bt["rsi"].values, bt["atr_pct"].values
    trend, gap = bt["trend"].values, bt["gap"].values
    sigs = []
    n = len(bt)
    for i in range(n - 1):
        if np.isnan(rsi[i]) or np.isnan(atr[i]) or np.isnan(trend[i + 1]) or np.isnan(gap[i + 1]):
            continue
        if atr[i] > ATR_MAX or gap[i + 1] < GAP_MIN:
            continue
        if rsi[i] <= RSI_LONG:
            raw = "LONG"
        elif rsi[i] >= RSI_SHORT:
            raw = "SHORT"
        else:
            continue
        side = raw if counter_trend else ("SHORT" if raw == "LONG" else "LONG")
        sigs.append((i + 1, side))     # fill bar
    return sigs


def run_cell(bt, sigs, tp_pct, sl_pct):
    """One pass; non-overlapping positions. Returns list of gross moves (%)."""
    o, h, l, c = (bt["open"].values, bt["high"].values,
                  bt["low"].values, bt["close"].values)
    n = len(bt)
    tp, sl = tp_pct / 100.0, sl_pct / 100.0
    moves = []
    busy_until = -1
    for fill_bar, side in sigs:
        if fill_bar <= busy_until or fill_bar >= n:
            continue
        e = o[fill_bar] * (1 + SLIP) if side == "LONG" else o[fill_bar] * (1 - SLIP)
        if side == "LONG":
            tp_px, sl_px = e * (1 + tp), e * (1 - sl)
        else:
            tp_px, sl_px = e * (1 - tp), e * (1 + sl)
        end = min(fill_bar + MAX_HOLD_BARS, n - 1)
        out = None
        for j in range(fill_bar, end + 1):
            if side == "LONG":
                sl_hit = l[j] <= sl_px
                tp_hit = h[j] >= tp_px
                if sl_hit:                      # pessimistic when both
                    fill = min(sl_px, o[j]) * (1 - SLIP)
                    out = (j, (fill / e - 1) * 100)
                    break
                if tp_hit:
                    out = (j, (tp_px / e - 1) * 100)
                    break
            else:
                sl_hit = h[j] >= sl_px
                tp_hit = l[j] <= tp_px
                if sl_hit:
                    fill = max(sl_px, o[j]) * (1 + SLIP)
                    out = (j, (e / fill - 1) * 100)   # short: gain when fill < e
                    break
                if tp_hit:
                    out = (j, (e / tp_px - 1) * 100)
                    break
        if out is None:
            fill = c[end] * (1 - SLIP) if side == "LONG" else c[end] * (1 + SLIP)
            mv = (fill / e - 1) * 100 if side == "LONG" else (e / fill - 1) * 100
            out = (end, mv)
        busy_until = out[0]
        moves.append(out[1])
    return moves


def main():
    bt = prep()
    print(f"Data: {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]}  ({len(bt):,} bars)")
    for ct in (True, False):
        sigs = signals(bt, counter_trend=ct)
        label = "COUNTER-TREND (live)" if ct else "WITH-TREND (flipped)"
        print(f"\n══ {label} — {len(sigs):,} signals ══")
        print(f"{'TP%':>5}{'SL%':>5}{'R:R':>6}{'N':>7}{'WR%':>7}{'WR*%':>6}"
              f"{'gross/tr':>9}{'ZERO':>8}{'MAKER':>8}{'TAKER':>8}  verdict")
        best = None
        for tp in TPS:
            for sl in SLS:
                moves = run_cell(bt, sigs, tp, sl)
                nt = len(moves)
                if nt < 30:
                    continue
                wr = sum(1 for m in moves if m > 0) / nt * 100
                wr_star = sl / (tp + sl) * 100
                gross = sum(moves) / nt
                nets = {k: gross - cost for k, cost in COSTS.items()}
                verdict = "PROFITABLE" if nets["TAKER"] > 0 else (
                    "maker-only" if nets["MAKER"] > 0 else (
                        "gross-only" if gross > 0 else "dead"))
                print(f"{tp:>5.2f}{sl:>5.2f}{tp/sl:>6.1f}{nt:>7}{wr:>7.1f}{wr_star:>6.1f}"
                      f"{gross:>+9.4f}{nets['ZERO']:>+8.4f}{nets['MAKER']:>+8.4f}"
                      f"{nets['TAKER']:>+8.4f}  {verdict}")
                key = nets["TAKER"]
                if best is None or key > best[0]:
                    best = (key, tp, sl, nt, wr, gross)
        if best:
            k, tp, sl, nt, wr, gross = best
            print(f"  BEST cell by TAKER net: TP {tp}% / SL {sl}% (R:R {tp/sl:.1f}) "
                  f"N={nt} WR={wr:.1f}% gross {gross:+.4f}%/trade, taker net {k:+.4f}%/trade"
                  f" -> {'PROFITABLE' if k > 0 else 'NOT profitable'}")


if __name__ == "__main__":
    main()
