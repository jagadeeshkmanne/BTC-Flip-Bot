BTC Flip Bot — MTF Engulfing Momentum (BTCUSDT 1H 2×)

A multi-timeframe trend-following strategy that captures high-conviction momentum reversals on Bitcoin.

⚠️ IMPORTANT: This strategy is tested and validated ONLY on BTCUSDT perpetual futures | 1H timeframe | 2× leverage. Do not apply to other pairs, timeframes, or leverage settings without independent testing.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HOW IT WORKS

Three timeframes must align simultaneously on a single 1H bar before a trade is taken:

🔹 DAILY — Trend Regime
Close must be above the 50-period EMA for longs (below for shorts). This ensures you only trade in the direction of the macro trend. Acts as the primary safety gate.

🔹 4H — Momentum Confirmation
RSI(14) must be above 50 for longs (below 50 for shorts). Confirms medium-term momentum aligns with the daily trend.

🔹 1H — Entry Trigger (all 5 must be true on the same bar)
• MACD(12,26,9) line above signal line
• Bullish or Bearish engulfing candle — the core trigger
• ATR(14) above its 50-period average — volatility expanding
• Volume above 1.5× its 20-period SMA — participation spike
• RSI(14) above 45 for longs / below 55 for shorts

Only ~0.1% of all 1H bars pass all 7 filters. This extreme selectivity is what produces the high profit factor.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STOP LOSS & EXITS

Stop Loss: Pattern-based — placed below the min of entry bar low and prior bar low, minus 0.1% buffer. Hard cap at 2.5% from entry. Whichever is tighter is used.

Partial Take Profit: At +5R favorable move, 30% of position is locked in. Remaining 70% continues running on the original stop loss.

Exit: Opposite-direction signal closes the trade. No flip-open — exit only. No fixed take-profit — winners run until stopped or reversed.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RISK MANAGEMENT

• Position sizing: 1% of equity risked per trade
• Leverage: 2× (tested at this level only)
• Same-direction cooldown: 36 hours after a stop-loss hit
• Post-exit cooldown: 2 hours after any exit
• Drawdown circuit breaker: -25% from equity peak halts trading for 7 days

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BACKTESTED RESULTS (BTCUSDT | 1H | 2× LEVERAGE)

📊 Period: April 2021 — April 2026 (5 years)

Start Capital: $10,000
Final Equity: $241,102 (+2,311%)
CAGR: +89.1%
Max Drawdown: -19.5%
Calmar Ratio: 4.57
Profit Factor: 5.50
Win Rate: 34.9% (15 wins / 28 losses)
Total Trades: 43 (~9 per year)

📅 Year-by-year performance:
• 2021: +35.7% (5 trades)
• 2022: +34.9% (12 trades)
• 2023: +71.7% (5 trades)
• 2024: +170.6% (11 trades)
• 2025: +97.3% (8 trades)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHY IT WORKS

The engulfing candle is the core edge. It captures the exact moment when one side (buyers or sellers) overwhelms the other with a decisive reversal bar. But engulfing alone is not enough — the 7-layer filter stack ensures each signal has:

✅ Macro trend support (Daily EMA50)
✅ Medium-term momentum (4H RSI)
✅ Short-term momentum (1H MACD)
✅ Volatility expansion (ATR above average)
✅ Institutional participation (volume spike)

~65% of trades will be stopped out. This is by design, not a flaw. The strategy is built around fat-tail capture — small frequent losses funded by rare but massive winners. Average winning trade moves +55% in your favor vs average losing trade at just -2%.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

WHAT TO EXPECT

This is NOT a high-frequency strategy. Expect:
• ~9 trades per year (roughly one every 5-6 weeks)
• Median wait between signals: 8 days
• Longest historical gap with no signal: 67 days
• Extended quiet periods where the dashboard shows 5/7 or 6/7 conditions met but no trade fires — this is the strategy working correctly, waiting for ALL conditions to align

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SETTINGS (all configurable via inputs)

Default values are the backtested optimals:
• Leverage: 2× | SL max: 2.5% | SL buffer: 0.1%
• Partial TP: 30% at +5R
• Vol spike ratio: 1.5× | ATR MA: 50 | Vol SMA: 20
• Same-dir cooldown: 36h | Generic cooldown: 2h
• DD halt threshold: -25% for 168h

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DISCLAIMER

• Past performance does not guarantee future results
• Backtested on historical data — live performance may differ
• TradingView free accounts have limited bar history — visible results may show fewer trades than the full 5-year backtest
• This strategy is designed for experienced traders who understand leverage, futures trading, and drawdown risk
• Always use risk capital you can afford to lose
• This is not financial advice
