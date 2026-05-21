#!/bin/bash
# ════════════════════════════════════════════════════════════════════
#  vm_ip.sh — show the external IP of your Bybit bot's GCP VM.
#
#  Use the printed IP for the Bybit API-key IP whitelist. If the IP is
#  ephemeral (the free-tier default), it can change when the VM stops/
#  restarts — this script offers to RESERVE it as a static IP so it
#  never changes again and your Bybit whitelist never breaks.
#
#  Run:  bash bybit/scripts/vm_ip.sh
# ════════════════════════════════════════════════════════════════════
set -euo pipefail

command -v gcloud >/dev/null 2>&1 || {
  echo "ERROR: gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install"
  exit 1
}

# ─── Pick the Google account ───
echo "── Google account ──"
mapfile -t ACCOUNTS < <(gcloud auth list --format="value(account)" 2>/dev/null || true)
[ "${#ACCOUNTS[@]}" -eq 0 ] && { echo "No authenticated accounts — run 'gcloud auth login'."; exit 1; }
CURRENT_ACCT="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null || true)"
[ -n "$CURRENT_ACCT" ] && echo "(currently active: $CURRENT_ACCT)"
PS3="Select the account (number): "
select ACCOUNT in "${ACCOUNTS[@]}"; do [ -n "${ACCOUNT:-}" ] && break; done
gcloud config set account "$ACCOUNT" >/dev/null

# ─── Pick the project ───
echo ""
echo "── GCP project ──"
mapfile -t PROJECTS < <(gcloud projects list --format="value(projectId)" 2>/dev/null || true)
[ "${#PROJECTS[@]}" -eq 0 ] && { echo "No projects visible for $ACCOUNT."; exit 1; }
PS3="Select the project (number): "
select PROJECT in "${PROJECTS[@]}"; do [ -n "${PROJECT:-}" ] && break; done
gcloud config set project "$PROJECT" >/dev/null

# ─── Pick the VM ───
echo ""
echo "── VM ──"
mapfile -t VMS < <(gcloud compute instances list --format="value(name,zone)" 2>/dev/null || true)
[ "${#VMS[@]}" -eq 0 ] && { echo "No VMs in project $PROJECT."; exit 1; }
if [ "${#VMS[@]}" -eq 1 ]; then
  CHOICE="${VMS[0]}"
  echo "Using the only VM: $CHOICE"
else
  PS3="Select the VM (number): "
  select CHOICE in "${VMS[@]}"; do [ -n "${CHOICE:-}" ] && break; done
fi
VM_NAME="$(echo "$CHOICE" | awk '{print $1}')"
ZONE="$(echo "$CHOICE" | awk '{print $2}')"
REGION="${ZONE%-*}"

# ─── Read the external IP ───
IP="$(gcloud compute instances describe "$VM_NAME" --zone="$ZONE" \
        --format='get(networkInterfaces[0].accessConfigs[0].natIP)' 2>/dev/null || true)"
[ -z "$IP" ] && { echo "ERROR: $VM_NAME has no external IP."; exit 1; }

echo ""
echo "════════════════════════════════════════════════════════"
echo "  External IP of $VM_NAME ($ZONE):"
echo ""
echo "        $IP"
echo ""
echo "  → paste this into your Bybit API-key IP whitelist"
echo "════════════════════════════════════════════════════════"

# ─── Ephemeral or static? ───
RESERVED_NAME="$(gcloud compute addresses list --filter="address=$IP" \
                   --format='value(name)' 2>/dev/null || true)"
echo ""
if [ -n "$RESERVED_NAME" ]; then
  echo "This IP is STATIC (reserved as '$RESERVED_NAME') — it will not change. ✓"
else
  echo "This IP is EPHEMERAL — it can change if the VM is stopped/restarted,"
  echo "which would break the Bybit whitelist until you re-paste the new IP."
  read -rp "Reserve it as a STATIC IP now, so it never changes? [y/N]: " ans
  if [[ "${ans:-}" =~ ^[Yy]$ ]]; then
    gcloud compute addresses create "${VM_NAME}-ip" \
      --addresses="$IP" --region="$REGION"
    echo "✓ Reserved $IP as static ('${VM_NAME}-ip'). It is now permanent —"
    echo "  whitelist it on Bybit once and you never need to update it again."
  else
    echo "Left as ephemeral. Re-run this script to get the IP again if it changes."
  fi
fi
