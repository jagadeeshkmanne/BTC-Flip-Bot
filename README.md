# BTC Flip Bot

Two trading strategies for BTC perpetual futures on Binance. Runs on GCP free tier.

## Strategies

### BB Confluence V2 (ACTIVE)
Mean reversion: buy at 4H+1H Bollinger Band confluence with RSI oversold + volume spike.

| Metric | Value |
|---|---|
| Backtest (2020-2026) | $5K → $24M |
| CAGR | +285% |
| Max DD | -15.7% |
| PF | 2.48 |
| Trades | 886 (~141/yr) |
| Win rate | 66.5% |

Entry: 1H BB lower + near 4H BB lower + RSI < 30 + vol > 1.3x SMA
TP: 4H BB mid | SL: 4H BB band ± ATR | Max hold: 24h

### Swing V8 (INACTIVE)
Trend-following: MTF engulfing + SL-flip + pyramiding.

| Metric | Value |
|---|---|
| Backtest (2021-2026) | $5K → $572K |
| CAGR | +158% |
| Max DD | -20.6% |
| PF | 7.29 |
| Trades | 62 (~12/yr) |

---

## Files

```
Root (live bot files — GCP runs these):
  bot_bb.py        — BB live bot (ACTIVE)
  bb_core.py       — BB strategy logic
  bot.py           — Swing live bot (inactive)
  core.py          — Swing strategy logic
  server.py        — HTTP server (dashboards + API)
  dashboard_bb.html — BB dashboard
  dashboard.html   — Swing dashboard
  settings.html    — Password-protected settings
  config/          — testnet.json, production.json
  data/            — state, status, logs per env
  scripts/         — start.sh, stop.sh, self_heal.sh

strategies/ (backtests + Pine scripts):
  bb/              — BB backtest + Pine V2 + dashboard
  swing/           — Swing backtest + Pine V3 + dashboard + publish docs
```

---

## Deploy (GCP)

### Push code
```bash
gcloud compute scp bot_bb.py bb_core.py bot.py core.py server.py \
  dashboard_bb.html dashboard.html settings.html \
  btc-bot-eu:~/BTC-Flip-Bot/ --zone=europe-west1-b
gcloud compute ssh btc-bot-eu --zone=europe-west1-b \
  --command='sudo systemctl restart btc-bot-server'
```

### Switch strategies
```bash
# Switch to BB:
sudo systemctl stop btc-bot-testnet.timer
sudo systemctl start btc-bot-bb.timer

# Switch to Swing:
sudo systemctl stop btc-bot-bb.timer
sudo systemctl start btc-bot-testnet.timer
```

### Check status
```bash
sudo systemctl list-timers --all | grep btc
tail -20 ~/BTC-Flip-Bot/data/testnet/bot.log
```

---

## Dashboards

- BB: `http://VM-IP:8888/dashboard_bb.html?env=testnet`
- Swing: `http://VM-IP:8888/dashboard.html?env=testnet`
- Settings: `http://VM-IP:8888/settings.html` (password-protected)

---

## License

Personal use. No warranty. Backtest results don't guarantee live performance.
