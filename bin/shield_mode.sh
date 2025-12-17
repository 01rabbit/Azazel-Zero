#!/usr/bin/env bash
set -euo pipefail
# 共通設定の読込（存在しなくても続行）
[ -f /etc/default/azazel-zero ] && . /etc/default/azazel-zero || true
# 既定は環境ファイルの USB_IF/WAN_IF、引数で上書き可
CONF_USB="${USB_IF:-usb0}"
CONF_WAN="${WAN_IF:-wlan0}"
USB="${1:-$CONF_USB}"
WAN="${2:-$CONF_WAN}"
iptables -N AZAZEL-FWD 2>/dev/null || true
iptables -C FORWARD -j AZAZEL-FWD 2>/dev/null || iptables -I FORWARD 1 -j AZAZEL-FWD
iptables -F AZAZEL-FWD

iptables -A AZAZEL-FWD -i "$USB" -o "$WAN" -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT
iptables -A AZAZEL-FWD -i "$WAN" -o "$USB" -m state --state ESTABLISHED,RELATED -j ACCEPT

# 漏洩・横移動の地雷系を遮断（外向き）
for p in 137:139 445 3389 1900 5353 5355 3702; do
  iptables -A AZAZEL-FWD -i "$USB" -o "$WAN" -p udp --dport $p -j REJECT
  iptables -A AZAZEL-FWD -i "$USB" -o "$WAN" -p tcp --dport $p -j REJECT
done

# QUICを切って観測性確保（任意）
iptables -A AZAZEL-FWD -i "$USB" -o "$WAN" -p udp --dport 443 -j REJECT || true

echo "[shield_mode] Leakage protocols blocked; QUIC disabled."