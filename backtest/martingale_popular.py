"""martingale_popular.py — honest test of the POPULAR martingale-bot spec (2026-06-12).

User request: "build martingale bot with our RSI entry — good profit, lose
less? check Bybit / popular martingale strategy."

Spec modeled on Bybit Futures Martingale Bot + Pionex/3commas DCA defaults:
  - LONG rounds. Base order at round start (market, taker).
  - Safety order k: resting limit at last_fill*(1-dev); COST = prev cost x mult
    (Bybit multiplies last order COST, not qty). Maker fee on SO fills.
  - TP: conditional MARKET at avg_cost*(1+tp) (Bybit doc: market order) —
    fill at the TP trigger price (favorable gaps NOT credited), taker+slip.
  - NO stop loss (that is the martingale premise). Round ends at TP; next
    round starts immediately (always-on) or at next 1h RSI14<30 (RSI gate).
  - SIZING (the honest part commercial bots hide): the base order is sized so
    the FULL ladder exactly fits available capital x leverage. Returns are on
    TOTAL equity, not on the base order.
  - Spot mode (1x): no liquidation possible, equity rides the bag.
    Futures 3x: cross-margin, liquidation when MTM equity at bar low <=
    0.5% maintenance of open notional (pessimistic vs TP same bar).
  - Same-bar SO fill -> TP deferred to later bars (no wick-order lookahead).
  - MTM equity tracked at bar lows (cash + qty*low); maxDD reported on that.
  - Open basket at data end is marked to final close.
GRID (fixed a priori):
  dev {1, 2, 3}% x mult {1.5, 2.0} x maxSO {7, 10} x TP {1, 1.5, 2}%
  x entry {ALWAYS, RSI<30} x lev {1 (spot), 3}.
Data: BTC 1h 2019-2026. IS ..2023 / OOS 2024.. reported on round exits.
"""
import numpy as np
import pandas as pd

CSV = "/Users/jags/Desktop/BTC-Flip-Bot/data/cache/BTCUSDT_1h.csv"
MAKER, TAKER, SLIP = 0.0002, 0.00055, 0.0002
MAINT = 0.005
INITIAL = 5000.0
OOS_START = pd.Timestamp("2024-01-01")

DEVS = [0.01, 0.02, 0.03]
MULTS = [1.5, 2.0]
MAXSOS = [7, 10]
TPS = [0.01, 0.015, 0.02]


def wilder_rsi(close, n=14):
    d = close.diff()
    ag = d.clip(lower=0).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + ag / al)


def load():
    df = pd.read_csv(CSV, parse_dates=["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["rsi"] = wilder_rsi(df["close"])
    # ~200-day SMA on 1h bars (24*200) for the trend-gate variant
    df["sma200d"] = df["close"].rolling(4800).mean()
    return df


def ladder_cost_units(mult, n_so):
    """Total cost of base + SOs in units of the base order."""
    total, cost = 1.0, 1.0
    for _ in range(n_so):
        cost *= mult
        total += cost
    return total


def run(df, dev, mult, n_so, tp, rsi_gate, lev, round_sl=None, trend_gate=False):
    """round_sl: close the WHOLE basket (market, taker) when price <=
    avg*(1-round_sl) — converts the bag into realized losses. trend_gate:
    only START rounds while last closed bar > 200d SMA."""
    o, h, l, c = df["open"].values, df["high"].values, df["low"].values, df["close"].values
    rsi = df["rsi"].values
    sma = df["sma200d"].values if "sma200d" in df.columns else np.full(len(df), np.nan)
    ts = df["timestamp"].values
    n = len(df)
    units = ladder_cost_units(mult, n_so)

    cash = INITIAL
    pos = None            # dict: qty, cost, avg, last_fill, so_filled, so_costs
    rounds = []
    peak = INITIAL; max_dd = 0.0
    worst_unreal = 0.0    # worst MTM equity / committed peak, in %
    max_depth = 0
    liq = None
    armed = not rsi_gate  # always-on starts armed

    for i in range(n):
        if liq is not None:
            break
        if pos is None:
            if rsi_gate and not armed and not np.isnan(rsi[i - 1] if i else np.nan):
                if i > 0 and rsi[i - 1] < 30:
                    armed = True
            if trend_gate and (i == 0 or np.isnan(sma[i - 1]) or c[i - 1] <= sma[i - 1]):
                continue
            if armed and i > 0:
                base_cost = cash * lev / units
                e = o[i] * (1 + SLIP)
                qty = base_cost / e
                fee = base_cost * TAKER
                pos = {"qty": qty, "cost": base_cost, "fees": fee,
                       "last": e, "so": 0, "next_cost": base_cost * mult,
                       "bar": i, "so_bar": -1}
                armed = not rsi_gate
            continue

        # safety order fills (resting limits)
        so_filled_this_bar = False
        while pos["so"] < n_so:
            trig = pos["last"] * (1 - dev)
            if l[i] <= trig:
                add_cost = pos["next_cost"]
                qty_add = add_cost / trig
                pos["qty"] += qty_add
                pos["cost"] += add_cost
                pos["fees"] += add_cost * MAKER
                pos["last"] = trig
                pos["so"] += 1
                pos["next_cost"] = add_cost * mult
                pos["so_bar"] = i
                so_filled_this_bar = True
                max_depth = max(max_depth, pos["so"])
            else:
                break

        avg = pos["cost"] / pos["qty"]

        # account equity = cash + unrealized PnL - fees; leverage only caps
        # ladder size (units sizing) and sets the liquidation threshold
        eq_low_v = cash + pos["qty"] * l[i] - pos["cost"] - pos["fees"]
        worst_unreal = min(worst_unreal, (eq_low_v / cash - 1) * 100)
        if lev > 1 and eq_low_v <= MAINT * pos["qty"] * l[i]:
            liq = pd.Timestamp(ts[i])
            cash = max(eq_low_v, 0.0)
            break

        # round SL first (pessimistic), then TP — both deferred on SO-fill bars
        if not so_filled_this_bar and round_sl is not None:
            sl_px = avg * (1 - round_sl)
            if l[i] <= sl_px:
                fill = min(sl_px, o[i]) * (1 - SLIP)
                proceeds = pos["qty"] * fill
                fee_out = proceeds * TAKER
                pnl = proceeds - pos["cost"] - pos["fees"] - fee_out
                cash += pnl
                rounds.append({"ts": pd.Timestamp(ts[i]), "pnl": pnl,
                               "so": pos["so"], "hold_h": i - pos["bar"]})
                pos = None
                if cash > peak: peak = cash
        if pos is not None and not so_filled_this_bar:
            tp_px = avg * (1 + tp)
            if h[i] >= tp_px:
                proceeds = pos["qty"] * tp_px * (1 - SLIP)
                fee_out = proceeds * TAKER
                pnl = proceeds - pos["cost"] - pos["fees"] - fee_out
                cash += pnl
                rounds.append({"ts": pd.Timestamp(ts[i]), "pnl": pnl,
                               "so": pos["so"],
                               "hold_h": i - pos["bar"]})
                pos = None
                if cash > peak: peak = cash
        # MTM drawdown vs peak (use bar low)
        if pos is not None:
            eq_mtm = cash + pos["qty"] * l[i] - pos["cost"] - pos["fees"]
        else:
            eq_mtm = cash
        if eq_mtm > peak: peak = eq_mtm
        dd = (peak - eq_mtm) / peak
        if dd > max_dd: max_dd = dd

    # mark open basket to end
    end_equity = cash
    if pos is not None and liq is None:
        end_equity = (cash + pos["qty"] * c[n - 1] * (1 - TAKER - SLIP)
                      - pos["cost"] - pos["fees"])
    return {"rounds": rounds, "end": end_equity, "max_dd": max_dd * 100,
            "worst_unreal": worst_unreal, "max_depth": max_depth, "liq": liq,
            "open_bag": pos is not None}


def main():
    df = load()
    print(f"BTC 1h: {df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]} ({len(df):,} bars)")
    print(f"\n{'entry':<7}{'lev':>4}{'dev':>5}{'mult':>5}{'SO':>4}{'TP':>5} | "
          f"{'rounds':>7}{'WR%':>6}{'final$':>10}{'mtmDD%':>8}{'depth':>6}{'bag':>4}"
          f"{'liq':>12} | {'OOS rounds':>10}{'OOS pnl$':>9}")
    results = []
    for rsi_gate in (False, True):
        for lev in (1.0, 3.0):
            for dev in DEVS:
                for mult in MULTS:
                    for n_so in MAXSOS:
                        for tp in TPS:
                            r = run(df, dev, mult, n_so, tp, rsi_gate, lev)
                            rds = r["rounds"]
                            nt = len(rds)
                            wr = (sum(1 for x in rds if x["pnl"] > 0) / nt * 100) if nt else 0
                            oos = [x for x in rds if x["ts"] >= OOS_START]
                            oos_pnl = sum(x["pnl"] for x in oos)
                            liq_s = str(r["liq"].date()) if r["liq"] else "-"
                            print(f"{'RSI<30' if rsi_gate else 'ALWAYS':<7}{lev:>4.0f}{dev*100:>4.0f}%"
                                  f"{mult:>5.1f}{n_so:>4}{tp*100:>4.1f}% | {nt:>7}{wr:>6.1f}"
                                  f"{r['end']:>10,.0f}{r['max_dd']:>8.1f}{r['max_depth']:>6}"
                                  f"{'Y' if r['open_bag'] else '-':>4}{liq_s:>12} | "
                                  f"{len(oos):>10}{oos_pnl:>+9.0f}")
                            results.append((r["end"], rsi_gate, lev, dev, mult, n_so, tp, r))
    results.sort(key=lambda x: -x[0])
    e, g, lv, d, m, s, t, r = results[0]
    print(f"\nBEST final equity: {'RSI<30' if g else 'ALWAYS'} lev{lv:.0f} dev{d*100:.0f}% "
          f"mult{m} SO{s} TP{t*100:.1f}% -> ${e:,.0f} "
          f"(BTC buy-hold same period: ${INITIAL * df['close'].iloc[-1] / df['close'].iloc[0]:,.0f})")


if __name__ == "__main__":
    main()
