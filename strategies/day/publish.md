# Adaptive S/R Reversal - RSI Divergence + DCA + BE Stop (5m)

> **Designed for: BTCUSDT perpetual · 5-minute timeframe · only.**
> Other symbols and timeframes have not been validated and may behave very
> differently. The script enforces 5m and shows a warning on any other TF.

A 5-minute mean-reversion strategy for **BTCUSDT perpetual futures** that
fades touches of yesterday's high/low when momentum confirms via fresh RSI
divergence. Averages down once if price moves further against, exits at
yesterday's midpoint pre-DCA or a fixed % from first entry post-DCA. Closes
flat at 20:00 UTC daily — unless the trade is favorable enough to ride
overnight (24-hour hard cap).

---

## Strategy Logic

### Entry — touch + momentum exhaustion (no trend bias)

- **LONG** when 5m bar low touches prev_day low (within 0.05% zone) AND a
  fresh **bullish RSI divergence** (price LL, RSI HL) was confirmed within
  the last 20 bars.
- **SHORT** when 5m bar high touches prev_day high (within 0.05%) AND a
  fresh **bearish RSI divergence** (price HH, RSI LH) was confirmed within
  the last 20 bars.
- Both directions allowed regardless of higher-timeframe trend. The
  divergence + S/R AND-filter is the edge — neither alone is sufficient.

### Filters

- **Volume confirmation**: entry bar volume ≥ 1.1× SMA(20)
- **Adaptive S/R range**: when prev_day range < 2% (squeeze day), expand
  lookback to a 2-day rolling H/L band so the R:R doesn't collapse
- **One cycle per UTC day** (configurable up to 5)
- **Trade window**: entries blocked after 20:00 UTC

### DCA

- 1 add at `worst_entry × (1 ∓ 0.85%)` — total 2 legs per cycle
- Sizing: `riskPct` of equity is split across the configured DCA levels
- Per-leg quantity capped by `leverage × equity / close / dcaLevels`

### Exits

- **TP (hybrid, default)**: `prev_mid × (1 ∓ 0.1%)` pre-DCA → switches to
  `firstEntry × (1 ∓ 4%)` once DCA fires (avg entry has shifted, so the
  post-DCA leg can ride a wider target).
- **SL**: `worst_entry × (1 ∓ 2%)` raw. Once BE arms (+1% favorable from
  first entry), tightens to `firstEntry × (1 ± 0.25%)` — locks in real
  ~0.20% net after fees.
- **EOD flatten**: closes at 20:00 UTC unless trade is ≥ +1.5% favorable
  from first entry → holds another 24 hours max.

---

## Best Configuration (defaults — tested on BTC 5m)

| Setting | Default | When to deviate |
|---|---|---|
| **Risk per cycle** | **6%** | 4% for safer; 8% for aggressive. At 6% a worst-case full-DCA SL costs ~6% of equity. |
| **Leverage cap** | **2×** | Higher than 2× tested no improvement, mostly amplifies bad days. |
| **DCA levels** | **2** | More legs ≠ better. 3+ stretches SL distance, slower recovery. |
| **DCA spacing** | **0.85%** | Tighter (0.6%) fills more often but burns through SL faster. Wider (1.2%) rarely fills. |
| **SL below worst** | **2.0%** | Pairs with BE-stop ON. Tighter (1.4%) noise-stops trades that would have made BE; wider gives no extra return. |
| **S/R touch zone** | **0.05%** | Wider zones (0.2%) fire on near-misses, hurt WR. Selective is the point. |
| **Volume × avg** | **1.1×** | With divergence ON, volume filter is largely redundant. 1.0 lets a few more in; 1.5 over-filters. |
| **RSI period** | **14** | Standard. Faster (7) noise; slower (21) stale. |
| **Pivot left/right** | **5 / 5** | 3/3 catches noise pivots. 7/7 misses real swings. |
| **Divergence freshness** | **20 bars** | ~100 min on 5m. 10 misses good entries; 50 lets stale signals through. |
| **Range floor** | **2.0%** | Below 2% the R:R collapses. Higher floor = pickier but fewer trades. |
| **Max lookback (extend)** | **2 days** | 3+ rarely improves things; 1d squeezes are usually short-lived. |
| **TP mode** | **hybrid** | `prev_mid` is more conservative but caps post-DCA upside. |
| **Post-DCA TP %** | **4%** | Tested 2% / 3% / 4% / 5% — 4% gave best balance of hit rate vs win size. |
| **BE trigger** | **1.0%** | Earlier (0.5%) over-triggers and stops out small wins. Later (1.5%) gives back more on reversals. |
| **BE buffer** | **0.25%** | Covers 2× taker fee + slippage cushion. Below this, BE locks in a loss. |
| **EOD close hour** | **20:00 UTC** | 23:00 lets trades work longer at the cost of overnight risk. |
| **Hold-past-EOD** | **ON @ 1.5%** | Captures runners that EOD would otherwise cut. 1.0% triggers too often; 2.0% rarely qualifies. |
| **Cycles per day** | **1** | More cycles = N× SL exposure on trend days. The bot's edge is selectivity. |

### Conservative variant (lower DD)

If you prioritize drawdown over return:

```
Risk per cycle:    4%  (was 6%)
Leverage cap:      1.5x  (was 2x)
SL below worst:    1.5% (was 2.0%)
Max cycles/day:    1
TP mode:           prev_mid (instead of hybrid — caps upside)
```

### Aggressive variant (higher return, higher DD)

```
Risk per cycle:    8%
Leverage cap:      3x
TP post-DCA:       6% (lets winners run further)
Hold-past-EOD:     ON @ 1.0% (more frequent overnight holds)
```

---

## ⚠ Regime Warning — Read This Before Going Live

The strategy is **regime-favorable, not regime-robust**.

**It works** when daily H/L levels consistently revert to the mid (chop,
range-bound markets, mean-reverting volatility).

**It loses** in strong trending markets where price punches through
prev_day levels and keeps going — every losing trend day costs a full SL,
and the small mean-reversion wins don't pay for them.

**Do NOT** size up based on a few good weeks. Paper-trade for at least 2-4
weeks across different regimes before deploying real capital. Short-window
backtests are real but NOT predictive — they're regime artifacts.

---

## TradingView Backtest

The included strategy script will give you live backtest numbers when you
load it on **BINANCE:BTCUSDT.P 5m**. Numbers vary substantially across
windows — that's the point of the regime warning above. Note that
TradingView's strategy tester assumes intra-bar fills that may not happen
in live trading without resting orders, so live results will likely be
more conservative than tester output.

---

## How to Use

1. **BTCUSDT.P (perp) only** — params are tuned to BTC's volatility profile.
   Spot has different funding/fee structure; alts have different ranges.
2. **5m chart only** — script enforces this and warns on other TFs.
3. **Paper trade first.** Use TV alerts → paper account on Binance Futures
   testnet, or a dedicated paper-trading bot, for at least 2-4 weeks.
4. Watch the on-chart dashboard (top-right) for live state: position, prev
   levels, active TP/SL, BE status, EOD hold status.
5. Don't change parameters mid-trade. Re-tune only after a full month of
   data on a new config.

---

## What's Plotted

- 🟢 **prev_day Low** (dim green dotted)
- 🔴 **prev_day High** (dim red dotted)
- 🔵 **prev_day Mid / TP target** (cyan dotted)
- 🔴 **Active SL** (solid red, 2px)
- 🔵 **Active TP** (solid cyan, 2px)
- 🟡 **Avg entry** (yellow dotted, after DCA fires)
- 🟣 **1h EMA(20)** (purple, visual reference only — not used for entries)
- ▲/▼ **Entry triangle markers** when L1/S1 conditions fire
- 💎 **Bull/Bear divergence diamonds** at the pivot bar (offset back)
- 📋 **Top-right dashboard**: position, prev levels, UTC hour, TP mode,
  filled count, BE status, RSI div age, equity, EOD-hold status

---

## Tested + Rejected (Anti-Recipes)

These were tried during development and removed — saving you the experiments:

- **Trend bias filter (1h EMA)** — gating LONG to bull bias and SHORT to
  bear bias seemed safer, but in practice price reaching prev_L always
  coincided with bias flipping bear, blocking 60%+ of valid entries.
- **RSI anti-extreme filter as primary gate** (skip LONG if RSI<25, etc.) —
  redundant with divergence; rejects the same entries.
- **Multi-RSI variants** (fast 7 / slow 21) — fast fired on noise, slow on
  stale moves. RSI(14) is the sweet spot.
- **Trailing stop** (post-DCA or full trail) — too many false trail-outs
  near peaks. BE-stop does the job cleaner.
- **Wider S/R zone** (0.2%) — fired on near-misses, hurt WR.
- **SL on candle close** (only fire SL when 5m bar closes beyond SL) —
  saves wick stops but gives more rope on real trends; net negative.
- **Multi-cycle per day** (2-3 cycles) — same-day re-entries whipsaw on
  trend days. 1 cycle/day is the validated default.
- **Always-in-market flip on every divergence** (no S/R touch required) —
  built and tested; loses. Divergence alone is not an edge — the AND with
  S/R touch is.
- **Recycling grid sub-strategies** — tested 4 grid variants (neutral,
  long-only, short-only, auto-bias). All ≤ +1.6% vs S/R-baseline +33%
  on the same window. Slippage + risk amplification on dip-buy rungs
  destroy the math.
- **Wait-for-HTF-rejection-candle** confirm filter — cuts return ~60% for
  marginal DD improvement.

---

## TradingView Publishing

**Type**: Strategy
**Category**: Support and Resistance · Cycles
**Tags**: `bitcoin` `btc` `btcusdt` `5min` `5m` `day trading` `intraday`
`mean reversion` `support resistance` `s/r` `pivot` `dca` `rsi divergence`
`breakeven` `take profit` `stop loss` `eod` `binance futures` `perpetual`

---

## Disclaimer

Backtest results don't predict live performance. Slippage, funding fees,
exchange downtime, latency, and live order routing all affect real-world
returns. The TradingView backtester assumes intra-bar fills that may not
happen in live trading without resting orders.

This script is shared for **educational and research purposes**. Use it as
one input in your own due diligence — never as a turn-key money-printer.
Past performance is not indicative of future results, especially for a
strategy this regime-dependent.
