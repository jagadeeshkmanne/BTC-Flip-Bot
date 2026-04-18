Multi-timeframe trend-following strategy with SL-flip + pyramiding for BTC perpetual futures.

⚠️ Use ONLY on BTCUSDT | 1H chart | 2× leverage.
⚠️ TradingView shows limited data — see Python backtest below for full numbers.

HOW IT WORKS
3 timeframes must align on one 1H bar:
• Daily: close > EMA50 (trend)
• 4H: RSI > 50 (momentum)
• 1H: RSI + MACD + Engulfing + ATR + Volume (all 5 must confirm)

Only ~1% of engulfing candles pass all 7 filters. This selectivity is the edge.

STOP LOSS & EXITS
• SL: pattern-based, capped at 2.5%
• Partial TP: 15% closed at +6R, SL moves to BE+0.1%
• Opposite signal: exit only (no flip-open)
• DD halt: -25% pauses trading for 7 days

SL-FLIP (on SL hit → flip to opposite)
• Wait 1h → open opposite with tight 1.5% SL
• 24h time-stop on flips, no flip-on-flip

PYRAMIDING (V2)
• At +3R favorable: add 50% more position
• SL moves to entry +0.5R (locks profit)
• Max 1 add, no pyramiding on flips
• Requires 4× exchange leverage ceiling

PYTHON BACKTEST (6.5 years, Binance futures)
V2: $5K → $420K | +143% CAGR | -20% DD | PF 5.54 | 73 trades
V1: $5K → $182K | +105% CAGR | -20% DD | PF 4.63 | 74 trades

Year-by-year: 2021 +61% | 2022 +64% | 2023 +83% | 2024 +332% | 2025 +141% | 2026 +67%

WHAT TO EXPECT
• ~15 trades/year (not high-frequency)
• ~55% stopped out (by design)
• Few mega-winners drive CAGR
• Patience required — quiet periods are normal

⚠️ DISCLAIMER
Past performance ≠ future results. Backtests can overfit. Small sample (73 trades). Fat-tail dependent. Not financial advice. Use risk capital only.
