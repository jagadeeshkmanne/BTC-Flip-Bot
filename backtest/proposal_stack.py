"""proposal_stack.py — honest test of the external review's proposals (2026-06-12).

Reviewer's claims to test (the parts NOT already falsified in FINDINGS.md):
  A) STACK-MR  : 15m trend alignment + 5m RSI14 <30/>70 + Bollinger(20,2)
                 band touch + confirming candle close + volume > SMA20(vol).
                 ("This alone will eliminate many bad trades.")
  B) PULLBACK  : trending regime (15m ADX14 > 25): price pulls back to 15m
                 EMA20 in trend direction + confirming candle -> with-trend entry.
  C) REGIME    : ADX > 25 -> PULLBACK entries; ADX < 20 -> RSI/BB mean
                 reversion (no trend gate); 20-25 dead zone -> no trade.
                 ("The single biggest improvement I have seen in crypto bots.")

Exits per reviewer's target R:R: TP {0.8, 1.0, 1.2}% x SL {0.5, 0.7}%,
single position, no DCA, max hold 24h then market-out (their "fixed SL, no
martingale" spec). PROTOCOL (pre-registered, FINDINGS.md checklist):
  - signals on closed 5m bars, entry next bar OPEN (+slip)
  - TP = resting limit (wick fill at price); SL = stop, fill worse(stop, open)
    +slip; both same bar -> SL (pessimistic)
  - costs reported at ZERO / MAKER 2x0.02% / TAKER 2x0.055%+2x0.02% slip
  - IS = ..2023-12 (selection), OOS = 2024-01.. (report only), data 2019-10+
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
SLIP = 0.0002
MAX_HOLD = 288
OOS_START = pd.Timestamp("2024-01-01")
TPS = [0.8, 1.0, 1.2]
SLS = [0.5, 0.7]
COSTS = {"ZERO": 0.0, "MAKER": 2 * 0.02, "TAKER": 2 * 0.055 + 2 * SLIP * 100}


def wilder_rsi(close, n):
    d = close.diff()
    ag = d.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + ag / al)


def wilder_adx(df15, n=14):
    h, l, c = df15["high"], df15["low"], df15["close"]
    up = h.diff()
    dn = -l.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    pdi = 100 * pd.Series(plus_dm, index=df15.index).ewm(alpha=1/n, adjust=False, min_periods=n).mean() / atr
    mdi = 100 * pd.Series(minus_dm, index=df15.index).ewm(alpha=1/n, adjust=False, min_periods=n).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return dx.ewm(alpha=1/n, adjust=False, min_periods=n).mean()


def prep():
    df = pd.read_csv(CSV, parse_dates=["timestamp"])
    df = df[df["timestamp"] >= "2019-10-01"].sort_values("timestamp").reset_index(drop=True)
    c = df["close"]
    df["rsi14"] = wilder_rsi(c, 14)
    mid = c.rolling(20).mean()
    sd = c.rolling(20).std()
    df["bb_lo"] = mid - 2 * sd
    df["bb_hi"] = mid + 2 * sd
    df["vol_ok"] = df["volume"] > df["volume"].rolling(20).mean()
    df["bull"] = df["close"] > df["open"]
    df["bear"] = df["close"] < df["open"]

    dfix = df.set_index("timestamp")
    df15 = dfix[["open", "high", "low", "close"]].resample(
        "15min", label="left", closed="left"
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    e20 = df15["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    e50 = df15["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    df15["up"] = e20 > e50
    df15["e20"] = e20
    df15["adx"] = wilder_adx(df15)
    df15 = df15.reset_index()
    df15["closed_at"] = df15["timestamp"] + pd.Timedelta(minutes=15)
    merged = pd.merge_asof(
        df, df15[["closed_at", "up", "e20", "adx"]].sort_values("closed_at"),
        left_on="timestamp", right_on="closed_at", direction="backward")
    return merged


def build_signals(bt):
    """Boolean long/short arrays per family; True on the CLOSED signal bar."""
    rsi = bt["rsi14"].values
    lo_b, hi_b = bt["bb_lo"].values, bt["bb_hi"].values
    l_a, h_a = bt["low"].values, bt["high"].values
    vol = bt["vol_ok"].values
    bull, bear = bt["bull"].values, bt["bear"].values
    up = bt["up"].values.astype(bool)
    has15 = ~bt["adx"].isna().values
    e20 = bt["e20"].values
    adx = bt["adx"].values

    mr_long = has15 & up & (rsi < 30) & (l_a <= lo_b) & bull & vol
    mr_short = has15 & ~up & (rsi > 70) & (h_a >= hi_b) & bear & vol

    trending = has15 & (adx > 25)
    ranging = has15 & (adx < 20)
    pb_long = trending & up & (l_a <= e20) & bull & vol
    pb_short = trending & ~up & (h_a >= e20) & bear & vol

    rng_long = ranging & (rsi < 30) & (l_a <= lo_b) & bull & vol
    rng_short = ranging & (rsi > 70) & (h_a >= hi_b) & bear & vol

    return {
        "A STACK-MR": (mr_long, mr_short),
        "B PULLBACK": (pb_long, pb_short),
        "C REGIME":   (pb_long | rng_long, pb_short | rng_short),
    }


def run_cell(bt, longs, shorts, tp_pct, sl_pct):
    o, h, l, c = (bt["open"].values, bt["high"].values,
                  bt["low"].values, bt["close"].values)
    ts = bt["timestamp"].values
    n = len(bt)
    tp, sl = tp_pct / 100.0, sl_pct / 100.0
    trades = []          # (exit_ts, gross_move_pct)
    busy_until = -1
    sig_idx = np.where(longs | shorts)[0]
    for i in sig_idx:
        fill_bar = i + 1
        if fill_bar <= busy_until or fill_bar >= n:
            continue
        side = "LONG" if longs[i] else "SHORT"
        e = o[fill_bar] * (1 + SLIP) if side == "LONG" else o[fill_bar] * (1 - SLIP)
        if side == "LONG":
            tp_px, sl_px = e * (1 + tp), e * (1 - sl)
        else:
            tp_px, sl_px = e * (1 - tp), e * (1 + sl)
        end = min(fill_bar + MAX_HOLD, n - 1)
        out = None
        for j in range(fill_bar, end + 1):
            if side == "LONG":
                if l[j] <= sl_px:
                    fill = min(sl_px, o[j]) * (1 - SLIP)
                    out = (j, (fill / e - 1) * 100); break
                if h[j] >= tp_px:
                    out = (j, (tp_px / e - 1) * 100); break
            else:
                if h[j] >= sl_px:
                    fill = max(sl_px, o[j]) * (1 + SLIP)
                    out = (j, (e / fill - 1) * 100); break
                if l[j] <= tp_px:
                    out = (j, (e / tp_px - 1) * 100); break
        if out is None:
            fill = c[end] * (1 - SLIP) if side == "LONG" else c[end] * (1 + SLIP)
            mv = (fill / e - 1) * 100 if side == "LONG" else (e / fill - 1) * 100
            out = (end, mv)
        busy_until = out[0]
        trades.append((ts[out[0]], out[1]))
    return trades


def seg_stats(trades, lo, hi):
    seg = [m for t, m in trades if lo <= pd.Timestamp(t) < hi]
    if not seg:
        return None
    nt = len(seg)
    wr = sum(1 for m in seg if m > 0) / nt * 100
    gross = sum(seg) / nt
    return nt, wr, gross


def main():
    bt = prep()
    print(f"Data: {bt['timestamp'].iloc[0]} -> {bt['timestamp'].iloc[-1]}  ({len(bt):,} bars)")
    fams = build_signals(bt)
    t0 = bt["timestamp"].iloc[0]
    t_end = bt["timestamp"].iloc[-1] + pd.Timedelta(minutes=5)
    for fam, (lg, sh) in fams.items():
        print(f"\n══ {fam} — {int(lg.sum()):,} long / {int(sh.sum()):,} short signal bars ══")
        print(f"{'TP%':>5}{'SL%':>5}{'R:R':>5} | {'N':>6}{'WR%':>6}{'gross':>8}{'MAKER':>8}{'TAKER':>8}"
              f" | {'IS N':>6}{'IS gr':>8} | {'OOS N':>6}{'OOS gr':>8}  verdict")
        for tp in TPS:
            for sl in SLS:
                trades = run_cell(bt, lg, sh, tp, sl)
                full = seg_stats(trades, t0, t_end)
                if full is None or full[0] < 30:
                    continue
                nt, wr, gross = full
                is_s = seg_stats(trades, t0, OOS_START) or (0, 0, float("nan"))
                oos = seg_stats(trades, OOS_START, t_end) or (0, 0, float("nan"))
                net_mk = gross - COSTS["MAKER"]
                net_tk = gross - COSTS["TAKER"]
                verdict = ("PROFITABLE" if net_tk > 0 else
                           "maker-only" if net_mk > 0 else
                           "gross-only" if gross > 0 else "dead")
                print(f"{tp:>5.1f}{sl:>5.1f}{tp/sl:>5.1f} | {nt:>6}{wr:>6.1f}{gross:>+8.4f}"
                      f"{net_mk:>+8.4f}{net_tk:>+8.4f} | {is_s[0]:>6}{is_s[2]:>+8.4f}"
                      f" | {oos[0]:>6}{oos[2]:>+8.4f}  {verdict}")


if __name__ == "__main__":
    main()
