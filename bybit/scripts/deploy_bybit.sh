#!/bin/bash
# ════════════════════════════════════════════════════════════════════
#  deploy_bybit.sh — deploy the Bybit divflip v1 LIVE bot to a GCP VM.
#
#  Interactive. Lets you pick:
#    1. which Google (gcloud) account to deploy with
#    2. which GCP project
#    3. which VM  (existing, or create a new free-tier e2-micro)
#  then uploads the bot and installs the 1-minute systemd timer.
#
#  Run from your Mac:  bash bybit/scripts/deploy_bybit.sh
#  Prereqs: gcloud CLI installed + at least one `gcloud auth login` done.
# ════════════════════════════════════════════════════════════════════
set -euo pipefail

# bybit/ folder = parent of this script's dir
BYBIT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="BTC-Flip-Bot-Bybit"

echo "════════════════════════════════════════════════════════"
echo "  Bybit Divflip v1 — LIVE deploy"
echo "  Source: $BYBIT_DIR"
echo "════════════════════════════════════════════════════════"

command -v gcloud >/dev/null 2>&1 || {
  echo "ERROR: gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install"
  exit 1
}

# ─── 1. Pick the Google account ───
echo ""
echo "── Step 1/4 · Google account ──"
mapfile -t ACCOUNTS < <(gcloud auth list --format="value(account)" 2>/dev/null || true)
if [ "${#ACCOUNTS[@]}" -eq 0 ]; then
  echo "No authenticated accounts. Running 'gcloud auth login'..."
  gcloud auth login
  mapfile -t ACCOUNTS < <(gcloud auth list --format="value(account)")
fi
echo "Authenticated Google accounts:"
CURRENT_ACCT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
[ -n "$CURRENT_ACCT" ] && echo "(currently active: $CURRENT_ACCT — pick it to keep, or choose another to switch)"
PS3="Select the account to deploy with (number): "
select ACCOUNT in "${ACCOUNTS[@]}" "+ Add another account (gcloud auth login)"; do
  if [ "$ACCOUNT" = "+ Add another account (gcloud auth login)" ]; then
    gcloud auth login
    mapfile -t ACCOUNTS < <(gcloud auth list --format="value(account)")
    select ACCOUNT in "${ACCOUNTS[@]}"; do [ -n "$ACCOUNT" ] && break; done
    break
  elif [ -n "${ACCOUNT:-}" ]; then
    break
  fi
done
gcloud config set account "$ACCOUNT"
echo "✓ Account: $ACCOUNT"

# ─── 2. Pick the project ───
echo ""
echo "── Step 2/4 · GCP project ──"
mapfile -t PROJECTS < <(gcloud projects list --format="value(projectId)" 2>/dev/null || true)
if [ "${#PROJECTS[@]}" -eq 0 ]; then
  echo "ERROR: no projects visible for $ACCOUNT. Create one in the GCP console first."
  exit 1
fi
CURRENT_PROJ="$(gcloud config get-value project 2>/dev/null || true)"
[ -n "$CURRENT_PROJ" ] && [ "$CURRENT_PROJ" != "(unset)" ] && echo "(currently active: $CURRENT_PROJ)"
PS3="Select the project (number): "
select PROJECT in "${PROJECTS[@]}"; do [ -n "${PROJECT:-}" ] && break; done
gcloud config set project "$PROJECT"
echo "✓ Project: $PROJECT"

# ─── 3. Pick or create the VM ───
echo ""
echo "── Step 3/4 · VM ──"
mapfile -t VMS < <(gcloud compute instances list --format="value(name,zone)" 2>/dev/null || true)
NEW_VM_LABEL="+ Create a new e2-micro VM"
PS3="Select a VM, or create one (number): "
select CHOICE in "${VMS[@]}" "$NEW_VM_LABEL"; do
  [ -n "${CHOICE:-}" ] && break
done

if [ "$CHOICE" = "$NEW_VM_LABEL" ]; then
  echo ""
  echo "  Bybit blocks US IPs (like Binance). GCP free tier is US-only and will"
  echo "  NOT work for Bybit. Use a Bybit-allowed region, e.g.:"
  echo "    europe-west1-b (Belgium)   europe-west3-c (Frankfurt)"
  echo "    asia-northeast1-a (Tokyo)  asia-south1-a (Mumbai)"
  echo "  Avoid: us-*, asia-southeast1 (Singapore), asia-east2 (Hong Kong),"
  echo "         europe-west2 (London), northamerica-* (Canada) — all blocked."
  read -rp "New VM name [bybit-bot]: " VM_NAME;  VM_NAME="${VM_NAME:-bybit-bot}"
  # Reject Bybit-restricted regions outright — the bot would just be blocked.
  while true; do
    read -rp "Zone [europe-west1-b]: " ZONE; ZONE="${ZONE:-europe-west1-b}"
    case "$ZONE" in
      us-*|northamerica-northeast*|asia-southeast1*|asia-east2*|europe-west2*)
        echo "  ✗ '$ZONE' is a Bybit-restricted region — the bot would be blocked there."
        echo "    Choose another: europe-west1-b, europe-west3-c, asia-northeast1-a, asia-south1-a" ;;
      *) break ;;
    esac
  done
  echo "Creating e2-micro VM '$VM_NAME' in $ZONE ..."
  gcloud compute instances create "$VM_NAME" \
    --zone="$ZONE" --machine-type=e2-micro \
    --image-family=ubuntu-2204-lts --image-project=ubuntu-os-cloud \
    --boot-disk-size=30GB --boot-disk-type=pd-standard \
    --metadata=startup-script='#!/bin/bash
      apt-get update -y && apt-get install -y python3 python3-pip git'
  echo "Waiting 45s for the VM to finish booting + installing python..."
  sleep 45
else
  VM_NAME="$(echo "$CHOICE" | awk '{print $1}')"
  ZONE="$(echo "$CHOICE" | awk '{print $2}')"
fi
echo "✓ VM: $VM_NAME ($ZONE)"

# ─── 4. Upload + install ───
echo ""
echo "── Step 4/4 · Upload + install ──"
SSH="gcloud compute ssh $VM_NAME --zone=$ZONE --command"
SCP="gcloud compute scp --zone=$ZONE"

# Open the dashboard port 8889 (idempotent).
echo "Ensuring firewall allows the dashboard port 8889..."
if gcloud compute firewall-rules describe bybit-divflip-dash >/dev/null 2>&1; then
  echo "  ✓ firewall rule 'bybit-divflip-dash' already exists"
else
  gcloud compute firewall-rules create bybit-divflip-dash \
    --allow=tcp:8889 --source-ranges=0.0.0.0/0 \
    --description="Bybit divflip bot dashboard" >/dev/null \
    && echo "  ✓ firewall rule 'bybit-divflip-dash' created (tcp:8889)"
fi

$SSH "mkdir -p ~/$REMOTE_DIR/config ~/$REMOTE_DIR/scripts ~/$REMOTE_DIR/data"

# Upload code (NOT data/ — preserves remote state on redeploy).
$SCP "$BYBIT_DIR"/bot_divflip_bybit.py "$BYBIT_DIR"/bybit_client.py \
     "$BYBIT_DIR"/core.py "$BYBIT_DIR"/core_divflip.py \
     "$BYBIT_DIR"/server.py "$BYBIT_DIR"/dashboard.html \
     "$BYBIT_DIR"/requirements.txt "$BYBIT_DIR"/README.md \
     "$BYBIT_DIR"/.env.example \
     "$VM_NAME":~/"$REMOTE_DIR"/
$SCP "$BYBIT_DIR"/config/bybit_live.json "$VM_NAME":~/"$REMOTE_DIR"/config/
$SCP "$BYBIT_DIR"/scripts/run_bybit.sh "$BYBIT_DIR"/scripts/gcp_install_bybit.sh \
     "$VM_NAME":~/"$REMOTE_DIR"/scripts/

if [ -f "$BYBIT_DIR/.env" ]; then
  echo "Uploading .env (API keys)..."
  $SCP "$BYBIT_DIR"/.env "$VM_NAME":~/"$REMOTE_DIR"/
else
  echo "NOTE: no local .env — create it on the VM before the bot can trade."
fi

echo ""
echo "Running the install script on the VM..."
$SSH "cd ~/$REMOTE_DIR && bash scripts/gcp_install_bybit.sh"

VM_IP="$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" \
          --format='get(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null || echo '<VM-IP>')"
echo ""
echo "════════════════════════════════════════════════════════"
echo "  ✓ Deployed to $VM_NAME ($ZONE) · project $PROJECT"
echo ""
echo "  Dashboard:  http://$VM_IP:8889/"
echo ""
if [ ! -f "$BYBIT_DIR/.env" ]; then
  echo "  NEXT — add your Bybit API keys on the VM, then re-run install:"
  echo "    gcloud compute ssh $VM_NAME --zone=$ZONE"
  echo "    cd ~/$REMOTE_DIR && cp .env.example .env && nano .env"
  echo "    bash scripts/gcp_install_bybit.sh"
  echo ""
fi
echo "  Logs:  gcloud compute ssh $VM_NAME --zone=$ZONE \\"
echo "           --command='tail -f ~/$REMOTE_DIR/data/bot.log'"
echo "════════════════════════════════════════════════════════"
