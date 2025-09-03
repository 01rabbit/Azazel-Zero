#!/usr/bin/env bash
set -euo pipefail
iptables -N AZAZEL-FWD 2>/dev/null || true
iptables -C FORWARD -j AZAZEL-FWD 2>/dev/null || iptables -I FORWARD 1 -j AZAZEL-FWD
iptables -F AZAZEL-FWD

# 80/443 のみ通す（tcp）
iptables -A AZAZEL-FWD -i usb0 -o wlan0 -p tcp -m multiport --dports 80,443 -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT
iptables -A AZAZEL-FWD -i wlan0 -o usb0 -m state --state ESTABLISHED,RELATED -j ACCEPT
# それ以外は黙って既定に落ちる（または最後にREJECTを追加してもよい）

echo "[lockdown_mode] Only TCP 80/443 allowed outward."