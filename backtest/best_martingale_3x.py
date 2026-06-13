"""best_martingale_3x.py — GOAL CHECK (user 2026-06-12): best martingale at 3x;
can any config earn 1%/day?

Bar: 1%/day over this dataset (2,416 days) = 1.01^2416 = 2.7e10x — $5K would
become ~$135T. Bar restated honestly: report the MAX daily growth rate any
surviving 3x martingale config achieves, with its risk.

Sweep (validated engine, BTC 1h 2019-2026, full ladder capitalized, 3x,
liquidation on MTM lows):
  dev {1,2,3,5,8}% x mult {1.1,1.3,1.5,2.0} x SO {5,9,15,20}
  x TP {1,2,3}% x entry {ALWAYS, RSI14<30}  = 480 configs
"""
import pandas as pd
import martingale_popular as mp

OOS = pd.Timestamp("2024-01-01")
DEVS = [0.01, 0.02, 0.03, 0.05, 0.08]
MULTS = [1.1, 1.3, 1.5, 2.0]
NSOS = [5, 9, 15, 20]
TPS = [0.01, 0.02, 0.03]


def main():
    df = mp.load()
    days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days
    bh_gpd = ((df["close"].iloc[-1] / df["close"].iloc[0]) ** (1 / days) - 1) * 100
    print(f"BTC 1h, {days} days | buy-hold {bh_gpd:.4f}%/day | "
          f"1%/day bar over sample: {1.01**days:.2e}x")
    rows = []
    n_liq = 0
    for entry in (False, True):
        for dev in DEVS:
            for mult in MULTS:
                for nso in NSOS:
                    for tp in TPS:
                        r = mp.run(df, dev, mult, nso, tp, rsi_gate=entry, lev=3.0)
                        if r["liq"] is not None:
                            n_liq += 1
                            continue
                        end = max(r["end"], 1e-9)
                        gpd = ((end / mp.INITIAL) ** (1 / days) - 1) * 100
                        closed = mp.INITIAL + sum(x["pnl"] for x in r["rounds"])
                        rows.append({"entry": "RSI" if entry else "ALW", "dev": dev,
                                     "mult": mult, "nso": nso, "tp": tp, "end": end,
                                     "gpd": gpd, "dd": r["max_dd"],
                                     "bag": r["end"] - closed,
                                     "depth": r["max_depth"],
                                     "oos": sum(x["pnl"] for x in r["rounds"] if x["ts"] >= OOS)})
    rows.sort(key=lambda x: -x["gpd"])
    total = n_liq + len(rows)
    print(f"\nConfigs: {total} | LIQUIDATED: {n_liq} ({n_liq/total*100:.0f}%) | survivors: {len(rows)}")
    print(f"\n── TOP 15 surviving 3x configs by %/day ──")
    print(f"{'entry':<6}{'dev':>5}{'mult':>6}{'SO':>4}{'TP':>5} | {'final$':>9}"
          f"{'%/day':>8}{'mtmDD%':>8}{'depth':>6}{'bag$':>8}{'OOS$':>8}")
    for r in rows[:15]:
        print(f"{r['entry']:<6}{r['dev']*100:>4.0f}%{r['mult']:>6.1f}{r['nso']:>4}"
              f"{r['tp']*100:>4.0f}% | {r['end']:>9,.0f}{r['gpd']:>8.4f}{r['dd']:>8.1f}"
              f"{r['depth']:>6}{r['bag']:>+8.0f}{r['oos']:>+8.0f}")
    if rows:
        b = rows[0]
        print(f"\nBEST: {b['gpd']:.4f}%/day — {1.0/b['gpd'] if b['gpd']>0 else float('inf'):.0f}x "
              f"short of 1%/day. Buy-hold same period: {bh_gpd:.4f}%/day.")


if __name__ == "__main__":
    main()
