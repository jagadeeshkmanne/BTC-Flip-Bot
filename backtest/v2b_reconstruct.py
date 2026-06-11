"""V2b 'Structure Break + SL-Flip' reconstruction — honest daily backtest.

Original Pine file is not on this machine. Reconstructed from project records:
  - 1D BTCUSDT, EMA50 bias gate (long-only above, short-only below)
  - entry: close breaks prior N-day high/low in bias direction (structure break)
  - exit:  Donchian-style trail at prior (N/2)-day extreme, ratcheting only
  - SL-Flip: on stop-out, if bias now agrees with the opposite side, reverse
ASSUMPTIONS (not in records): N value, trail=N/2 channel, all-in 1x sizing.
We therefore sweep N in {10,20,30,55} and report flip on/off — looking for a
ROBUST family, not one good cell.

Honest mechanics: signal on daily close -> fill next open; stop fill = worse
of stop and open; taker fee + slip on entries/stops (stops are market-ish);
mark-to-market DD from daily adverse extremes; compounded 1x equity.
"""
import pandas as pd
import numpy as np

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1d.csv"
INITIAL = 5000.0
FEE_MODES = {"0-fee": (0.0, 0.0), "REAL": (0.00055, 0.0002)}  # (fee/side, slip)


def run(df, N, flip, fee, slip):
    ema50 = df["close"].ewm(span=50, adjust=False).mean().values
    hi_n = df["high"].rolling(N).max().shift(1).values
    lo_n = df["low"].rolling(N).min().shift(1).values
    M = max(N // 2, 2)
    ex_lo = df["low"].rolling(M).min().shift(1).values
    ex_hi = df["high"].rolling(M).max().shift(1).values
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    years = df["timestamp"].dt.year.values

    bal = INITIAL
    pos = None; pend = None
    peak_mtm = INITIAL; max_dd = 0.0
    trades = []; ybal = {}

    for i in range(len(df)):
        if np.isnan(hi_n[i]) or np.isnan(ema50[i]):
            continue
        # 1) pending entry fills at open
        if pos is None and pend is not None:
            side = pend; pend = None
            eff = o[i] * (1 + slip) if side == "L" else o[i] * (1 - slip)
            qty = bal / eff
            bal -= eff * qty * fee
            stop = ex_lo[i] if side == "L" else ex_hi[i]
            pos = {"side": side, "qty": qty, "avg": eff, "stop": stop, "yr": years[i]}

        if pos is not None:
            # 2) ratchet trail
            if pos["side"] == "L":
                pos["stop"] = max(pos["stop"], ex_lo[i])
                hit = l[i] <= pos["stop"]
                fill = min(pos["stop"], o[i])
            else:
                pos["stop"] = min(pos["stop"], ex_hi[i])
                hit = h[i] >= pos["stop"]
                fill = max(pos["stop"], o[i])
            if hit:
                eff = fill * (1 - slip) if pos["side"] == "L" else fill * (1 + slip)
                gross = (eff - pos["avg"]) * pos["qty"] if pos["side"] == "L" else (pos["avg"] - eff) * pos["qty"]
                bal += gross - eff * pos["qty"] * fee
                trades.append({"net": gross, "yr": years[i]})
                old = pos["side"]; pos = None
                if flip:  # bias check at this close: reverse if regime agrees
                    if old == "L" and c[i] < ema50[i]:
                        pend = "S"
                    elif old == "S" and c[i] > ema50[i]:
                        pend = "L"
            else:
                adv = l[i] if pos["side"] == "L" else h[i]
                unreal = (adv - pos["avg"]) * pos["qty"] if pos["side"] == "L" else (pos["avg"] - adv) * pos["qty"]
                eq = bal + unreal
                fav = h[i] if pos["side"] == "L" else l[i]
                ufav = (fav - pos["avg"]) * pos["qty"] if pos["side"] == "L" else (pos["avg"] - fav) * pos["qty"]
                peak_mtm = max(peak_mtm, bal + ufav)
                max_dd = max(max_dd, (peak_mtm - eq) / peak_mtm)
        else:
            peak_mtm = max(peak_mtm, bal)
            max_dd = max(max_dd, (peak_mtm - bal) / peak_mtm)

        # 3) signal at close
        if pos is None and pend is None:
            if c[i] > ema50[i] and c[i] > hi_n[i]:
                pend = "L"
            elif c[i] < ema50[i] and c[i] < lo_n[i]:
                pend = "S"
        ybal[years[i]] = bal

    wins = [t for t in trades if t["net"] > 0]
    gl = sum(t["net"] for t in trades if t["net"] < 0)
    gw = sum(t["net"] for t in wins)
    yrs_total = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days / 365.25
    cagr = ((bal / INITIAL) ** (1 / yrs_total) - 1) * 100 if bal > 0 else -100.0
    return {"final": bal, "ret": (bal / INITIAL - 1) * 100, "cagr": cagr,
            "pf": abs(gw / gl) if gl < 0 else float("inf"),
            "dd": max_dd * 100, "trades": len(trades),
            "wr": len(wins) / len(trades) * 100 if trades else 0,
            "trade_list": trades, "ybal": ybal}


df = pd.read_csv(CSV, parse_dates=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
print(f"Data: {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()}  ({len(df)} daily bars)")
bh = (df["close"].iloc[-1] / df["open"].iloc[0] - 1) * 100
print(f"Buy-and-hold same window: {bh:+,.0f}%  (max DD ~77% in 2022)\n")

for fname, (fee, slip) in FEE_MODES.items():
    print(f"══ fees: {fname} ══")
    print(f"  {'N':>3} {'flip':>5} {'trades':>7} {'WR':>6} {'PF':>6} {'return':>10} {'CAGR':>7} {'maxDD':>7}")
    for N in (10, 20, 30, 55):
        for flip in (True, False):
            r = run(df, N, flip, fee, slip)
            print(f"  {N:>3} {str(flip):>5} {r['trades']:>7} {r['wr']:>5.0f}% {r['pf']:>6.2f} "
                  f"{r['ret']:>+9.0f}% {r['cagr']:>+6.1f}% {r['dd']:>6.1f}%")
    print()

# year-wise for the headline cell (N=20, flip=True, REAL fees)
r = run(df, 20, True, 0.00055, 0.0002)
print("Year-wise net $ (N=20, flip=True, REAL fees):")
byy = {}
for t in r["trade_list"]:
    byy.setdefault(t["yr"], []).append(t["net"])
for y in sorted(byy):
    w = sum(1 for x in byy[y] if x > 0)
    print(f"  {y}: {len(byy[y]):>3} trades  {w}/{len(byy[y])} wins  net ${sum(byy[y]):+,.0f}")
