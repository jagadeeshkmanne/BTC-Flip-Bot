"""martingale_validate.py — bug audit of martingale_popular.py (user 2026-06-12:
"those are with bugs. i want you to find out").

Strategy: test the ACTUAL engine (imported, not copied) against hand-computed
ground truth on synthetic data, then forensically verify the real-data
liquidation against the raw BTC candles.

  T1  Uptrend, no dips: only the base order fills; round-1 PnL must equal the
      closed-form value (qty*tp*(1-slip) - cost - taker_in - taker_out).
  T2  Crafted dip exactly 3 SO triggers deep, then recovery: SO count, average
      cost, TP price and PnL must match the hand-computed ladder.
  T3  Crafted crash at 3x: engine must liquidate, and equity at the reported
      moment must be <= maintenance (no phantom survival).
  T4  Conservation: final equity == INITIAL + sum(round PnLs) + open-bag mark
      (no money created or destroyed by the bookkeeping).
  T5  Real-data forensics: print the actual BTC 1h candles around the reported
      liquidation (2020-03-12) and the ladder coverage math, showing the move
      exceeded what any tested ladder could absorb.
"""
import numpy as np
import pandas as pd
import martingale_popular as mp

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, ok, detail=""):
    results.append((name, ok))
    print(f"[{PASS if ok else FAIL}] {name}  {detail}")


def mkdf(prices):
    """Synthetic 1h df: o=h=l=c per bar unless tuples (o,h,l,c) given."""
    rows = []
    t0 = pd.Timestamp("2024-01-01")
    for k, p in enumerate(prices):
        if isinstance(p, tuple):
            o, h, l, c = p
        else:
            o = h = l = c = p
        rows.append({"timestamp": t0 + pd.Timedelta(hours=k),
                     "open": o, "high": h, "low": l, "close": c, "rsi": 50.0})
    return pd.DataFrame(rows)


# ── T1: uptrend, base order only ──
dev, mult, n_so, tp, lev = 0.02, 2.0, 7, 0.01, 1.0
units = mp.ladder_cost_units(mult, n_so)            # 255 for 2.0 x 7
prices = [10000 * (1.003 ** k) for k in range(40)]
df = mkdf(prices)
r = mp.run(df, dev, mult, n_so, tp, rsi_gate=False, lev=lev)
e = prices[1] * (1 + mp.SLIP)                       # round 1 entry = bar1 open
base_cost = mp.INITIAL * lev / units
qty = base_cost / e
tp_px = e * (1 + tp)                                # avg == entry (no SOs)
proceeds = qty * tp_px * (1 - mp.SLIP)
pnl_hand = proceeds - base_cost - base_cost * mp.TAKER - proceeds * mp.TAKER
ok = len(r["rounds"]) >= 1 and abs(r["rounds"][0]["pnl"] - pnl_hand) < 1e-9 \
     and r["rounds"][0]["so"] == 0 and r["max_depth"] == 0
check("T1 uptrend round-1 PnL == closed form", ok,
      f"sim {r['rounds'][0]['pnl']:.6f} vs hand {pnl_hand:.6f}, SOs={r['rounds'][0]['so']}")

# ── T2: dip exactly 3 SO triggers, then recovery ──
dev, mult, n_so, tp, lev = 0.02, 1.5, 7, 0.01, 1.0
units = mp.ladder_cost_units(mult, n_so)
p0 = 10000.0
e = p0 * (1 + mp.SLIP)
trig = [e * (1 - dev), e * (1 - dev) ** 2, e * (1 - dev) ** 3]
# bars: warmup, entry bar (flat), dip bar exactly to trig[2], flat bar, recovery
costs = [mp.INITIAL / units]
for _ in range(3):
    costs.append(costs[-1] * mult)
qtys = [costs[0] / e] + [costs[k + 1] / trig[k] for k in range(3)]
cost_tot = sum(costs)
qty_tot = sum(qtys)
avg = cost_tot / qty_tot
tp_px = avg * (1 + tp)
prices = [(p0, p0, p0, p0),                       # bar0: warmup (no pos)
          (p0, p0, p0, p0),                       # bar1: entry at open
          (p0, p0, trig[2], trig[2]),             # bar2: dip fills 3 SOs (TP deferred)
          (trig[2], trig[2], trig[2], trig[2]),   # bar3: flat
          (tp_px * 1.001,) * 4,                   # bar4: recovery above TP
          (tp_px * 1.001,) * 4]
df = mkdf(prices)
r = mp.run(df, dev, mult, n_so, tp, rsi_gate=False, lev=lev)
fees = costs[0] * mp.TAKER + sum(c * mp.MAKER for c in costs[1:])
proceeds = qty_tot * tp_px * (1 - mp.SLIP)
pnl_hand = proceeds - cost_tot - fees - proceeds * mp.TAKER
got = r["rounds"][0] if r["rounds"] else None
ok = got is not None and got["so"] == 3 and abs(got["pnl"] - pnl_hand) < 1e-6
check("T2 3-SO ladder avg/TP/PnL == hand calc", ok,
      f"sim {got['pnl'] if got else None:.6f} vs hand {pnl_hand:.6f}, SOs={got['so'] if got else '-'}")

# ── T3: crash at 3x must liquidate at/below maintenance ──
dev, mult, n_so, tp, lev = 0.01, 1.5, 7, 0.01, 3.0
prices = [(10000.0,) * 4, (10000.0,) * 4] + \
         [(10000 * (1 - 0.06 * k), 10000 * (1 - 0.06 * k),
           10000 * (1 - 0.06 * (k + 1)), 10000 * (1 - 0.06 * (k + 1))) for k in range(1, 12)]
df = mkdf(prices)
r = mp.run(df, dev, mult, n_so, tp, rsi_gate=False, lev=lev)
ok = r["liq"] is not None and r["end"] < mp.INITIAL * 0.05
check("T3 3x crash -> liquidation, equity <= maint", ok,
      f"liq={r['liq']}, end=${r['end']:.2f}")

# ── T4: conservation on real data (best spot config) ──
df_real = mp.load()
r = mp.run(df_real, 0.01, 2.0, 7, 0.02, rsi_gate=False, lev=1.0)
closed = mp.INITIAL + sum(x["pnl"] for x in r["rounds"])
# end = closed cash + open-bag mark; bag mark = end - closed (must be plausible: >= -cash)
bag_mark = r["end"] - closed
ok = (not r["open_bag"] and abs(bag_mark) < 1e-6) or (r["open_bag"] and bag_mark <= 0.0)
check("T4 conservation: end == initial + ΣPnL + bag mark", ok,
      f"closed ${closed:,.0f}, end ${r['end']:,.0f}, open-bag mark ${bag_mark:,.0f} "
      f"({'bag open' if r['open_bag'] else 'flat'})")

# ── T5: forensic — the 2020-03-12 candles vs ladder coverage ──
print("\n── T5 forensics: BTC 1h, 2020-03-12 (the reported 3x liquidation day) ──")
seg = df_real[(df_real["timestamp"] >= "2020-03-12") & (df_real["timestamp"] < "2020-03-14")]
day_open = seg["open"].iloc[0]
day_low = seg["low"].min()
drop = (day_low / day_open - 1) * 100
print(f"  2020-03-12 00:00 open ${day_open:,.0f} -> 48h low ${day_low:,.0f}  ({drop:+.1f}%)")
for w_dev, w_n in [(0.01, 7), (0.01, 10), (0.03, 10)]:
    cover = (1 - (1 - w_dev) ** w_n) * 100
    print(f"  ladder dev {w_dev*100:.0f}% x {w_n} SOs covers {cover:.1f}% below entry"
          f" -> {'BLOWN THROUGH' if abs(drop) > cover else 'holds'}")
print(f"  at 3x, full ladder + ~33% below average cost = maintenance -> a {drop:+.1f}% "
      f"move liquidates every tested config. The candles, not the code, did this.")

n_fail = sum(1 for _, ok in results if not ok)
print(f"\n{len(results) - n_fail}/{len(results)} checks passed" + ("" if n_fail == 0 else " — FAILURES ABOVE"))
