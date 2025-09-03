#!/usr/bin/env bash
set -euo pipefail
WAN="${1:-wlan0}"

# mangle チェーン解除
iptables -t mangle -D POSTROUTING -j AZAZEL-DRAG 2>/dev/null || true
iptables -t mangle -F AZAZEL-DRAG 2>/dev/null || true
iptables -t mangle -X AZAZEL-DRAG 2>/dev/null || true

# qdisc 削除（存在時のみ）
tc qdisc del dev "$WAN" root 2>/dev/null || true

echo "[delay_off] WAN=${WAN} removed."