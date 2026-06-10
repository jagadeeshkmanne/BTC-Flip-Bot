#!/bin/bash
# ─── Create GCP Free Tier VM for BTC MACD Bot ───
# Run this from your Mac after installing gcloud CLI
#
# Prerequisites:
#   1. Install gcloud: https://cloud.google.com/sdk/docs/install
#   2. Run: gcloud auth login
#   3. Create a project: gcloud projects create btc-bot-YOURNAME --name="BTC Bot"
#   4. Set project: gcloud config set project btc-bot-YOURNAME
#   5. Enable billing (required even for free tier): https://console.cloud.google.com/billing
#   6. Enable Compute Engine API: gcloud services enable compute.googleapis.com

set -e

PROJECT=$(gcloud config get-value project)
echo "Using GCP project: $PROJECT"

# Free tier: e2-micro in us-central1, us-east1, or us-west1
ZONE="us-central1-a"
VM_NAME="btc-bot"

echo "Creating e2-micro VM (free tier)..."
gcloud compute instances create $VM_NAME \
  --zone=$ZONE \
  --machine-type=e2-micro \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --boot-disk-type=pd-standard \
  --tags=bot-server \
  --metadata=startup-script='#!/bin/bash
    apt-get update -y
    apt-get install -y python3 python3-pip git
  '

echo ""
echo "VM created! Now run:"
echo "  1. Upload bot code:"
echo "     gcloud compute scp --recurse ~/Desktop/BTC-Flip-Bot $VM_NAME:~ --zone=$ZONE"
echo ""
echo "  2. SSH into VM:"
echo "     gcloud compute ssh $VM_NAME --zone=$ZONE"
echo ""
echo "  3. On the VM, run the install script:"
echo "     cd ~/BTC-Flip-Bot && bash scripts/gcp_install.sh"
