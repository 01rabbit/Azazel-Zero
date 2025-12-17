#!/usr/bin/env bash
set -euo pipefail
[ -f /etc/default/azazel-zero ] && . /etc/default/azazel-zero || true
# 既定は /etc/default/azazel-zero の WAN_IF / SUBNET、引数で上書き可
CONF_WAN="${WAN_IF:-wlan0}"
CONF_SUBNET="${SUBNET:-192.168.7.0/24}"
WAN="${1:-$CONF_WAN}"                  # egress 側
SUBNET="${2:-$CONF_SUBNET}"            # usb0 側セグメント
MARK=0x66

#
# mangle 用チェーン（idempotentなセットアップとチェイン挿入）
iptables -t mangle -N AZAZEL-DRAG 2>/dev/null || true
# PREROUTING にフック（重複回避）
iptables -t mangle -C PREROUTING -j AZAZEL-DRAG 2>/dev/null || iptables -t mangle -A PREROUTING -j AZAZEL-DRAG

#
# 60秒以内のSYNを recent で判定し、既知はマーキング、新規は recent 登録
iptables -t mangle -F AZAZEL-DRAG
iptables -t mangle -A AZAZEL-DRAG -o "$WAN" -p tcp --syn -s "$SUBNET" -m recent --name DRAG --update --seconds 60 --hitcount 1 -j MARK --set-xmark ${MARK}/0xffffffff
iptables -t mangle -A AZAZEL-DRAG -o "$WAN" -p tcp --syn -s "$SUBNET" -m recent --name DRAG --set

# egress shaping（HTB+netem、軽め）
if ! tc qdisc show dev "$WAN" | grep -q "handle 1: root htb"; then
  tc qdisc add dev "$WAN" handle 1: root htb default 10
  tc class add dev "$WAN" parent 1: classid 1:10 htb rate 20mbit ceil 20mbit
  tc class add dev "$WAN" parent 1: classid 1:20 htb rate 2mbit  ceil 5mbit
  tc qdisc add  dev "$WAN" parent 1:20 handle 20: netem delay 150ms 50ms distribution normal loss 0.1%
fi
# fwmark 振り分け（重複回避）
tc filter replace dev "$WAN" protocol ip parent 1: prio 1 handle ${MARK} fw flowid 1:20

echo "[delay_on] WAN=${WAN} SUBNET=${SUBNET} mark=${MARK}"