#!/usr/bin/env bash
set -euo pipefail
iptables -N AZAZEL-FWD 2>/dev/null || true
iptables -C FORWARD -j AZAZEL-FWD 2>/dev/null || iptables -I FORWARD 1 -j AZAZEL-FWD
iptables -F AZAZEL-FWD

iptables -A AZAZEL-FWD -i usb0 -o wlan0 -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT
iptables -A AZAZEL-FWD -i wlan0 -o usb0 -m state --state ESTABLISHED,RELATED -j ACCEPT

# 漏洩・横移動の地雷系を遮断（外向き）
for p in 137:139 445 3389 1900 5353 5355 3702; do
  iptables -A AZAZEL-FWD -i usb0 -o wlan0 -p udp --dport $p -j REJECT
  iptables -A AZAZEL-FWD -i usb0 -o wlan0 -p tcp --dport $p -j REJECT
done

# QUICを切って観測性確保（任意）
iptables -A AZAZEL-FWD -i usb0 -o wlan0 -p udp --dport 443 -j REJECT || true

echo "[shield_mode] Leakage protocols blocked; QUIC disabled."