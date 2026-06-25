# BTC-Flip-Bot — Strategy Findings & Final Spec

_Last updated: 2026-06-19. Distilled from ~30 honest backtests in `scripts/`._

All backtests follow the same **honesty rules**: signals on closed bars, fills at the
**next bar open**, fee 0.055%/side + 0.05% slippage, stops/targets on **real intrabar
high/low** (stop-first on straddle), **in-sample/out-of-sample (60/40) split**, and metrics
reported as **CAGR + max drawdown** (NOT total return — totals are inflated by 2021).

---

## 🏆 FINAL STRATEGY (best from all backtests)

**4-coin equal-weight basket — EMA8/200 trend, long/short "reverse", 1×.**

| Spec | Value |
|---|---|
| Coins | BTC, ETH, BNB, SOL — equal weight (25% each, 4 concurrent positions) |
| Signal (per coin) | `EMA8 > EMA200` → **long**; `EMA8 < EMA200` → **short** (always in market) |
| Entry / Exit | enter on the cross; exit/reverse on the **opposite** cross (no early exit) |
| Leverage | **1× (none)** |
| Stop loss / Take profit / Trailing | **NONE** — all proven to reduce returns |
| Timeframe | 4h |
| Script | `scripts/backtest_final.py`, `scripts/backtest_multipair.py` |
| **Live bot** | `bot/bot_allweather_4h.py` (PAPER, deployed 2026-06-19) — runner `scripts/run_allweather.sh`, data `data/allweather/`, dashboard id `allweather` |

**Performance (2020-08 → 2026, corrected linear-perp P&L):**
- **CAGR 99%** — but **realistic forward ≈ 42%/yr** (the 99% is inflated by 2021's once-in-history run)
- **Max drawdown −45%**
- **ret/DD 2.21** (best risk-adjusted of everything tested)
- **54% positive months**, **green every calendar year incl. 2022 (+1%, marginal)** — in-sample only

### Simpler / safer alternative (already live)
**BTC only — EMA50/200 (or live bot's EMA13>EMA20 & close>EMA200) long/flat, 1×.**
CAGR ~52%, but DD −59% and **red in bear years** (2022 −52%, 2026 −8%). One asset, no shorts,
dead simple. This is what `bot/bot_trend_4h.py` already runs (with dynamic vol-scaled leverage).

---

## ⚠️ Honest caveats (must accept before risking money)

1. **−45% drawdown is brutal** — expect to watch ~half the account vanish before recovery.
2. **Leans on SOL** for the all-weather property (concentration + survivorship risk — SOL
   barely survived the FTX collapse; its short side rescued 2022).
3. **In-sample only** — NOT walk-forward validated. Treat ~40%/yr as a hope, not a promise.
4. **Lumpy** — NOT monthly income (41–54% positive months; a few big months/years carry it).
5. **1× only** — 3×/5× = liquidation / wipeout (proven). No leverage, no stops, no TP.

---

## Every dial tested — and why each "more" backfired

| Dial | Result | Verdict |
|---|---|---|
| Timeframe 4h/daily | best signal-to-noise | ✅ use 4h |
| Timeframe 1h / 15m / 5m | noise + fees → losses (5m scalp −62%) | ❌ |
| Trend-following | the only edge on BTC | ✅ |
| Mean-reversion / reversal (HA, RSI, Bollinger, shooting star, supply/demand) | all lost OOS | ❌ |
| EMA anchor = 200 | every winner anchors on 200 | ✅ |
| EMA anchor = 50/100 (faster) | whipsaw, much worse | ❌ |
| Diversification → 4 coins | lower DD, all-green — the one free win | ✅ |
| Diversification → 10 coins | dilutes SOL, loses all-green | ❌ |
| Long/short reverse (simple, slow) | adds bear-year profit, raises ret/DD | ✅ |
| Shorts via pullback / signal / dedicated tuning | squeezed, worse | ❌ |
| Stop loss | **increased** DD (locks recoverable dips) | ❌ |
| Trailing stop | killed returns (+6,711% → +130%) | ❌ |
| Take profit | killed returns (caps winners, 99% → 13%) | ❌ |
| Faster exit EMA | ret/DD 2.21 → 0.11 | ❌ |
| 3× / 5× leverage | −82% to −97% DD, liquidations, 5×<3× return | ❌ |
| DCA / cascade / grids (3 types) | all reduced or lost | ❌ |
| Single-coin param fine-tune | overfits (IS-best −38% OOS) | ❌ |

**Unifying law:** higher timeframe + trend-following + long-biased + few trades + 1× +
~4-coin diversification + "do-nothing" exits = best. Every addition (leverage, stops, TP,
trailing, lower TF, more coins, more indicators, single-coin tuning) measurably subtracted.

---

## Known bug (fixed)
`backtest_multibot_2x.py` and `backtest_leverage.py` originally used an **inverse-perp short
P&L convention** (`equity = bal × entry/price`) that **overstated short gains** on big
down-moves, inflating results to 145% (1×) / 245% (2×). Correct **linear-perp** P&L
(`equity = bal × (2 − price/entry)`) gives **CAGR 99%**. Use the corrected numbers.

---

## Next step before going live
**Walk-forward validation** of the 4-coin basket (rolling optimize→test across multiple
windows) — it has only passed a single 60/40 split. Until then, paper-trade only.
