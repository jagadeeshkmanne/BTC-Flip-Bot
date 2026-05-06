#!/bin/bash
# Paper trading mode: mainnet PUBLIC kline data + virtual fills + virtual balance.
# No API keys needed (only public endpoints used).
# State stored in data/paper/, logs in data/paper/bot_paper.log.
cd /home/jags/BTC-Flip-Bot
/usr/bin/python3 strategies/day/bot.py --paper 2>&1
