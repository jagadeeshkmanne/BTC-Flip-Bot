BTC Flip Bot V1 — MTF Engulfing + SL-Flip (BTCUSDT 1H 2×)

Author: Jagadeesh Manne
Version: V1 — first public release (April 2026)

A multi-timeframe trend-following strategy with SL-flip extension for BTC perpetual futures.

⚠️ IMPORTANT: This strategy is tested and validated ONLY on BTCUSDT perpetual futures | 1H timeframe | 2× leverage. Do not apply to other pairs, timeframes, or leverage settings without independent testing.

⚠️ TradingView free accounts show only ~14 months of 1H data. The TV backtest you see here is a LIMITED SLICE of the strategy's behavior. See the "Full backtest" section below for the 5-year Python results.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOW IT WORKS

Three timeframes must align simultaneously on a single 1H bar before a trade is taken:

🔹 DAILY — Trend Regime
Close > 50-period EMA for longs (below for shorts). Macro trend safety gate.

🔹 4H — Momentum Confirmation
RSI(14) > 50 for longs (below for shorts). Medium-term momentum alignment.

🔹 1H — Entry Trigger (all 5 must be true on the same bar)
• MACD(12,26,9) line above signal line
• Bullish or Bearish engulfing candle — the core trigger
• ATR(14) above 50-period average (volatility expanding)
• Volume above 1.5× 20-period SMA (participation spike)
• RSI(14) > 45 for longs / < 55 for shorts

Only ~0.1% of all 1H bars pass all 7 filters. This extreme selectivity is the edge.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STOP LOSS & PARTIAL TP

Main SL: Pattern-based (min of entry bar low + prior bar low, − 0.1% buffer). Capped at 2.5% from entry. Whichever is tighter wins.

Partial TP: At +5R favorable move, 30% of position closes. Remaining 70% continues on original SL (captures fat-tail winners).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SL-FLIP EXTENSION (NEW in V1)

When main SL hits, the strategy queues an opposite-direction flip trade:

• Waits 1 hour after SL hit (lets whipsaw settle)
• Opens opposite direction with TIGHT 1.5% SL (vs main 2.5%)
• SL placed at swing high/low from last 10 bars OR 1.5% cap from broken SL — whichever is tighter
• No flip-on-flip cascade (prevents revenge trading)
• 24-hour time-stop on flip positions

Flip trades historically win ~55% of the time and add ~+$108K over the 5-year baseline with same max drawdown.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

EXIT RULES

• Stop Loss hit → close
• Partial TP at +5R → close 30%, rest runs
• Opposite direction signal → close only (no flip-open on signals, only on SL)
• Flip time-stop at 24h → close
• Drawdown circuit breaker: -25% from peak halts all trading for 7 days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RISK MANAGEMENT

• 1% equity risked per trade (sized from SL distance)
• 2× leverage (tested at this level only)
• 24h same-direction cooldown after SL
• 2h generic post-exit cooldown
• -25% drawdown halt for 7 days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

FULL BACKTEST (Python, Binance historical data, 5 years)

Period: April 2021 — April 2026

V5 baseline (no SL-flip):
Start → Final: $10,000 → $241,102 (+2,311%)
CAGR: +89.1% | Max DD: -19.5% | PF: 4.24 | Win Rate: 34.9% | Trades: 43

V1 (this strategy, WITH SL-flip):
Start → Final: $10,000 → $349,000 (+3,388%)
CAGR: +103.6% | Max DD: -19.6% | PF: 4.80 | Win Rate: 44.0% | Trades: 75

Year-by-year (V1):
• 2021: +54.9% (9 trades, 56% WR)
• 2022: +45.2% (20 trades, 40% WR) — bear market year
• 2023: +87.5% (9 trades, 44% WR)
• 2024: +182.4% (20 trades, 50% WR)
• 2025: +105.2% (14 trades, 36% WR)
• 2026 YTD: +42.7% (3 trades, 33% WR)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHY TRADINGVIEW RESULTS DIFFER

TradingView free accounts only load ~10,000 bars on 1H (~14 months). This limited window:
• Misses the indicator warmup period (Daily EMA50 needs 50 daily bars ≈ 2 months)
• Excludes multiple bull/bear cycles that the strategy was validated on
• Shows a small sample that can skew stats

The full 5-year Python backtest uses the complete Binance futures data from 2021-2026. Results above are from that authoritative source.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT TO EXPECT

This is NOT a high-frequency strategy:
• ~15 trades per year including flip trades (~9 main + ~6 flips)
• Median wait between main signals: 8 days
• Longest historical gap: 67 days
• ~65% of main trades stopped out (by design — fat-tail capture)
• Average winner: +55% MFE | Average loser: -2% MAE
• Requires patience — extended quiet periods are normal

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SETTINGS (all configurable)

Every parameter is exposed as an input with tooltips explaining what it does:

• Leverage: 2× (tested only at this level)
• Risk per trade: 1%
• SL max: 2.5% | SL buffer: 0.1%
• Partial TP: 30% at +5R
• Engulf body multiplier: 1.0
• Vol spike ratio: 1.5× | ATR MA: 50 | Vol SMA: 20
• RSI long/short: 45/55
• Same-dir cooldown: 24h | Generic cooldown: 2h
• DD halt: -25% for 168h
• SL-Flip enabled | Flip wait: 1h | Flip SL cap: 1.5% | Flip time-stop: 24h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DISCLAIMER

• Past performance does not guarantee future results
• Backtested on historical data — live performance may differ
• TradingView free accounts show limited data; full backtest uses Python + Binance archives
• This strategy is designed for experienced traders who understand leverage, futures trading, and drawdown risk
• Small sample size (75 trades over 5 years) means regime changes in the future could degrade performance
• Always use risk capital you can afford to lose
• Not financial advice — use at your own risk
