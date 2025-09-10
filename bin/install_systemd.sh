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
EOF

# systemd unit を配置
sudo install -m 0644 "${ROOT}/systemd/azazel-boot-splash.service" /etc/systemd/system/
sudo install -m 0644 "${ROOT}/systemd/azazel-console.service" /etc/systemd/system/
sudo install -m 0644 "${ROOT}/systemd/azazel-epd.service"     /etc/systemd/system/
sudo install -m 0644 "${ROOT}/systemd/suri-epaper.service"    /etc/systemd/system/
sudo install -m 0644 "${ROOT}/systemd/opencanary.service"     /etc/systemd/system/

# 反映・起動
sudo systemctl daemon-reload
sudo systemctl enable --now azazel-console.service
sudo systemctl enable --now suri-epaper.service
echo "Units installed. Edit opencanary.service if needed, then: sudo systemctl enable --now opencanary.service"