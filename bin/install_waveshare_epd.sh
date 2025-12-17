#!/usr/bin/env bash
# Waveshare e-Paper function library installer for Raspberry Pi Zero 2 W
# Mirrors the official steps (apt → git clone → demo archive) following Raspberry Pi OS Trixie/PEP 668 best practices (no system-wide pip).
set -euo pipefail

TARGET_DIR=${TARGET_DIR:-/opt/waveshare-epd}
DEMO_URL="https://files.waveshare.com/upload/7/71/E-Paper_code.zip"
DEMO_SUBDIR="e-Paper"
OWNER=${AZAZEL_WAVESHARE_OWNER:-pi}
RUN_DEMO=0
SKIP_APT=0
SKIP_DEMO=0
DEMO_SCRIPT="epd_2in13b_V4_test.py"

log(){ printf "[waveshare] %s\n" "$*"; }
die(){ printf "[waveshare][ERROR] %s\n" "$*" >&2; exit 1; }

usage(){
  cat <<USAGE
Usage: sudo bin/install_waveshare_epd.sh [--skip-apt] [--skip-demo-download] [--run-demo]
  --skip-apt            Skip apt-get update/install (use when already satisfied)
  --skip-demo-download  Do not download/unpack E-Paper_code.zip
  --run-demo            Execute python3 ${DEMO_SCRIPT} after installation
Environment variables:
  TARGET_DIR                 Destination for the Waveshare repository (default: /opt/waveshare-epd)
  AZAZEL_WAVESHARE_OWNER     Owner applied via chown if the user exists (default: pi)
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-apt) SKIP_APT=1 ;;
    --skip-demo-download) SKIP_DEMO=1 ;;
    --run-demo) RUN_DEMO=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
  shift
done

[[ ${EUID:-$(id -u)} -eq 0 ]] || die "Run with sudo/root."

APT_PACKAGES=(
  python3-pil python3-numpy python3-dev
  python3-rpi.gpio python3-gpiozero python3-spidev git wget unzip p7zip-full
)

if [[ $SKIP_APT -eq 0 ]]; then
  log "Updating apt and installing required packages…"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y
  apt-get install -y "${APT_PACKAGES[@]}"
else
  log "Skipping apt operations (per --skip-apt)."
fi

log "Raspberry Pi OS Trixie uses an externally-managed Python environment (PEP 668). Installing Python deps via apt (no system-wide pip)."

log "Ensuring Waveshare e-Paper repo exists at ${TARGET_DIR}…"
mkdir -p "$(dirname "$TARGET_DIR")"
if [[ -d "$TARGET_DIR/.git" ]]; then
  git -C "$TARGET_DIR" fetch --all --prune
  git -C "$TARGET_DIR" reset --hard origin/master
else
  rm -rf "$TARGET_DIR"
  git clone https://github.com/waveshare/e-Paper "$TARGET_DIR"
fi

if [[ $SKIP_DEMO -eq 0 ]]; then
  log "Downloading demo archive (${DEMO_URL})…"
  tmp_zip=$(mktemp)
  wget -qO "$tmp_zip" "$DEMO_URL"
  demo_root="${TARGET_DIR}/${DEMO_SUBDIR}"
  rm -rf "$demo_root"
  mkdir -p "$demo_root"
  log "Extracting archive into ${demo_root}…"
  unzip -oq "$tmp_zip" -d "$demo_root"
  rm -f "$tmp_zip"
else
  log "Skipping demo archive download (per --skip-demo-download)."
fi

if id "$OWNER" >/dev/null 2>&1; then
  log "Applying ownership to ${OWNER}:${OWNER}"
  chown -R "$OWNER:$OWNER" "$TARGET_DIR"
else
  log "Owner ${OWNER} not found; skipping chown."
fi

if [[ $RUN_DEMO -eq 1 ]]; then
  demo_dir="${TARGET_DIR}/${DEMO_SUBDIR}/RaspberryPi_JetsonNano/python/examples"
  [[ -d "$demo_dir" ]] || die "Demo directory missing: $demo_dir"
  log "Running demo: python3 ${DEMO_SCRIPT}"
  ( cd "$demo_dir" && python3 "$DEMO_SCRIPT" )
fi

log "Waveshare function library installation complete."
