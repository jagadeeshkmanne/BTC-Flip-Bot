BTC Flip Bot V1 — MTF Engulfing + SL-Flip (BTCUSDT 1H 2×)

Author: Jagadeesh Manne
Version: V1 — first public release (April 2026)

A multi-timeframe trend-following strategy with SL-flip extension for BTC perpetual futures.

⚠️ IMPORTANT: This strategy is tested and validated ONLY on BTCUSDT perpetual futures | 1H timeframe | 2× leverage. Do not apply to other pairs, timeframes, or leverage settings without independent testing.

⚠️ TradingView's chart timeframe affects how this strategy calculates. A red banner appears if the chart is not set to 1H — set the top-left TF to "1h" explicitly for results to match the published backtest.

⚠️ TradingView free/basic accounts cache only a limited number of bars (5K–20K). The TV backtest you see will cover only the most recent window of the full 6.5-year test. See the "Full backtest" section below for the authoritative Python numbers.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOW IT WORKS

Three timeframes must align simultaneously on a single 1H bar before a trade is taken:

🔹 DAILY — Trend Regime
Close > 50-period EMA for longs (below for shorts). Macro trend safety gate — rejects ~50% of all signals that would trade against the dominant trend.

🔹 4H — Momentum Confirmation
RSI(14) > 50 for longs (below for shorts). Medium-term momentum alignment.

🔹 1H — Entry Trigger (all 5 must be true on the same bar)
• RSI(14) > 45 for longs / < 55 for shorts
• MACD(12,26,9) line above/below signal line
• Bullish or Bearish engulfing candle (body > prior body) — the core trigger
• ATR(14) above 50-period average (volatility expanding)
• Volume above 1.5× 20-period SMA (participation spike)

Only ~1% of all engulfing candles pass all 7 filters across a 5-year period. This extreme selectivity is the edge.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STOP LOSS & PARTIAL TP

Main SL: Pattern-based (min of entry bar low + prior bar low, with 0.1% buffer). Capped at 2.5% from entry. Whichever is tighter wins.

Partial TP (tuned April 2026):
• At +6R favorable move, 15% of position closes
• After partial TP, SL moves to entry + 0.1% (break-even + fee buffer)
• Remaining 85% continues running with BE stop — captures fat-tail winners while protecting locked-in profit

Why 15%@6R (not 30%@5R)? Grid-search on 5yr data showed this variant lifts CAGR +11 percentage points vs the previous 30%@5R partial, because a small partial lets the runner portion capture the full fat-tail move when a trend plays out.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SL-FLIP EXTENSION

When main SL hits, the strategy queues an opposite-direction flip trade:

• Waits 1 hour after SL hit (lets whipsaw settle)
• Opens opposite direction with TIGHT 1.5% SL (vs main 2.5%)
• SL placed at swing high/low from last 10 bars OR 1.5% cap from broken SL — whichever is tighter
• No flip-on-flip cascade (prevents revenge trading)
• 24-hour time-stop on flip positions
• Flips respect DD halt + generic post-exit cooldown

Flip trades add a meaningful contribution over the non-flip baseline while keeping max drawdown unchanged.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EXIT RULES

• Stop Loss hit → close (triggers flip if not already a flip)
• Partial TP at +6R → close 15%, SL moves to BE+0.1%, rest runs
• Opposite direction signal → close only (no flip-open on signals, only on SL)
• Flip time-stop at 24h → close
• Drawdown circuit breaker: -25% from peak halts all trading for 7 days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RISK MANAGEMENT

• 2× leverage (tested at this level only)
• Position sizing: notional = equity × leverage (deploys full leverage on each trade)
• 24h same-direction cooldown after SL hit
• 2h generic post-exit cooldown (any direction)
• -25% drawdown halt pauses trading for 7 days
• Flip trades use tighter SL (1.5%) + 24h time-stop

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FULL BACKTEST (Python, Binance futures historical data)

Period: September 2019 — April 2026 (~6.5 years)
Starting capital: $5,000
Leverage: 2×

Results (V1 with latest tuning: 15%@6R partial + BE+0.1% + SL-flip):
Start → Final: $5,000 → $181,943 (+3,539%)
CAGR: +105.3%
Max DD: -19.7%
Profit Factor: 4.63
Win Rate: 41.9% (31W / 43L)
Total Trades: 74 (~15/yr) — 38 Long, 36 Short, 9 of which were flips
SL hits: 40
DD halts triggered: 0

Tuning history (each change validated on 5yr data):
• Baseline V5 (no flip): +89% CAGR, PF 4.24, 43 trades
• V6 + SL-flip: +97% CAGR, PF 4.20, 79 trades (flips add more trade opportunities)
• V6 + BE-move after partial TP: PF 4.20 → 4.29 (kills runner-giveback)
• V6 + 15%@6R partial: CAGR 97% → 105%, PF 4.29 → 4.63 (current)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHY TRADINGVIEW RESULTS DIFFER FROM PUBLISHED NUMBERS

Two reasons:

1. Bar history limit — TradingView loads a finite number of bars for strategy calculation. Free and basic plans only cover ~6–14 months of 1H data. The chart date header shows the full visible range, but the strategy only computes on the loaded bars. Premium plans load more history but usually still less than 6.5 years.

2. Indicator warmup — the Daily EMA50 filter needs ~50 daily bars (~2 months) of warmup data before producing stable values. Python skips the first 100 bars explicitly; Pine Script does not.

For production-accurate numbers, use the Python backtest values above. For a feel of the strategy's pattern, the TV preview is fine as an indication.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT TO EXPECT

This is NOT a high-frequency strategy:
• ~15 trades per year including flip trades
• Median wait between main signals: 7–10 days
• Longest historical quiet gap: ~2 months
• ~55% of trades stopped out (by design — fat-tail capture)
• Average winner >> average loser (each win is ~5–8× an average loss)
• Requires patience — extended quiet periods are normal, not a malfunction
• Fat tails matter: a few mega-winners (10%+ single trades) drive most of the CAGR

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SETTINGS (all configurable with tooltips)

Risk Management:
• Leverage: 2× (tested only at this level)
• Risk per trade: 1% (used for dashboard display)
• DD halt: -25% for 168 hours (7 days)

Stop Loss:
• SL max: 2.5% cap
• SL buffer: 0.1%

Partial Take Profit:
• TP trigger: 6R (sweep-verified peak)
• TP close: 15% of position
• SL-to-BE buffer after partial: 0.1% (kills runner-giveback)

Entry Filters:
• RSI long/short zones: 45 / 55
• Engulf body multiplier: 1.0× (any-size engulfing)
• ATR MA length: 50
• Volume SMA length: 20
• Volume spike ratio: 1.5×

Cooldowns:
• Same-dir SL cooldown: 24h
• Generic post-exit cooldown: 2h

SL-Flip:
• Enabled by default
• Flip wait: 1h
• Flip SL cap: 1.5%
• Swing lookback: 10 bars
• Flip time-stop: 24h

Visuals:
• Clean view toggle (default ON — hides dashboard for publishing)
• Daily EMA50 line toggle
• Timeframe advisory banner (red warning if chart TF ≠ 1H)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DISCLAIMER

• Past performance does not guarantee future results. Backtests can overfit, especially after extensive parameter tuning.
• Live performance will differ from backtest due to real slippage, partial fills, exchange latency, and market regime shifts.
• TradingView free/basic accounts show a limited window; the authoritative full backtest is Python + Binance archives.
• This strategy is designed for experienced traders who understand leverage, futures trading, stop losses, drawdown risk, and position sizing.
• Small sample size (74 trades over 6.5 years) means regime changes could meaningfully degrade performance. A flat 3–6 month period is not a strategy failure.
• The strategy depends on fat-tail winners. Missing one or two large trends can halve expected CAGR.
• 2× leverage amplifies both gains AND drawdowns. Use capital you can afford to lose entirely.
• Not financial advice — use at your own risk.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CHANGELOG

V1 (April 2026 — first public release):
• Multi-timeframe entry: Daily EMA50 + 4H RSI + 1H (RSI + MACD + Engulfing + ATR + Volume)
• Pattern-based SL with 2.5% cap
• Partial TP: 15% at +6R (small partial for maximum fat-tail capture)
• SL-to-BE move after partial TP (kills runner-giveback)
• SL-flip extension: opposite-direction entry after SL hit with tight 1.5% SL
• 24h same-direction cooldown + 2h generic cooldown
• -25% DD halt for 7 days
• Clean-view toggle for minimal chart display
• Timeframe-mismatch advisory banner
