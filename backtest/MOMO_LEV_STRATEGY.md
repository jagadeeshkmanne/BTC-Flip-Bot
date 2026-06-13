# MOMO-LEV v1 — max-honest-daily-rate BTC strategy (2026-06-11)

Built in response to the goal "1% daily profit." This is the closest any
honest configuration gets; the gap to the goal is documented at the bottom.

## Rules

Two capital states, decided once per UTC daily close, on closed bars only:

1. **DEPLOYED (momentum long, 2x perp):** when daily close > SMA200 AND
   RSI14 > 70 on any of the last 7 closed daily bars (= MOMO v1 signal),
   open BTCUSDT perp long at 2x leverage, full equity as margin, filled at
   next-day open. Exit to harvest state when the signal lapses, filled at
   next-day open. No stops (tested: stops reduce the rate and don't prevent
   high-leverage liquidations), no DCA, no grid.
2. **HARVEST (flat days, ~81% of the time):** long spot + 1x perp short,
   collecting funding (~8% APR at current rates). External cash flow, no
   prediction, no liquidation risk at 1x.

Costs modeled honestly: 0.055% taker per side on notional, funding paid
0.01%/8h while long, liquidation checked against intraday lows, next-open
fills, no stop-price fictions.

## Honest backtest (BTCUSDT 1d, Sep 2019 – Jun 2026, momo_lev_sweep.py)

| Config | g/day | CAGR | maxDD | Liqs |
|---|---|---|---|---|
| L=1 + harvest | 0.094% | 41% | 31% | 0 |
| **L=2 + harvest (recommended)** | **0.148%** | **71%** | **54%** | 0 |
| L=3 + harvest | 0.188% | 98% | 68% | 0 |
| L=5 + harvest | 0.238% | 138% | 85% | 0 (luck — see below) |
| L=6+ | −100% | — | 100% | liquidated |

- Worst per-trade adverse excursion: **17.8%** vs 19.5% liq buffer at 5x.
  One slightly deeper wick = total loss. Max design-safe leverage ≈ 2-3x
  (buffer ≥ 2x worst observed MAE → L=2: 49.5% buffer).
- 2021+ window (excluding the 2020 mega-bull): L=2 → 0.099%/day, so expect
  the lower number going forward (edge is decaying, FINDINGS #4).
- Intraday stop-losses at 5/8/10%: do NOT unlock L≥8 (overnight gaps go
  through the stop to liquidation) and cut g/day at survivable leverage.
- Caveats: harvest↔momo transition fees not modeled (~0.2%/yr drag at 22
  trades/6.2y); funding assumed constant at long-run averages; paper reads
  slightly better than real (FINDINGS #6 applies).

## Verdict vs the 1%/day goal

The honest deployable ceiling is **~0.10–0.15%/day** (L=2), or ~0.24%/day
at uninsurable liquidation risk. 1%/day (≈ 3,678%/yr compounded) is 4–10x
beyond the best honest edge found in this repo across 150+ scalp combos,
7 exit families, 5 grid attempts, and now the full leverage frontier of the
only validated daily edge. Every strategy ever observed "achieving" it
contained a fill fiction or fee blindness (FINDINGS #1-#3). This strategy
is the maximum that survives honest accounting.
