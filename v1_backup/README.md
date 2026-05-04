# V1 Backup — pre-divergence S/R DCA Day Trader

Frozen 2026-05-04. Contains the live-bot configuration from before the V2
RSI-divergence rollout.

## Contents

- `strategies/day/bot.py` — live bot, no divergence
- `strategies/day/core.py` — strategy logic, no divergence
- `strategies/day/strategy_sr_dca_5m.pine` — TradingView script (matches live bot)
- `dashboard.html` — dashboard UI before V2
- `server.py` — JSON server for the dashboard

## V1 config

- DCA: 2 levels, 0.8% spacing, SL 1.9%
- Filters: volume ×1.2, RSI anti-extreme (skip <25 / >75)
- Breakeven SL: ON (trigger 1.0%, buffer 0.25%)
- TP: hybrid (prev_mid pre-DCA, +4% post-DCA)
- Range filter: extend mode, 2-day lookback, 2% floor
- 1 cycle per UTC day, EOD flatten at 20:00

## V1 backtest (BTCUSDT 5m)

- Mar 23–May 3 (5w): +43.18% / PF 4.42 / DD 4.61% / 47 trades / WR 66%

## Rollback procedure

1. SSH to bot VM: `gcloud compute ssh btc-bot-eu --zone=europe-west1-b`
2. Replace live files:
   ```
   cp v1_backup/strategies/day/bot.py  ~/BTC-Flip-Bot/strategies/day/bot.py
   cp v1_backup/strategies/day/core.py ~/BTC-Flip-Bot/strategies/day/core.py
   ```
3. Or upload from this Mac: `gcloud compute scp ...`
4. Next cron tick (max 5 min) picks up the changed files automatically.

The trade history (`data/{env}/state_day.json`) is kept across versions —
no data loss on rollback.
