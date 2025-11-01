

#!/usr/bin/env bash
# Azazel-Zero: Captive Portal detector
# Usage: portal_detect.sh [OUTIF]
#   OUTIF: outbound interface (e.g., wlan1). If omitted, reads $OUTIF from env or defaults to wlan1.
# Behavior:
#   - Sends HTTP HEAD to a set of plain-HTTP endpoints.
#   - If 30x redirect is observed, assumes captive portal is present.
#   - Debounces notifications using a lock timestamp under /run/azazel.
#   - Notifies via E-Paper, console, and Mattermost webhook (if configured).

set -euo pipefail

# Resolve outbound interface: arg > env > default
OUTIF_ARG="${1:-}" 
OUTIF_ENV="${OUTIF:-}"
OUTIF="${OUTIF_ARG:-${OUTIF_ENV:-wlan1}}"

# Runtime paths
RUN_DIR="/run/azazel"
LOCK_FILE="$RUN_DIR/portal.lock"
LOG_TAG="azazel-portal"

# Notification helpers (best-effort)
notify_epd() {
  local line1="${1:-PORTAL REQUIRED}"
  local line2="${2:-Open http://neverssl.com}"
  if [ -x /opt/azazel/bin/epaper_notify.sh ]; then
    /opt/azazel/bin/epaper_notify.sh "$line1" "$line2" || true
  fi
}

notify_console() {
  local msg="${1:-PORTAL REQUIRED: Open http://neverssl.com}"
  if [ -x /opt/azazel/bin/azazel_console.sh ]; then
    /opt/azazel/bin/azazel_console.sh "⚠ ${msg}" || true
  fi
}

notify_mm() {
  local msg="$1"
  local hook_file="/opt/azazel/config/mm_webhook.url"
  if [ -f "$hook_file" ]; then
    curl -sS -H 'Content-Type: application/json' \
      -d "{\"text\":\"${msg}\"}" "$(cat "$hook_file")" >/dev/null || true
  fi
}

log_info()  { logger -t "$LOG_TAG" "INFO: $*"  || echo "[INFO] $*"  >&2; }
log_warn()  { logger -t "$LOG_TAG" "WARN: $*"  || echo "[WARN] $*"  >&2; }
log_error() { logger -t "$LOG_TAG" "ERROR: $*" || echo "[ERROR] $*" >&2; }

mkdir -p "$RUN_DIR"

# Quick sanity on interface: presence in routing table is a soft check; proceed even if absent to avoid hard fails
if ! ip link show "$OUTIF" >/dev/null 2>&1; then
  log_warn "Interface $OUTIF not found; continuing without --interface binding"
  CURL_IF_OPTS=()
else
  CURL_IF_OPTS=("--interface" "$OUTIF")
fi

# Candidate plain-HTTP endpoints (avoid HTTPS upgrade traps)
ENDPOINTS=(
  "http://neverssl.com/"
  "http://example.com/"
)

has_captive_portal=1   # 0 = detected, 1 = not detected

for url in "${ENDPOINTS[@]}"; do
  # Use HEAD to minimize data; follow redirects disabled to catch 30x; small timeout
  if out=$(curl -I -sS "${CURL_IF_OPTS[@]}" --max-time 7 "$url" 2>&1); then
    # Match typical 30x codes often used by captive portals
    if echo "$out" | grep -qiE '^HTTP/.* 30(1|2|3|7|8)'; then
      location=$(echo "$out" | awk -F': ' 'BEGIN{IGNORECASE=1}$1=="Location"{print $2;exit}')
      log_info "30x from $url; Location=${location:-<none>}"
      has_captive_portal=0
      break
    else
      log_info "No redirect from $url"
    fi
  else
    log_warn "curl failed for $url: $out"
  fi
done

if [ "$has_captive_portal" -eq 0 ]; then
  # Debounce: notify at most once per 300 seconds
  now=$(date +%s)
  last=$(cat "$LOCK_FILE" 2>/dev/null || echo 0)
  if [ $((now - last)) -lt 300 ]; then
    log_info "Portal detected but notification suppressed (debounce)"
    exit 0
  fi
  echo "$now" > "$LOCK_FILE"

  MSG_MAIN="PORTAL REQUIRED"
  MSG_SUB="Open http://neverssl.com to authenticate"
  notify_epd "$MSG_MAIN" "$MSG_SUB"
  notify_console "$MSG_MAIN: $MSG_SUB (IF=$OUTIF)"
  notify_mm "$MSG_MAIN: $MSG_SUB (IF=$OUTIF)"
  log_info "Portal notification dispatched (IF=$OUTIF)"
  exit 0
else
  log_info "No captive portal detected"
  exit 0
fi