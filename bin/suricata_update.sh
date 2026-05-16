#!/bin/bash
# Azazel-Gadget: Suricata lightweight profile updater.
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1; then
    exec sudo -E bash "$0" "$@"
  fi
  echo "[suricata_update][ERROR] run as root (or install sudo)" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="pi-zero2-lite"
WAN_IF="${WAN_IF:-wlan0}"
DO_TEST=1
DO_RESTART=1
UPDATE_SOURCES=1
AF_PACKET_THREADS=1
AF_PACKET_BPF='tcp dst port 22 or tcp dst port 80'

RULE_OUT="/var/lib/suricata/rules/suricata.rules"
CUSTOM_RULES="/var/lib/suricata/rules/azazel-canary-lite.rules"

log() { printf "[suricata_update] %s\n" "$*"; }
warn() { printf "[suricata_update][WARN] %s\n" "$*" 1>&2; }
die() { printf "[suricata_update][ERROR] %s\n" "$*" 1>&2; exit 1; }

usage() {
  cat <<'USAGE'
Usage: suricata_update.sh [OPTIONS]

Options:
  --profile PROFILE          minimal | canary-lite | pi-zero2-lite (default: pi-zero2-lite)
  --wan-if IFACE             capture interface hint for /etc/default/suricata
  --no-test
  --no-restart
  --no-update-sources
  -h, --help
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) shift; PROFILE="${1:-$PROFILE}" ;;
    --profile=*) PROFILE="${1#*=}" ;;
    --wan-if) shift; WAN_IF="${1:-$WAN_IF}" ;;
    --wan-if=*) WAN_IF="${1#*=}" ;;
    --no-test) DO_TEST=0 ;;
    --no-restart) DO_RESTART=0 ;;
    --no-update-sources) UPDATE_SOURCES=0 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1" ;;
  esac
  shift
done

command -v suricata >/dev/null 2>&1 || die "suricata not found"
SURI_VER_MM=$(suricata -V 2>&1 | sed -n 's/.* \([0-9]\+\.[0-9]\+\)\..*/\1/p' | head -n1)
SURI_VER_MM=${SURI_VER_MM:-7.0}
BASE="https://rules.emergingthreats.net/open/suricata-${SURI_VER_MM}/rules"

declare -a RULE_URLS
case "$PROFILE" in
  minimal)
    RULE_URLS=(
      "${BASE}/emerging-dns.rules"
      "${BASE}/emerging-scan.rules"
    )
    ;;
  canary-lite)
    RULE_URLS=(
      "${BASE}/emerging-dns.rules"
      "${BASE}/emerging-scan.rules"
      "${BASE}/emerging-ssh.rules"
      "${BASE}/emerging-web_server.rules"
    )
    ;;
  pi-zero2-lite)
    RULE_URLS=()
    ;;
  *)
    die "unsupported profile: $PROFILE"
    ;;
esac

write_canary_rules() {
  cat > "$CUSTOM_RULES" <<'RULES'
# Local low-overhead rules for OpenCanary-facing services (22/80).
# Source is 'any' on purpose so same-LAN attackers on wlan0 are detected.
alert tcp any any -> $HOME_NET 22 (msg:"AZAZEL CANARY SSH SYN"; flags:S; flow:to_server; classtype:attempted-recon; sid:9901001; rev:2;)
alert tcp any any -> $HOME_NET 80 (msg:"AZAZEL CANARY HTTP SYN"; flags:S; flow:to_server; classtype:attempted-recon; sid:9901002; rev:2;)
alert tcp any any -> $HOME_NET [22,80] (msg:"AZAZEL CANARY repeated access to canary ports"; flags:S; flow:to_server; threshold:type both, track by_src, count 8, seconds 60; classtype:attempted-recon; sid:9901003; rev:2;)
alert tcp any any -> $HOME_NET 22 (msg:"AZAZEL CANARY SSH brute-force like pattern"; flags:S; flow:to_server; threshold:type both, track by_src, count 20, seconds 300; classtype:attempted-admin; sid:9901004; rev:2;)
alert tcp any any -> $HOME_NET 80 (msg:"AZAZEL CANARY HTTP burst access pattern"; flags:S; flow:to_server; threshold:type both, track by_src, count 30, seconds 120; classtype:web-application-attack; sid:9901005; rev:2;)
RULES
}

normalize_default_iface() {
  local defaults="/etc/default/suricata"
  [[ -f "$defaults" ]] || return 0

  if grep -q '^IFACE=' "$defaults"; then
    sed -i -E "s|^IFACE=.*$|IFACE=${WAN_IF}|" "$defaults"
  else
    printf '\nIFACE=%s\n' "$WAN_IF" >> "$defaults"
  fi
}

apply_yaml_minify() {
  local yaml_script="$SCRIPT_DIR/suricata_yaml_minify.sh"
  if [[ -x "$yaml_script" ]]; then
    "$yaml_script" \
      --yaml /etc/suricata/suricata.yaml \
      --default-rule-path /var/lib/suricata/rules \
      --rule-file suricata.rules \
      --no-test \
      --no-restart
  else
    warn "suricata_yaml_minify.sh not found; keeping existing YAML"
  fi
}

apply_pi_zero_tuning() {
  local yaml="/etc/suricata/suricata.yaml"
  [[ -f "$yaml" ]] || return 0

  # Lower stats logging frequency to reduce I/O.
  sed -i -E '0,/^([[:space:]]*interval:)[[:space:]]*[0-9]+/ s//\1 60/' "$yaml"

  # Pin AF_PACKET to a single thread and capture only OpenCanary-facing traffic.
  sed -i '/^af-packet:/,/^af-xdp:/ {
    s|^[[:space:]]*#\?threads:[[:space:]].*|  threads: '"${AF_PACKET_THREADS}"'|
    s|^[[:space:]]*#\?bpf-filter:[[:space:]].*|  bpf-filter: "'"${AF_PACKET_BPF}"'"|
  }' "$yaml"
}

ensure_yaml_header() {
  local yaml="/etc/suricata/suricata.yaml"
  [[ -f "$yaml" ]] || return 0

  local first second
  first="$(sed -n '1p' "$yaml" 2>/dev/null || true)"
  second="$(sed -n '2p' "$yaml" 2>/dev/null || true)"
  if [[ "$first" == "%YAML 1.1" && "$second" == "---" ]]; then
    return 0
  fi

  local tmp
  tmp="$(mktemp)"
  {
    echo "%YAML 1.1"
    echo "---"
    cat "$yaml"
  } > "$tmp"
  mv "$tmp" "$yaml"
}

apply_eve_alert_only() {
  local yaml="/etc/suricata/suricata.yaml"
  [[ -f "$yaml" ]] || return 0

  if ! command -v python3 >/dev/null 2>&1; then
    warn "python3 not found; skipping eve-log alert-only tuning"
    return 0
  fi

  if ! python3 - "$yaml" <<'PY'
import sys
from pathlib import Path

try:
    import yaml  # type: ignore
except Exception:
    sys.exit(2)

path = Path(sys.argv[1])
data = yaml.safe_load(path.read_text(encoding="utf-8"))
if not isinstance(data, dict):
    sys.exit(0)

outputs = data.get("outputs")
if not isinstance(outputs, list):
    sys.exit(0)

changed = False
for item in outputs:
    if not isinstance(item, dict) or "eve-log" not in item:
        continue
    eve = item.get("eve-log")
    if not isinstance(eve, dict):
        continue

    eve["enabled"] = True
    eve["community-id"] = False
    eve["pcap-file"] = False
    eve["payload"] = False
    eve["packet"] = False
    eve["metadata"] = False
    eve["http-body"] = False
    eve["http-body-printable"] = False
    eve["payload-printable"] = False
    eve["types"] = [{"alert": {"metadata": False, "payload": False, "packet": False}}]

    xff = eve.get("xff")
    if isinstance(xff, dict):
        xff["enabled"] = False
    else:
        eve["xff"] = {"enabled": False}

    changed = True

if changed:
    dumped = yaml.safe_dump(data, sort_keys=False, default_flow_style=False, allow_unicode=True)
    # Suricata requires YAML header lines at the top of suricata.yaml.
    output_text = "%YAML 1.1\n---\n" + dumped.lstrip()
    path.write_text(output_text, encoding="utf-8")
PY
  then
    warn "eve-log alert-only tuning skipped (PyYAML missing or parse failed)"
    return 0
  fi

  log "Applied eve-log alert-only tuning"
}

build_rules() {
  install -d /var/lib/suricata/rules /var/lib/suricata/update /etc/suricata/rules
  rm -f "$RULE_OUT"

  local compiled=0
  if command -v suricata-update >/dev/null 2>&1 && [[ ${#RULE_URLS[@]} -gt 0 ]]; then
    log "Refreshing Suricata sources"
    if [[ "$UPDATE_SOURCES" -eq 1 ]]; then
      suricata-update update-sources >/dev/null 2>&1 || warn "update-sources failed"
    fi
    suricata-update disable-source et/open >/dev/null 2>&1 || true

    local cmd=(suricata-update)
    local url
    for url in "${RULE_URLS[@]}"; do
      cmd+=(--url "$url")
    done

    log "Fetching profile '$PROFILE' rules"
    if "${cmd[@]}" >/dev/null 2>&1; then
      compiled=1
    else
      warn "suricata-update failed; falling back to local canary-only rules"
    fi
  else
    if [[ ${#RULE_URLS[@]} -eq 0 ]]; then
      log "Profile '$PROFILE' uses local canary-only rules"
    else
      warn "suricata-update not found; using local canary-only rules"
    fi
  fi

  write_canary_rules

  if [[ $compiled -eq 0 ]] || [[ ! -s "$RULE_OUT" ]]; then
    cp "$CUSTOM_RULES" "$RULE_OUT"
  else
    {
      printf '\n# --- Azazel canary local rules ---\n'
      cat "$CUSTOM_RULES"
    } >> "$RULE_OUT"
  fi

  ln -sf "$RULE_OUT" /etc/suricata/rules/suricata.rules
}

start_or_restart_suricata() {
  if ! command -v systemctl >/dev/null 2>&1; then
    warn "systemctl not found; skipping service restart"
    return 0
  fi

  systemctl stop suricata >/dev/null 2>&1 || true
  systemctl enable suricata >/dev/null 2>&1 || true
  systemctl restart suricata >/dev/null 2>&1 || systemctl start suricata >/dev/null 2>&1 || true
}

log "Applying Suricata profile=${PROFILE}, wan-if=${WAN_IF}"
build_rules
apply_yaml_minify
apply_pi_zero_tuning
ensure_yaml_header
apply_eve_alert_only
ensure_yaml_header
normalize_default_iface

if [[ "$DO_TEST" -eq 1 ]]; then
  log "Testing Suricata configuration"
  suricata -T -c /etc/suricata/suricata.yaml -v >/dev/null
fi

if [[ "$DO_RESTART" -eq 1 ]]; then
  start_or_restart_suricata
fi

log "Rules ready: $RULE_OUT"
log "Custom canary rules: $CUSTOM_RULES"
log "Done"
