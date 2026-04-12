#!/bin/bash
# ─── Open port 8888 for dashboard access ───
# Run from your Mac (not the VM)

gcloud compute firewall-rules create allow-bot-dashboard \
  --allow=tcp:8888 \
  --target-tags=bot-server \
  --description="Allow access to bot dashboard" \
  --source-ranges="0.0.0.0/0"

echo ""
echo "Firewall rule created. Dashboard accessible at:"
echo "  http://$(gcloud compute instances describe btc-bot --zone=us-central1-a --format='get(networkInterfaces[0].accessConfigs[0].natIP)'):8888"
