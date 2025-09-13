#!/usr/bin/env bash
# Azazel-Zero bootstrap script
# Purpose: reproducible setup on Raspberry Pi Zero 2 W
# Usage: sudo tools/bootstrap_zero.sh [--no-epd] [--no-enable] [--no-suricata] [--dry-run]

set -euo pipefail

# ---------- defaults ----------
WITH_EPD=1
ENABLE_SERVICES=1
RUN_SURICATA_UPDATE=1
LOG=${LOG:-/var/log/azazel-bootstrap.log}
AZAZEL_ROOT="${AZAZEL_ROOT:-$HOME/Azazel-Zero}"

# ---------- helpers ----------
log(){ echo "[+] $*" | tee -a "$LOG"; }
warn(){ echo "[!] $*" | tee -a "$LOG" >&2; }
fail(){ echo "[x] $*" | tee -a "$LOG" >&2; exit 1; }
require_root(){ if [ "${EUID:-$(id -u)}" -ne 0 ]; then fail "Run as root"; fi; }
cmd(){ echo "+ $*" | tee -a "$LOG"; eval "$@" | tee -a "$LOG"; }

# ---------- preflight checks (non-fatal) ----------
check_spi(){
  local cfg
  for cfg in /boot/config.txt /boot/firmware/config.txt; do
    if [ -f "$cfg" ]; then
      if grep -Eq '^\s*dtparam=spi=on' "$cfg"; then log "SPI appears enabled in $cfg"; return 0; fi
    fi
  done
  warn "SPI does not appear enabled (dtparam=spi=on not found). Enable via 'raspi-config'."
}

check_gadget(){
  local c1=/boot/cmdline.txt c2=/boot/firmware/cmdline.txt cfg1=/boot/config.txt cfg2=/boot/firmware/config.txt
  local cmdf="" cfgf=""
  for f in "$c1" "$c2"; do [ -f "$f" ] && cmdf="$f" && break; done
  for f in "$cfg1" "$cfg2"; do [ -f "$f" ] && cfgf="$f" && break; done
  if [ -n "$cfgf" ] && ! grep -Eq 'dtoverlay=dwc2' "$cfgf"; then warn "USB Gadget overlay missing in $cfgf (dtoverlay=dwc2)."; fi
  if [ -n "$cmdf" ] && ! grep -Eq 'modules-load=dwc2,g_ether' "$cmdf"; then warn "USB Gadget modules not found in $cmdf (modules-load=dwc2,g_ether)."; fi
}

# ---------- steps ----------
install_deps(){
  log "Installing dependencies (WITH_EPD=${WITH_EPD})"
  if [ "$WITH_EPD" -eq 1 ]; then
    cmd "bash $AZAZEL_ROOT/bin/install_dependencies.sh --all"
  else
    cmd "bash $AZAZEL_ROOT/bin/install_dependencies.sh"
  fi
}

install_systemd(){
  log "Installing systemd units"
  cmd "bash $AZAZEL_ROOT/bin/install_systemd.sh"
}

suricata_minimal(){
  if [ "$RUN_SURICATA_UPDATE" -eq 1 ]; then
    log "Configuring minimal Suricata rules"
    cmd "bash $AZAZEL_ROOT/bin/suricata_update.sh"
  else
    log "Skipping Suricata update"
  fi
}

enable_services(){
  if [ "$ENABLE_SERVICES" -eq 1 ]; then
    log "Enabling services"
    systemctl enable --now azazel-epd.service suri-epaper.service azazel-console.service || true
    systemctl enable --now opencanary.service || true
  else
    log "Skipping systemctl enable --now"
  fi
}

smoke_test(){
  log "Smoke test"
  systemctl is-active azazel-epd.service >/dev/null && log "EPD OK" || warn "EPD not active"
  systemctl is-active suri-epaper.service >/dev/null && log "suri-epaper OK" || warn "suri-epaper not active"
  if tmux ls 2>/dev/null | grep -q azazel; then log "tmux console OK"; else warn "tmux console missing"; fi
}

usage(){
  cat <<USAGE
Usage: sudo tools/bootstrap_zero.sh [--no-epd] [--no-enable] [--no-suricata] [--dry-run]
  --no-epd        Skip E-Paper optional deps
  --no-enable     Do not enable/start services
  --no-suricata   Skip minimal Suricata rules
  --dry-run       Show steps only
USAGE
}

main(){
  require_root
  mkdir -p "$(dirname "$LOG")" && : >"$LOG"

  local DRY=0
  for a in "$@"; do
    case "$a" in
      --no-epd) WITH_EPD=0 ;;
      --no-enable) ENABLE_SERVICES=0 ;;
      --no-suricata) RUN_SURICATA_UPDATE=0 ;;
      --dry-run) DRY=1 ;;
      -h|--help) usage; exit 0 ;;
      *) fail "Unknown arg: $a" ;;
    esac
  done

  log "Bootstrap start (AZAZEL_ROOT=$AZAZEL_ROOT)"
  check_spi
  check_gadget

  if [ "$DRY" -eq 1 ]; then
    echo "Would run: install_deps -> install_systemd -> suricata_minimal -> enable_services -> smoke_test"
    exit 0
  fi

  install_deps
  install_systemd
  suricata_minimal
  enable_services
  smoke_test
  log "Bootstrap complete"
}

main "$@"
