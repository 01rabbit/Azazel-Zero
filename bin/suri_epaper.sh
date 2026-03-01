#!/usr/bin/env bash
set -euo pipefail

for defaults in /etc/default/azazel-gadget /etc/default/azazel-zero; do
  if [[ -r "$defaults" ]]; then
    # shellcheck disable=SC1090
    . "$defaults"
    break
  fi
done

if [[ -z "${AZAZEL_ROOT:-}" ]]; then
  for candidate in \
    "$HOME/Azazel-Gadget" \
    "$HOME/Azazel-Zero" \
    "$HOME/azazel-gadget" \
    "$HOME/azazel-zero" \
    "/home/azazel/Azazel-Gadget" \
    "/home/azazel/Azazel-Zero"; do
    if [[ -d "$candidate" ]]; then
      AZAZEL_ROOT="$candidate"
      break
    fi
  done
fi

AZAZEL_ROOT="${AZAZEL_ROOT:-/home/azazel/Azazel-Gadget}"

EVE="/var/log/suricata/eve.json"
RUNTIME_DIR="/run/azazel"
STATE_FILE="${RUNTIME_DIR}/suri_epd_state.json"
COOLDOWN_SEC="${SURI_EPD_COOLDOWN_SEC:-90}"
MIN_GAP_SEC="${SURI_EPD_MIN_GAP_SEC:-10}"
ALERT_TTL_SEC="${SURI_EPD_ALERT_TTL_SEC:-120}"
command -v jq >/dev/null || { echo "jq required"; exit 1; }
mkdir -p "$RUNTIME_DIR"

normalize_num() {
  local val="${1:-0}"
  if [[ "$val" =~ ^[0-9]+$ ]]; then
    printf '%s' "$val"
  else
    printf '0'
  fi
}

should_publish() {
  local state="$1"
  local msg="$2"
  local now
  now="$(date +%s)"
  local last_ts=0
  local last_state=""
  local last_msg=""

  if [[ -f "$STATE_FILE" ]]; then
    read -r last_ts last_state last_msg < <(
      jq -r '[.ts // 0, .state // "", .msg // ""] | @tsv' "$STATE_FILE" 2>/dev/null || \
      printf "0\t\t\n"
    )
  fi

  last_ts="$(normalize_num "$last_ts")"
  local delta=$(( now - last_ts ))
  if (( delta < MIN_GAP_SEC )); then
    return 1
  fi
  if [[ "$state" == "$last_state" && "$msg" == "$last_msg" && $delta -lt $COOLDOWN_SEC ]]; then
    return 1
  fi
  return 0
}

mark_published() {
  local state="$1"
  local msg="$2"
  local severity="$3"
  local signature="$4"
  local ts
  ts="$(date +%s)"
  local expires_at=$(( ts + ALERT_TTL_SEC ))
  local tmp="${STATE_FILE}.tmp"
  jq -n \
    --argjson ts "$ts" \
    --argjson expires_at "$expires_at" \
    --arg state "$state" \
    --arg msg "$msg" \
    --arg severity "$severity" \
    --arg signature "$signature" \
    '{
      ts: $ts,
      expires_at: $expires_at,
      state: $state,
      msg: $msg,
      severity: $severity,
      signature: $signature
    }' > "$tmp" || return 0
  mv -f "$tmp" "$STATE_FILE" || true
}

trigger_refresh() {
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl start --no-block azazel-epd-refresh.service >/dev/null 2>&1; then
      return 0
    fi
  fi

  if [[ -x /usr/local/bin/azazel-epd-refresh ]]; then
    /usr/local/bin/azazel-epd-refresh >/dev/null 2>&1 || true
    return 0
  fi

  local refresh_py="${AZAZEL_ROOT}/py/azazel_control/epd_mode_refresh.py"
  if [[ -f "$refresh_py" ]]; then
    /usr/bin/python3 "$refresh_py" >/dev/null 2>&1 || true
    return 0
  fi
  return 1
}

publish_alert() {
  local severity="$1"
  local signature="$2"
  local state="warning"
  local msg="SCAN DETECTED"

  if [[ "$severity" =~ ^[0-9]+$ ]] && (( severity <= 2 )); then
    state="danger"
    msg="ATTACK DETECTED"
  fi

  logger -t suri-epaper "suricata alert: severity=${severity} signature=${signature:0:96}" >/dev/null 2>&1 || true

  if ! should_publish "$state" "$msg"; then
    return 0
  fi

  mark_published "$state" "$msg" "$severity" "$signature"
  if ! trigger_refresh; then
    logger -t suri-epaper "refresh trigger unavailable after state publish" >/dev/null 2>&1 || true
  fi
}

tail -Fn0 "$EVE" | jq -rc 'select(.event_type=="alert") | [(.alert.severity // 3), (.alert.signature // "IDS alert")] | @tsv' | \
while IFS=$'\t' read -r severity signature; do
  signature="${signature//$'\r'/ }"
  signature="${signature//$'\n'/ }"
  publish_alert "$severity" "$signature"
done
