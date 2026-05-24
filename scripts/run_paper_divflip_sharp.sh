#!/bin/bash
# Divergence-Flip paper-trading bot.
# Mainnet PUBLIC kline data + virtual fills + virtual $5K balance.
# Independent from the V2.2 paper bot — own state/log in data/paper_divflip_sharp/.
# No API keys needed (only public Binance endpoints).
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot_divflip_sharp.py 2>&1
