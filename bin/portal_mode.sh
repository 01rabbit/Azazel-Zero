#!/usr/bin/env bash
set -euo pipefail
# FORWARD 早期チェーン
iptables -N AZAZEL-FWD 2>/dev/null || true
iptables -C FORWARD -j AZAZEL-FWD 2>/dev/null || iptables -I FORWARD 1 -j AZAZEL-FWD
iptables -F AZAZEL-FWD

# 既定はDROPだとして usb0→wlan0 の NEW/EST/REL を許可
iptables -A AZAZEL-FWD -i usb0 -o wlan0 -m state --state NEW,ESTABLISHED,RELATED -j ACCEPT
iptables -A AZAZEL-FWD -i wlan0 -o usb0 -m state --state ESTABLISHED,RELATED -j ACCEPT

# ポータル突破のため 80/443 は素通し。他は既定ルールに委ねる
echo "[portal_mode] Basic forwarding relaxed for portal auth."