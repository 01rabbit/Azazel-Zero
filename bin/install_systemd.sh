#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "AZAZEL_ROOT=${ROOT}"

# スクリプトを /usr/local/bin へ
install -m 0755 "${ROOT}/bin/azazel_console.sh"     /usr/local/bin/
install -m 0755 "${ROOT}/bin/update_epaper_tmux.sh" /usr/local/bin/
install -m 0755 "${ROOT}/bin/delay_on.sh"           /usr/local/bin/
install -m 0755 "${ROOT}/bin/delay_off.sh"          /usr/local/bin/
install -m 0755 "${ROOT}/bin/portal_mode.sh"        /usr/local/bin/
install -m 0755 "${ROOT}/bin/shield_mode.sh"        /usr/local/bin/
install -m 0755 "${ROOT}/bin/lockdown_mode.sh"      /usr/local/bin/
install -m 0755 "${ROOT}/bin/suri_epaper.sh"        /usr/local/bin/

# 環境ファイル
sudo install -d /etc/default
sudo tee /etc/default/azazel-zero >/dev/null <<EOF
AZAZEL_ROOT=${ROOT}
AZAZEL_CANARY_VENV=/home/azazel/canary-venv

# 統一したEPDパスとロックファイル
EPD_PY=${ROOT}/py/boot_splash_epd.py
EPD_LOCK=/run/azazel-epd.lock

# ネットワーク関連（遅滞制御用）
WAN_IF=wlan0
USB_IF=usb0
SUBNET=192.168.7.0/24
EOF

# systemd unit を配置
sudo install -m 0644 "${ROOT}/systemd/azazel-console.service" /etc/systemd/system/
sudo install -m 0644 "${ROOT}/systemd/azazel-epd.service"     /etc/systemd/system/
sudo install -m 0644 "${ROOT}/systemd/suri-epaper.service"    /etc/systemd/system/
sudo install -m 0644 "${ROOT}/systemd/opencanary.service"     /etc/systemd/system/

# 反映・起動
sudo systemctl daemon-reload
sudo systemctl enable --now azazel-console.service
sudo systemctl enable --now suri-epaper.service
echo "Units installed. Edit opencanary.service if needed, then: sudo systemctl enable --now opencanary.service"