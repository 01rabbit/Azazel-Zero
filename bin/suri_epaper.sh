#!/usr/bin/env bash
set -euo pipefail
AZAZEL_ROOT="${AZAZEL_ROOT:-/home/azazel/Azazel-Zero}"
EVE="/var/log/suricata/eve.json"
command -v jq >/dev/null || { echo "jq required"; exit 1; }

tail -Fn0 "$EVE" | jq -r 'select(.event_type=="alert") | .alert.signature' | \
while read -r line; do
  /usr/bin/python3 "${AZAZEL_ROOT}/py/boot_splash_epd.py" "IDS: $line" >/dev/null 2>&1 || true
done