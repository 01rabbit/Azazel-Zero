#!/usr/bin/env bash
# Azazel-Gadget: Captive Portal detector
# Usage:
#   portal_detect.sh [--iface <iface>]
# Behavior:
#   - Prefer interface from CLI/env, else read connection.captive_probe_iface from state.json.
#   - Bind probe traffic with curl --interface.
#   - Detect 30x redirect as captive portal.

set -euo pipefail

RUN_DIR="/run/azazel"
LOCK_FILE="$RUN_DIR/portal.lock"
LOG_TAG="azazel-portal"
STATE_PRIMARY="/run/azazel-zero/ui_snapshot.json"
USER_HOME="${HOME:-/home/azazel}"
STATE_FALLBACK="${USER_HOME}/.azazel-zero/run/ui_snapshot.json"
STATE_FALLBACK_AZAZEL="/home/azazel/.azazel-zero/run/ui_snapshot.json"

log_info()  { logger -t "$LOG_TAG" "INFO: $*"  || echo "[INFO] $*"  >&2; }
log_warn()  { logger -t "$LOG_TAG" "WARN: $*"  || echo "[WARN] $*"  >&2; }
log_error() { logger -t "$LOG_TAG" "ERROR: $*" || echo "[ERROR] $*" >&2; }

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
    /opt/azazel/bin/azazel_console.sh "WARN ${msg}" || true
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

resolve_state_iface() {
  python3 - "$STATE_PRIMARY" "$STATE_FALLBACK" "$STATE_FALLBACK_AZAZEL" <<'PY'
import json
import sys
from pathlib import Path

for candidate in sys.argv[1:]:
    path = Path(candidate)
    if not path.exists():
        continue
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    connection = data.get("connection") or {}
    iface = str(connection.get("captive_probe_iface") or "").strip()
    if iface:
        print(iface)
        raise SystemExit(0)
    iface = str(data.get("captive_probe_iface") or "").strip()
    if iface:
        print(iface)
        raise SystemExit(0)
print("")
PY
}

extract_host() {
  local value="${1:-}"
  case "$value" in
    *://*) ;;
    *) printf ''; return 0 ;;
  esac
  printf '%s' "$value" | sed -E 's#^[a-zA-Z]+://([^/:]+).*#\1#'
}

OUTIF="${OUTIF:-}"
while [ $# -gt 0 ]; do
  case "$1" in
    --iface)
      OUTIF="${2:-}"
      shift 2
      ;;
    *)
      OUTIF="${1:-}"
      shift
      ;;
  esac
done

if [ -z "$OUTIF" ]; then
  OUTIF="$(resolve_state_iface)"
fi

mkdir -p "$RUN_DIR"

if [ -z "$OUTIF" ]; then
  log_warn "skip captive probe: reason=NOT_FOUND (no captive_probe_iface)"
  exit 0
fi
if ! [[ "$OUTIF" =~ ^[a-zA-Z0-9_.:-]+$ ]]; then
  log_warn "skip captive probe: reason=INVALID_IFACE iface=$OUTIF"
  exit 0
fi
if ! ip link show "$OUTIF" >/dev/null 2>&1; then
  log_warn "skip captive probe: reason=NOT_FOUND iface=$OUTIF"
  exit 0
fi
if ! ip -o link show "$OUTIF" | grep -q 'state UP'; then
  log_warn "skip captive probe: reason=LINK_DOWN iface=$OUTIF"
  exit 0
fi
if ! ip -4 -o addr show dev "$OUTIF" | grep -q 'inet '; then
  log_warn "skip captive probe: reason=NO_IP iface=$OUTIF"
  exit 0
fi

ENDPOINTS=(
  "http://connectivitycheck.gstatic.com/generate_204"
  "http://captive.apple.com/hotspot-detect.html"
)

status="NA"
reason="NOT_CHECKED"
location=""

for url in "${ENDPOINTS[@]}"; do
  body_file="$(mktemp "${RUN_DIR}/portal_body.XXXXXX")"
  hdr_file="$(mktemp "${RUN_DIR}/portal_hdr.XXXXXX")"

  if out=$(curl --interface "$OUTIF" -sS --max-time 7 -o "$body_file" -D "$hdr_file" -w "%{http_code} %{url_effective}" "$url" 2>&1); then
    code="${out%% *}"
    effective_url=""
    if [ "$out" != "$code" ]; then
      effective_url="${out#* }"
    fi
    requested_host="$(extract_host "$url")"
    location="$(awk -F': ' 'BEGIN{IGNORECASE=1}$1=="Location"{print $2;exit}' "$hdr_file" | tr -d '\r')"
    redirect_host="$(extract_host "${location:-$effective_url}")"
    body_len="$(wc -c < "$body_file" 2>/dev/null || echo 0)"
    if [ "$code" = "204" ]; then
      status="NO"
      reason="HTTP_204"
      rm -f "$body_file" "$hdr_file"
      break
    elif [[ "$code" =~ ^30[1278]$ ]]; then
      if [ -n "$redirect_host" ] && [ "$redirect_host" != "$requested_host" ]; then
        status="YES"
        reason="HTTP_30X"
        rm -f "$body_file" "$hdr_file"
        break
      fi
      status="NA"
      reason="HTTP_30X_SAME_ORIGIN"
    elif [ "$code" = "200" ] && [ "${body_len:-0}" -gt 0 ]; then
      if [ "$requested_host" = "captive.apple.com" ] && grep -qi 'success' "$body_file"; then
        status="NO"
        reason="HTTP_200_APPLE_SUCCESS"
        rm -f "$body_file" "$hdr_file"
        break
      fi
      status="SUSPECTED"
      reason="HTTP_200_BODY"
    else
      status="NA"
      reason="HTTP_${code:-000}"
    fi
  else
    rc=$?
    status="NA"
    reason="CURL_ERR_${rc}"
  fi
  rm -f "$body_file" "$hdr_file"
done

if [ "$status" = "YES" ] || [ "$status" = "SUSPECTED" ]; then
  now=$(date +%s)
  last=$(cat "$LOCK_FILE" 2>/dev/null || echo 0)
  if [ $((now - last)) -lt 300 ]; then
    log_info "Portal detected but notification suppressed (debounce): status=$status reason=$reason iface=$OUTIF"
    exit 0
  fi
  echo "$now" > "$LOCK_FILE"

  msg_main="PORTAL REQUIRED"
  msg_sub="Open http://neverssl.com to authenticate"
  if [ "$status" = "SUSPECTED" ]; then
    msg_main="PORTAL SUSPECTED"
  fi
  notify_epd "$msg_main" "$msg_sub"
  notify_console "$msg_main: $msg_sub (IF=$OUTIF)"
  notify_mm "$msg_main: $msg_sub (IF=$OUTIF, reason=$reason)"
  log_info "Portal status=$status reason=$reason iface=$OUTIF location=${location:-}"
else
  log_info "No captive portal detected (status=$status reason=$reason iface=$OUTIF)"
fi

exit 0
