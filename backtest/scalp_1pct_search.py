"""scalp_1pct_search.py — pre-registered honest search for a 5m BTC scalp
strategy targeting 1%/day (= +34.8%/month compounded) at 3x / 5x leverage.

PROTOCOL (fixed before running, same discipline as fresh_search.py):
  - Signals on CLOSED 5m bars only; entry at NEXT bar open.
  - Honest fills: trigger exits fill at the first observable price beyond the
    trigger (bar open if it gaps past, else the trigger). Pessimistic intra-bar
    ordering: SL before TP when both touch.
  - Costs: 0.055%/side taker + 0.02%/side slip = 0.15% round trip on notional.
    A zero-cost diagnostic run is reported for every variant to show whether
    the raw signal has ANY gross edge.
  - Leverage 3x and 5x on 95% of equity, compounded per trade. No pyramiding,
    one position at a time, max hold 288 bars (24h) then market-out at close.
  - IS = 2019-10 .. 2023-12 (selection), OOS = 2024-01 .. 2026-06 (report only).
  - All variants reported by family; winners AND losers. No re-tuning after
    seeing OOS.

GOAL BAR: monthly return >= +34.8% in EVERY month (1% daily compounded).
Also reported: % months positive, median month, worst month.

Families (parameter grids fixed a priori, literature-standard values):
  DON   Donchian breakout: N in {24,48,96,288} prior-bar extreme; TP/SL grid.
  BOLL  z-score reversion: win in {20,60}, z in {2.0,3.0}; exit mid-band or SL.
  EMA   EMA cross momentum: (9,21),(20,50); TP/SL grid.
  SQZ   BB-width squeeze (20-bar width < its 10th pctile over 2880 bars) then
        breakout of 12-bar extreme; TP/SL grid.
TP/SL grid where applicable: SL in {0.3,0.6,1.0}%, TP in {0.3,0.6,1.2}%.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_5m_extended.csv"
FEE_SIDE = 0.00055
SLIP_SIDE = 0.0002
COST_RT = 2 * (FEE_SIDE + SLIP_SIDE)          # 0.15% of notional per round trip
EQ_FRAC = 0.95
MAX_HOLD = 288                                 # bars (24h)
IS_END = pd.Timestamp("2024-01-01")
GOAL_MONTH = 0.348                             # 1.01**30 - 1

SL_GRID = [0.003, 0.006, 0.010]
TP_GRID = [0.003, 0.006, 0.012]


def load():
    df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp")
    df = df[df["timestamp"] >= "2019-10-01"].reset_index(drop=True)
    return df


def signals(df):
    """Return list of (name, long_sig, short_sig) boolean arrays — signal true
    on bar i means 'decided at close of bar i', enter at open of i+1."""
    c, h, l = df["close"], df["high"], df["low"]
    out = []

    for n in (24, 48, 96, 288):
        hi = h.rolling(n).max().shift(1)
        lo = l.rolling(n).min().shift(1)
        out.append((f"DON{n}", (c > hi).values, (c < lo).values))

    for w in (20, 60):
        m = c.rolling(w).mean()
        s = c.rolling(w).std()
        z = (c - m) / s
        for zt in (2.0, 3.0):
            out.append((f"BOLL{w}z{zt}", (z < -zt).values, (z > zt).values))

    for f, s_ in ((9, 21), (20, 50)):
        ef = c.ewm(span=f, adjust=False, min_periods=f).mean()
        es = c.ewm(span=s_, adjust=False, min_periods=s_).mean()
        x_up = (ef > es) & (ef.shift(1) <= es.shift(1))
        x_dn = (ef < es) & (ef.shift(1) >= es.shift(1))
        out.append((f"EMA{f}/{s_}", x_up.values, x_dn.values))

    m20 = c.rolling(20).mean()
    s20 = c.rolling(20).std()
    width = (4 * s20) / m20
    thresh = width.rolling(2880).quantile(0.10)
    squeeze = (width < thresh).shift(1)
    hi12 = h.rolling(12).max().shift(1)
    lo12 = l.rolling(12).min().shift(1)
    out.append(("SQZ", (squeeze & (c > hi12)).fillna(False).values,
                       (squeeze & (c < lo12)).fillna(False).values))
    return out


def run_trades(df, long_sig, short_sig, sl, tp, mid_exit=None):
    """Event-driven honest engine. Returns list of (exit_ts, side, gross_frac).
    mid_exit: optional array of exit-trigger prices (mean-reversion target);
    when set, tp is ignored and exit is first touch of mid_exit (or SL/time)."""
    O, H, L, C = (df[c].values for c in ("open", "high", "low", "close"))
    TS = df["timestamp"].values
    n = len(df)
    trades = []
    i = 0
    while i < n - 2:
        side = 1 if long_sig[i] else (-1 if short_sig[i] else 0)
        if side == 0:
            i += 1
            continue
        e = i + 1
        entry = O[e]
        sl_px = entry * (1 - sl * side)
        tp_px = entry * (1 + tp * side) if mid_exit is None else None
        end = min(e + MAX_HOLD, n - 1)
        exit_px, exit_j = None, None
        for j in range(e, end):
            o, hh, ll = O[j], H[j], L[j]
            # gap-aware stop (honest: fill at open if it opens beyond)
            if side == 1 and (ll <= sl_px):
                exit_px = min(sl_px, o) if j > e else min(sl_px, o)
                exit_j = j
                break
            if side == -1 and (hh >= sl_px):
                exit_px = max(sl_px, o)
                exit_j = j
                break
            tgt = mid_exit[j] if mid_exit is not None else tp_px
            if tgt is not None:
                if side == 1 and hh >= tgt:
                    exit_px = max(tgt, o)
                    exit_j = j
                    break
                if side == -1 and ll <= tgt:
                    exit_px = min(tgt, o)
                    exit_j = j
                    break
        if exit_px is None:
            exit_j = end
            exit_px = C[end]
        gross = side * (exit_px / entry - 1.0)
        trades.append((TS[exit_j], gross))
        i = exit_j + 1
    return trades


def evaluate(trades, lev, cost_rt):
    """Compound equity; return dict of stats incl. monthly distribution."""
    if not trades:
        return None
    eq = 1.0
    monthly = {}
    wins = losses = 0
    gw = gl = 0.0
    blown = False
    for ts, gross in trades:
        net = gross - cost_rt
        eq_mult = 1 + EQ_FRAC * lev * net
        if eq_mult <= 0:
            eq = 0.0
            blown = True
            break
        prev = eq
        eq *= eq_mult
        mk = str(ts)[:7]
        monthly[mk] = monthly.get(mk, 1.0) * eq_mult
        if net > 0:
            wins += 1; gw += net
        else:
            losses += 1; gl += net
    mrets = np.array([v - 1 for v in monthly.values()])
    return dict(
        final=eq, blown=blown, n=wins + losses, wr=wins / max(1, wins + losses),
        pf=(gw / abs(gl)) if gl < 0 else float("inf"),
        months=len(mrets), pos_months=float((mrets > 0).mean()),
        goal_months=float((mrets >= GOAL_MONTH).mean()),
        med_month=float(np.median(mrets)), worst_month=float(mrets.min()),
        best_month=float(mrets.max()),
    )


def main():
    df = load()
    print(f"Data: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}  ({len(df):,} bars)")
    is_mask = df["timestamp"] < IS_END
    print(f"IS bars {is_mask.sum():,}  OOS bars {(~is_mask).sum():,}")
    print(f"GOAL: every month >= +{GOAL_MONTH*100:.1f}%  (1%/day compounded)\n")

    sigs = signals(df)

    # mean-reversion mid-band exit arrays
    mid20 = df["close"].rolling(20).mean().values
    mid60 = df["close"].rolling(60).mean().values

    hdr = (f"{'variant':<26}{'lev':>4}{'seg':>5}{'trades':>7}{'WR%':>6}{'PF':>6}"
           f"{'final_eq':>10}{'pos_mo%':>8}{'goal_mo%':>9}{'med_mo%':>8}{'worst_mo%':>10}")
    print(hdr)
    print("-" * len(hdr))

    rows = []
    for name, ls, ss in sigs:
        if name.startswith("BOLL"):
            w = 20 if "BOLL20" in name else 60
            mid = mid20 if w == 20 else mid60
            combos = [(sl, None, mid) for sl in SL_GRID]
        else:
            combos = [(sl, tp, None) for sl in SL_GRID for tp in TP_GRID]
        for sl, tp, mid in combos:
            vname = f"{name} sl{sl*100:.1f}" + (f" tp{tp*100:.1f}" if tp else " ->mid")
            trades = run_trades(df, ls, ss, sl, tp if tp else 0, mid_exit=mid)
            if len(trades) < 30:
                continue
            t_is = [t for t in trades if pd.Timestamp(t[0]) < IS_END]
            t_oos = [t for t in trades if pd.Timestamp(t[0]) >= IS_END]
            for lev in (3, 5):
                r_all = evaluate(trades, lev, COST_RT)
                r_is = evaluate(t_is, lev, COST_RT)
                r_oos = evaluate(t_oos, lev, COST_RT)
                r_free = evaluate(trades, lev, 0.0)   # zero-cost diagnostic
                rows.append((vname, lev, r_is, r_oos, r_all, r_free))

    def fmt(vname, lev, seg, r):
        if r is None:
            return f"{vname:<26}{lev:>4}{seg:>5}      (no trades)"
        fe = f"{r['final_eq']:.3f}" if False else (f"{r['final']:.3f}" if r['final'] < 1000 else f"{r['final']:.2e}")
        return (f"{vname:<26}{lev:>4}{seg:>5}{r['n']:>7}{r['wr']*100:>6.1f}{min(r['pf'],99.9):>6.2f}"
                f"{fe:>10}{r['pos_months']*100:>8.1f}{r['goal_months']*100:>9.1f}"
                f"{r['med_month']*100:>8.2f}{r['worst_month']*100:>10.1f}"
                + ("  BLOWN" if r["blown"] else ""))

    # sort families by IS final equity (selection metric), print all
    rows.sort(key=lambda x: -(x[2]["final"] if x[2] else 0))
    for vname, lev, r_is, r_oos, r_all, r_free in rows:
        print(fmt(vname, lev, "IS", r_is))
        print(fmt(vname, lev, "OOS", r_oos))
        print(fmt(vname, lev, "0fee", r_free))
        print()

    # summary
    print("=" * len(hdr))
    survivors = [r for r in rows if r[4] and r[4]["final"] > 1.0 and not r[4]["blown"]]
    goal_hits = [r for r in rows if r[4] and r[4]["goal_months"] >= 0.999]
    gross_edge = [r for r in rows if r[5] and r[5]["pf"] > 1.05]
    print(f"variants x leverage evaluated: {len(rows)}")
    print(f"profitable ALL-period after costs: {len(survivors)}")
    print(f"meeting the goal (>=34.8% EVERY month): {len(goal_hits)}")
    print(f"raw signals with PF>1.05 at ZERO cost: {len(gross_edge)}")


if __name__ == "__main__":
    main()
