# S/R DCA Day Trader (5m)

> **Designed for: BTCUSDT · 5-minute timeframe · only.**
> Backtested + tuned + live-tested on BTC perpetual futures. Other symbols and timeframes have not been validated and may behave very differently. The script enforces 5m and shows a warning on any other TF.

A **5-minute support/resistance DCA day trader** for **BTCUSDT perpetual futures**. Buys at yesterday's low, shorts at yesterday's high, averages down once if price moves further against, and exits at the midpoint of yesterday's range. Closes flat at 20:00 UTC every day — never holds overnight.

---

## 📊 Live Testnet Performance

This strategy runs **24/7 on Binance Futures Testnet** so you can verify it works on live data before deploying:

🔗 **Live Dashboard**: http://34.14.124.215:8888/dashboard.html?env=testnet

The dashboard shows:
- Current position (entry, mark, PnL, leverage)
- Resting TP / SL / DCA levels on Binance
- Realized PnL from completed trades (net of funding fees)
- Strategy equity curve vs Buy & Hold benchmark
- Win rate, drawdown, holding-period stats
- Cycle countdown (next entry window)

The bot runs every 5 min via cron — testnet positions are visible on Binance Testnet UI in real time.

---

## Strategy Logic

### Entry (no bias filter)
- **LONG** when 5m bar low touches prev_day's low (within 0.05% zone)
- **SHORT** when 5m bar high touches prev_day's high (within 0.05% zone)
- Both sides allowed regardless of trend direction

### Filters at entry
- **RSI anti-extreme** — skip LONG if RSI < 25, skip SHORT if RSI > 75 (avoids catching falling/flying knives)
- **Volume confirmation** — entry bar volume must exceed 1.2× 20-bar SMA
- **1-cycle-per-day cap** — no re-entry after the cycle closes

### DCA (1 extra leg by default)
- For LONG: adds at `worst_entry × (1 − 0.8%)`
- For SHORT: adds at `worst_entry × (1 + 0.8%)`
- Sizing: total `riskPct` of equity is split across the configured DCA levels

### Exits
- **TP** (smart, default): picks whichever of `prev_day midpoint` or `avg_entry × (1 ∓ 0.5%)` is **closer** to current price (easier to hit). Recomputes after each DCA leg, so TP adapts to your real avg entry instead of being stuck at a fixed S/R level. Other modes available: `prev_mid` (fixed S/R anchor), `prev_extreme` (full prev_day range), `fixed_pct` (N% from first entry).
- **SL**: `worst_entry × (1 ∓ 2%)` — stays tight to the worst leg, moves with each DCA fill so the trade has room to recover
- **EOD flatten**: closes any open position at 20:00 UTC

---

## Default Settings

| Setting | Default | Notes |
|---|---|---|
| Risk per cycle | 6% | Adjustable 0.1%–15% |
| Leverage cap | 2× | Prevents oversizing |
| DCA levels | 2 | 1 entry + 1 add |
| DCA spacing | 0.8% | Tighter = fills DCA more often |
| S/R touch zone | 0.05% | Selective — only direct touches |
| SL below worst entry | 2% | Static |
| Close hour | 20:00 UTC | Force flatten |
| RSI filter | 25 / 75 | Anti-extreme |
| Volume × avg | 1.2 | Above 20-bar SMA |
| TP mode | prev_mid | or `prev_extreme` / `fixed_pct` |

---

## Backtest Results

### TradingView (BTCUSDT 5m, Mar 23 → May 1, 2026):
- **Default (prev_mid + 0.1% offset)**: +35.25% / PF 3.92 / DD 4.80% / 67% WR / 46 trades / Calmar 7.34
- **Fixed 4% TP variant**: +35.20%

### ⚠️ Regime warning — important

A separate 2.3-year Python backtest of the same logic returned **−72.78% / PF 0.91 / 82% drawdown / 617 cycles**. The strategy is **regime-favorable, not regime-robust**: it works well when prev_day H/L touches consistently revert to the midpoint, but breaks down in strong trending markets where price punches through prev_day levels and keeps going.

**What this means:**
- Recent performance doesn't guarantee future performance — it depends heavily on the current market regime
- Paper-trade or testnet only until you've watched it through several different market conditions (see the live testnet link above)
- Don't size up based on the 5-week backtest alone

---

## How to Use

1. **BTCUSDT only** — params are tuned to BTC's volatility profile, daily range, and S/R behavior. ETH, SOL, alts, and stocks have different ranges and have NOT been tested.
2. **5m chart only** — the strategy enforces this and shows a wrong-TF warning if you switch. Other timeframes will produce different prev_day touch frequency, RSI behavior, and DCA fill rate.
3. Default settings are tuned for BTCUSDT; adjust risk % to your tolerance.
4. Watch the on-chart dashboard (top-right): position state, filled DCA legs, prev_day levels, cycle status.
5. The 1h EMA20 line is shown for visual context only — entries don't gate on bias (the bias filter was tested and removed because it blocked 60%+ of valid S/R entries).
6. **Watch the live testnet dashboard** (link at top) to see actual order flow + slippage on a real exchange before going live.

---

## Design Decisions

A few things I tested and rejected during development — saving you from re-running the same experiments:

- **Trend bias filter (1h EMA20)** — gating LONG to bull bias and SHORT to bear bias seemed safer, but in practice price reaching prev_L on a SHORT entry day always coincided with bias flipping bear, blocking 60%+ of valid entries. Removed.
- **Inverted RSI (require extremes)** — the opposite of the current 25/75 filter. Backtest worse; current anti-extreme version wins.
- **1h RSI** — slower than the 5m RSI. Filtered too few entries to matter.
- **Range gate** — required prev_day range > N% before allowing entries. Reduced trade count without improving win rate.
- **Trailing stop** — too many false trail-outs near peaks. Static SL at worst-entry × (1 ± 2%) outperformed.
- **Wider S/R zone** (0.2%) — fired on near-misses, hurt the win rate. Tight 0.05% only fires on direct touches.
- **Tighter / looser SL** (1.5%, 2.5%) — tested both directions vs the 2% baseline. Neither improved Calmar; 2% is the sweet spot for BTC 5m.
- **Breakeven SL** (move SL to entry once trade is +1% profit) — tested, no meaningful improvement on the Mar 23–May 1 window. Most losers don't first reach +1% profit, so BE rarely arms.
- **SL on candle close** (only fire SL when 5m bar closes beyond SL, filtering wicks) — tested, increased DD. Saves some false-wick stops but gives more rope on real trending moves, and the bigger trending-day losses outweigh the wick savings.
- **Entry reversal-candle filter** (only enter if touch bar closes in reversal direction) — tested, increased DD. Over-filters fast-reversal entries that were the strategy's best wins.
- **Adaptive range gate** (skip days where prev_day range < N-day SMA × multiplier) — tested across 5/7/14-day lookbacks and 0.8/1.0/1.2 multipliers. None improved Calmar — gate either filtered profitable trades or didn't filter the trending-day losers.
- **Multi-cycle per day** with TP-only re-entry — tested 2-3 cycles/day, both with and without "lock day after SL". Worse on every metric vs 1 cycle/day; same-day re-entries whipsaw.
- **Entry offset toward mark** (mirror of TP offset, fills before exact S/R touch) — tested at 0.05% / 0.1% / 0.15%. No improvement. prev_H and prev_L are extreme S/R levels where price actually does touch (the existing 0.05% zone catches them); the fill reliability gain is marginal and outweighed by the slightly worse entry price.
- **No SL** (ride to TP or 20:00 UTC EOD only) — tested. Total return basically unchanged (+35.30% vs +35.25%, same trade count and WR), but Max DD jumped from 4.80% → 5.94% and Calmar dropped from 7.34 → 5.94. SL fires rarely but caps the bad-day losses meaningfully. Removing it adds risk without adding return.

---

## What's Plotted

- 🟢 prev_day Low (green dotted)
- 🔴 prev_day High (red dotted)
- 🔵 prev_day Mid / TP (cyan dotted)
- 🔴 Active SL (red solid, 2px when in position)
- 🟣 1h EMA20 (purple, reference only)
- ▲/▼ Entry triangle markers when L1/S1 conditions fire
- 📋 Top-right dashboard: position, prev_day levels, UTC hour, TP mode, filled count, cycle status, equity

---

## Disclaimer

Backtest results don't predict live performance. Slippage, funding fees, exchange downtime, and live order routing all impact real-world returns. The TradingView backtester also assumes intra-bar fills which may not happen in live trading without resting orders.

This script is shared for educational and research purposes. Use it as one input in your own due diligence — **never as a turn-key money-printer**. Past performance is not indicative of future results.

---

## TradingView Publishing

**Type**: Strategy
**Primary category**: Support and Resistance
**Secondary** (if available): Cycles

**Tags**: `bitcoin` `btc` `btcusdt` `5min` `5m` `day trading` `intraday` `mean reversion` `support resistance` `s/r` `pivot` `dca` `dollar cost averaging` `take profit` `stop loss` `eod` `binance futures` `perpetual`
