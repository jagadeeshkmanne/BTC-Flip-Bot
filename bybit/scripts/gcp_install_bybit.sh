#!/bin/bash
# ─── Run this ON the GCP VM after the code is uploaded ───
# Installs deps + the dashboard server, runs a connectivity preflight, and
# ONLY starts the 1-minute trading timer if the preflight passes.
# Idempotent — safe to re-run after a redeploy or after fixing keys.
set -e

BOT_DIR="$HOME/BTC-Flip-Bot-Bybit"
cd "$BOT_DIR"

echo "=== Installing Python dependencies ==="
pip3 install -r requirements.txt 2>/dev/null \
  || pip3 install --break-system-packages -r requirements.txt
mkdir -p data

if [ ! -f .env ]; then
  echo ""
  echo "!!  .env not found — the bot cannot trade without it."
  echo "!!  Create it:  cp .env.example .env && nano .env"
fi

# ─── systemd units ───
echo ""
echo "=== Installing systemd units ==="

# Trading bot — oneshot, fired every 1 min by the timer
sudo tee /etc/systemd/system/bybit-divflip.service > /dev/null << EOF
[Unit]
Description=Bybit Divflip v1 LIVE bot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$USER
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/python3 $BOT_DIR/bot_divflip_bybit.py
StandardOutput=journal
StandardError=journal
EOF

sudo tee /etc/systemd/system/bybit-divflip.timer > /dev/null << EOF
[Unit]
Description=Run Bybit Divflip bot every 1 min

[Timer]
OnBootSec=60
OnUnitActiveSec=1min
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Dashboard server — always on
sudo tee /etc/systemd/system/bybit-divflip-server.service > /dev/null << EOF
[Unit]
Description=Bybit Divflip dashboard server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/python3 $BOT_DIR/server.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload

# ─── Dashboard server — start regardless (so you can watch status) ───
echo ""
echo "=== Starting dashboard server (port 8889) ==="
sudo systemctl enable bybit-divflip-server.service >/dev/null 2>&1 || true
sudo systemctl restart bybit-divflip-server.service

# ─── Connectivity preflight — gate the trading timer ───
echo ""
echo "=== Connectivity preflight (checking BEFORE the bot starts) ==="
if python3 bot_divflip_bybit.py --check; then
  echo ""
  echo "Preflight PASSED — starting the 1-minute trading timer."
  sudo systemctl enable --now bybit-divflip.timer
  TRADING_ON=1
else
  echo ""
  echo "!!  Preflight FAILED — trading bot NOT started (by design)."
  echo "!!  Fix the issue above — usually the API key's IP whitelist must be"
  echo "!!  set to THIS VM's external IP — then re-run this script:"
  echo "!!     cd ~/BTC-Flip-Bot-Bybit && bash scripts/gcp_install_bybit.sh"
  sudo systemctl disable bybit-divflip.timer >/dev/null 2>&1 || true
  TRADING_ON=0
fi

IP=$(curl -s ifconfig.me 2>/dev/null || echo "<VM-IP>")
echo ""
echo "================================================================"
if [ "$TRADING_ON" = "1" ]; then
  echo "  DONE — bot is LIVE (1-min timer) + dashboard running."
else
  echo "  Dashboard is running. Trading bot is NOT started (preflight failed)."
fi
echo ""
echo "  Dashboard:  http://$IP:8889/"
echo "  Bot logs:   tail -f $BOT_DIR/data/bot.log"
echo "  Timers:     sudo systemctl list-timers | grep bybit"
echo "  Stop bot:   sudo systemctl disable --now bybit-divflip.timer"
echo "================================================================"
