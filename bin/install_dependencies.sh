#!/bin/bash
# Re-exec under bash if not already
[ -n "${BASH_VERSION:-}" ] || exec /bin/bash "$0" "$@"

# Azazel-Zero: Dependency installer (Bullseye 32-bit, Pi Zero 2 W)
# Installs base packages for: networking (iptables/tc/dnsmasq), tmux, Suricata, and optional OpenCanary & Waveshare E-Paper deps.
# Usage:
#   sudo bin/install_dependencies.sh               # base only
#   sudo bin/install_dependencies.sh --with-canary # + OpenCanary (venv)
#   sudo bin/install_dependencies.sh --with-epd    # + Waveshare epaper python deps
#   sudo bin/install_dependencies.sh --all         # base + canary + epd

set -euo pipefail

WITH_CANARY=0
WITH_EPD=0
if [[ "${1:-}" == "--all" ]]; then
  WITH_CANARY=1
  WITH_EPD=1
else
  for arg in "$@"; do
    case "$arg" in
      --with-canary) WITH_CANARY=1 ;;
      --with-epd)    WITH_EPD=1 ;;
      *) echo "[WARN] Unknown option: $arg" >&2 ;;
    esac
  done
fi

log(){ printf "[deps] %s\n" "$*"; }
die(){ printf "[deps][ERROR] %s\n" "$*" >&2; exit 1; }

# Sanity
command -v apt-get >/dev/null || die "This script expects Debian/apt."
if [[ $EUID -ne 0 ]]; then
  die "Run with sudo/root."
fi

# Base packages
log "Updating apt and installing base packages…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  iproute2 iptables iptables-persistent dnsmasq \
  jq tmux git curl ca-certificates \
  python3 python3-venv python3-pip \
  suricata suricata-update

# Persist iptables on reboot (no prompt)
if ! systemctl is-enabled netfilter-persistent >/dev/null 2>&1; then
  log "Enabling iptables-persistent (netfilter-persistent)…"
  systemctl enable netfilter-persistent || true
fi

# Optional: OpenCanary (venv)
if [[ "$WITH_CANARY" -eq 1 ]]; then
  log "Installing OpenCanary into venv: /home/azazel/canary-venv"
  id azazel >/dev/null 2>&1 || useradd -m -s /bin/bash azazel
  sudo -u azazel python3 -m venv /home/azazel/canary-venv
  sudo -u azazel /home/azazel/canary-venv/bin/pip install --upgrade pip wheel
  # OpenCanary依存は環境によってビルドが走るため、wheelを先に上げておく
  sudo -u azazel /home/azazel/canary-venv/bin/pip install opencanary
  # 初期設定ファイル
  if [[ ! -f /home/azazel/.opencanary.conf ]]; then
    log "Creating default OpenCanary config"
    sudo -u azazel /home/azazel/canary-venv/bin/opencanaryd --copyconfig || true
  fi
fi

# Optional: Waveshare E-Paper deps (for boot_splash_epd.py)
if [[ "$WITH_EPD" -eq 1 ]]; then
  log "Installing Waveshare E-Paper Python dependencies (system-wide)"
  apt-get install -y python3-dev python3-numpy python3-pil python3-spidev python3-rpi.gpio
  # numpy/pillow はOSパッケージで十分。必要に応じて最新版へ:
  # pip3 install --no-cache-dir --upgrade pillow numpy
fi

# Finish
log "Base dependencies installed."
if [[ "$WITH_CANARY" -eq 1 ]]; then
  log "OpenCanary venv ready at /home/azazel/canary-venv"
fi
if [[ "$WITH_EPD" -eq 1 ]]; then
  log "Waveshare E-Paper deps installed."
fi

cat <<'NEXT'
[Next steps]
1) Register scripts/services:
   sudo bash bin/install_systemd.sh

2) Prepare Suricata minimal config (optional but recommended for Pi Zero):
   sudo bash bin/suricata_yaml_minify.sh
   sudo bash bin/suricata_update.sh

3) Start OpenCanary (if installed) after editing ~/.opencanary.conf as needed:
   sudo systemctl enable --now opencanary.service
NEXT