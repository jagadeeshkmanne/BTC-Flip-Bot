#!/bin/bash
# Divergence-Flip v2 paper-trading bot (3rd bot).
# A/B variant of the divflip bot: 2-DCA 50/50 @ 0.5%, worst-anchored SL 1%.
# Mainnet PUBLIC kline data + virtual fills + virtual $5K balance.
# Independent from the other paper bots — own state/log in data/paper_divflip_v2/.
# No API keys needed (only public Binance endpoints).
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_divflip_v2.py 2>&1
