"""fresh_search.py — pre-registered honest search for a cross-cycle BTC strategy.

PROTOCOL (fixed before running):
  - Classic strategies, literature-standard parameters. NO sweeps, NO tuning.
  - Signals on closed bars, fills at NEXT bar open, fees+slip on every change.
  - Long legs modeled on SPOT (0.10%/side taker, no funding, no liquidation).
    Short legs need perp: 0.055%/side + funding cost UNKNOWN here -> shorts are
    additionally charged a conservative 0.01%/day holding cost proxy.
  - 1x only. No leverage.
  - IS = 2021-05..2023-12, OOS = 2024-01..2026-06. Selection looks at IS only;
    OOS reported for everything regardless.
  - All results printed, winners and losers.

Strategies (params fixed a priori):
  HOLD        buy & hold (benchmark)
  SMA200-LF   long when close > 200d SMA, else flat              (classic)
  EMAX-LF     EMA50/200 cross long/flat                          (golden cross)
  EMAX-LS     EMA50/200 cross long/short
  EMAX4h-LF   EMA20/100 cross on 4h, long/flat
  EMAX4h-LS   EMA20/100 cross on 4h, long/short
  DON-LF      Donchian 55-high entry / 20-low exit, long/flat    (turtle)
  DON-LS      Donchian 55/20 symmetric long/short
  TSMOM90-LF  long if trailing 90d return > 0, else flat         (momentum)
  TSMOM30-LS  long if 30d ret > 0, short if < 0
"""
import pandas as pd
import numpy as np

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m.csv"
SPOT_FEE = 0.0010 + 0.0003   # taker + slip per side
PERP_FEE = 0.00055 + 0.0003
SHORT_HOLD_COST_DAILY = 0.0001  # funding proxy while short
IS_END = pd.Timestamp("2024-01-01")


def load():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).set_index("timestamp")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    d1 = df.resample("1D").agg(agg).dropna()
    h4 = df.resample("4h").agg(agg).dropna()
    return d1, h4


def donchian_pos(df, n_in=55, n_out=20, allow_short=True):
    """Stateful turtle: enter on n_in-bar extreme breakout (of PRIOR bars),
    exit on opposite n_out-bar extreme. Position decided on bar close i,
    applied at bar i+1 open."""
    hi_in = df["high"].rolling(n_in).max().shift(1)
    lo_in = df["low"].rolling(n_in).min().shift(1)
    hi_out = df["high"].rolling(n_out).max().shift(1)
    lo_out = df["low"].rolling(n_out).min().shift(1)
    c = df["close"].values
    pos = np.zeros(len(df))
    p = 0
    for i in range(len(df)):
        if np.isnan(hi_in.iloc[i]):
            pos[i] = 0; continue
        if p == 0:
            if c[i] > hi_in.iloc[i]: p = 1
            elif allow_short and c[i] < lo_in.iloc[i]: p = -1
        elif p == 1:
            if c[i] < lo_out.iloc[i]:
                p = -1 if (allow_short and c[i] < lo_in.iloc[i]) else 0
        elif p == -1:
            if c[i] > hi_out.iloc[i]:
                p = 1 if c[i] > hi_in.iloc[i] else 0
        pos[i] = p
    return pd.Series(pos, index=df.index)


def strategies(d1, h4):
    out = []  # (name, df, target_pos_series)
    out.append(("HOLD", d1, pd.Series(1.0, index=d1.index)))
    sma200 = d1["close"].rolling(200).mean()
    out.append(("SMA200-LF", d1, (d1["close"] > sma200).astype(float)))
    e50 = d1["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    e200 = d1["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    lf = (e50 > e200).astype(float); lf[e200.isna()] = 0
    out.append(("EMAX-LF", d1, lf))
    ls = np.where(e200.isna(), 0, np.where(e50 > e200, 1.0, -1.0))
    out.append(("EMAX-LS", d1, pd.Series(ls, index=d1.index)))
    f4 = h4["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    s4 = h4["close"].ewm(span=100, adjust=False, min_periods=100).mean()
    lf4 = (f4 > s4).astype(float); lf4[s4.isna()] = 0
    out.append(("EMAX4h-LF", h4, lf4))
    ls4 = np.where(s4.isna(), 0, np.where(f4 > s4, 1.0, -1.0))
    out.append(("EMAX4h-LS", h4, pd.Series(ls4, index=h4.index)))
    out.append(("DON-LF", d1, donchian_pos(d1, allow_short=False)))
    out.append(("DON-LS", d1, donchian_pos(d1, allow_short=True)))
    r90 = d1["close"].pct_change(90)
    out.append(("TSMOM90-LF", d1, (r90 > 0).astype(float).where(r90.notna(), 0)))
    r30 = d1["close"].pct_change(30)
    ls30 = np.where(r30.isna(), 0, np.where(r30 > 0, 1.0, -1.0))
    out.append(("TSMOM30-LS", d1, pd.Series(ls30, index=d1.index)))
    return out


def backtest(df, target, bars_per_day):
    """Signal from bar i close -> position held from bar i+1 open to i+2 open.
    Returns per-bar equity (and intra-bar lows for MTM DD)."""
    o = df["open"].values; lo = df["low"].values; hi = df["high"].values
    tgt = target.shift(1).fillna(0).values  # applied at THIS bar's open
    eq = 1.0
    pos = 0.0
    rows = []
    for i in range(len(df) - 1):
        p = tgt[i]
        if p != pos:
            fee = SPOT_FEE if (pos >= 0 and p >= 0) else PERP_FEE
            eq *= 1 - abs(p - pos) * fee
            pos = p
        # mark-to-market trough within the holding bar
        if pos > 0:
            eq_low = eq * (1 + pos * (lo[i] / o[i] - 1))
        elif pos < 0:
            eq_low = eq * (1 + pos * (hi[i] / o[i] - 1))
        else:
            eq_low = eq
        r = o[i + 1] / o[i] - 1
        eq *= (1 + pos * r)
        if pos < 0:
            eq *= (1 - SHORT_HOLD_COST_DAILY / bars_per_day)
        rows.append((df.index[i], eq, eq_low, pos))
    return pd.DataFrame(rows, columns=["ts", "eq", "eq_low", "pos"]).set_index("ts")


def stats(track):
    if len(track) == 0:
        return None
    eq = track["eq"]; eql = track["eq_low"]
    ret = (eq.iloc[-1] / eq.iloc[0] - 1) * 100
    peak = eq.cummax()
    dd = ((peak - np.minimum(eq, eql)) / peak).max() * 100
    years = (track.index[-1] - track.index[0]).days / 365.25
    cagr = ((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1) * 100 if years > 0 else 0
    flips = (track["pos"].diff().abs() > 0).sum()
    return ret, cagr, dd, flips


def main():
    d1, h4 = load()
    print(f"1d bars: {len(d1)}  4h bars: {len(h4)}  IS < {IS_END.date()} <= OOS\n")
    hdr = f"{'strategy':<12}{'seg':<5}{'ret%':>9}{'CAGR%':>8}{'maxDD%':>8}{'trades':>7}"
    print(hdr); print("-" * len(hdr))
    for name, df, tgt in strategies(d1, h4):
        bpd = 1 if df is d1 else 6
        track = backtest(df, tgt, bpd)
        for seg, t in [("IS", track[track.index < IS_END]),
                       ("OOS", track[track.index >= IS_END]),
                       ("ALL", track)]:
            if len(t) == 0: continue
            t = t.copy(); t["eq"] = t["eq"] / t["eq"].iloc[0]
            t["eq_low"] = t["eq_low"] / t["eq"].iloc[0] if False else t["eq_low"] / (t["eq_low"].iloc[0] / t["eq"].iloc[0]) if False else t["eq_low"]
            # normalize lows by same base as eq
            base = track.loc[t.index[0], "eq"]
            t["eq"] = track.loc[t.index, "eq"] / base
            t["eq_low"] = track.loc[t.index, "eq_low"] / base
            s = stats(t)
            print(f"{name:<12}{seg:<5}{s[0]:>9.1f}{s[1]:>8.1f}{s[2]:>8.1f}{s[3]:>7}")
        print()


if __name__ == "__main__":
    main()
