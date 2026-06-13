"""user_ladder_3pct9.py — user's requested config (2026-06-12):
dev 3% x 9 safety orders (covers ~24% below entry), cost multiplier,
TP from avg, entries ONLY on RSI14<30 (1h), validated engine.
Sweep: mult {1.2, 1.5, 2.0} x TP {1, 1.5, 2}% x lev {1 spot, 3}.
"""
import pandas as pd
import martingale_popular as mp

DEV, NSO = 0.03, 9
OOS = pd.Timestamp("2024-01-01")


def main():
    df = mp.load()
    days = (df["timestamp"].iloc[-1] - df["timestamp"].iloc[0]).days
    bh = mp.INITIAL * df["close"].iloc[-1] / df["close"].iloc[0]
    cover = (1 - (1 - DEV) ** NSO) * 100
    print(f"BTC 1h {df['timestamp'].iloc[0].date()} -> {df['timestamp'].iloc[-1].date()} "
          f"({days} days) | ladder covers {cover:.1f}% | buy-hold ${bh:,.0f} "
          f"(~{((bh/mp.INITIAL)**(1/days)-1)*100:.3f}%/day)")
    print(f"\n{'lev':>4}{'mult':>6}{'TP':>6}{'base$':>7} | {'rounds':>7}{'WR%':>6}"
          f"{'final$':>8}{'%/day':>8}{'mtmDD%':>8}{'depth':>6}{'bag$':>8}{'liq':>12} | {'OOS pnl$':>9}")
    for lev in (1.0, 3.0):
        for mult in (1.2, 1.5, 2.0):
            units = mp.ladder_cost_units(mult, NSO)
            base = mp.INITIAL * lev / units
            for tp in (0.01, 0.015, 0.02):
                r = mp.run(df, DEV, mult, NSO, tp, rsi_gate=True, lev=lev)
                rds = r["rounds"]
                nt = len(rds)
                wr = (sum(1 for x in rds if x["pnl"] > 0) / nt * 100) if nt else 0
                closed = mp.INITIAL + sum(x["pnl"] for x in rds)
                bag = r["end"] - closed
                gpd = ((max(r["end"], 1e-9) / mp.INITIAL) ** (1 / days) - 1) * 100
                oos_pnl = sum(x["pnl"] for x in rds if x["ts"] >= OOS)
                liq_s = str(r["liq"].date()) if r["liq"] else "-"
                print(f"{lev:>4.0f}{mult:>6.1f}{tp*100:>5.1f}%{base:>7.0f} | "
                      f"{nt:>7}{wr:>6.1f}{r['end']:>8,.0f}{gpd:>8.4f}{r['max_dd']:>8.1f}"
                      f"{r['max_depth']:>6}{bag:>+8.0f}{liq_s:>12} | {oos_pnl:>+9.0f}")


if __name__ == "__main__":
    main()
