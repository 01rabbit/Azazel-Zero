#!/usr/bin/env bash
# set -euo pipefail
set -euo pipefail
# 共通設定の読込（存在しなくても続行）
[ -f /etc/default/azazel-zero ] && . /etc/default/azazel-zero || true
# 既定は環境ファイルの USB_IF/WAN_IF、引数で上書き可
CONF_USB="${USB_IF:-usb0}"
CONF_WAN="${WAN_IF:-wlan0}"
USB="${1:-$CONF_USB}"
WAN="${2:-$CONF_WAN}"
# FORWARD 早期チェーン
iptables -N AZAZEL-FWD 2>/dev/null || true
iptables -C FORWARD -j AZAZEL-FWD 2>/dev/null || iptables -I FORWARD 1 -j AZAZEL-FWD
iptables -F AZAZEL-FWD

# 既定はDROPだとして $USB→$WAN の NEW/EST/REL を許可
iptables -A AZAZEL-FWD -i "$USB" -o "$WAN" -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT
iptables -A AZAZEL-FWD -i "$WAN" -o "$USB" -m state --state ESTABLISHED,RELATED -j ACCEPT

# ポータル突破のため 80/443 は素通し。他は既定ルールに委ねる
echo "[portal_mode] Basic forwarding relaxed for portal auth."