#!/usr/bin/env bash
set -euo pipefail
[ -f /etc/default/azazel-zero ] && . /etc/default/azazel-zero || true
# 既定は /etc/default/azazel-zero の WAN_IF、引数で上書き可
CONF_WAN="${WAN_IF:-wlan0}"
WAN="${1:-$CONF_WAN}"

# mangle チェーン解除
# PREROUTING のフック解除（delay_on.sh と整合）
iptables -t mangle -D PREROUTING -j AZAZEL-DRAG 2>/dev/null || true
iptables -t mangle -F AZAZEL-DRAG 2>/dev/null || true
iptables -t mangle -X AZAZEL-DRAG 2>/dev/null || true

# qdisc 削除（存在時のみ）
tc qdisc del dev "$WAN" root 2>/dev/null || true

echo "[delay_off] WAN=${WAN} removed."