#!/bin/bash
# Azazel-Gadget DHCP/DNS トラブルシューティングスクリプト
# usb0のセットアップとdnsmasqの動作を診断します

set -u

echo "=================================================="
echo "Azazel-Gadget DHCP/DNS Diagnostics"
echo "=================================================="
echo ""

# 1. usb0インターフェースの確認
echo "[1] Checking usb0 interface status..."
if ip link show usb0 >/dev/null 2>&1; then
    STATUS=$(ip link show usb0 | grep -o "UP\|DOWN" | head -1)
    echo "✓ usb0 exists and is ${STATUS}"
    
    # IPアドレス確認
    IP_INFO=$(ip addr show usb0 2>/dev/null | grep "inet " | awk '{print $2}')
    if [ -z "$IP_INFO" ]; then
        echo "⚠ usb0 has NO IP address assigned!"
    else
        echo "✓ usb0 has IP: $IP_INFO"
    fi
else
    echo "✗ usb0 interface NOT FOUND"
fi
echo ""

# 2. usb0-staticサービスの確認
echo "[2] Checking usb0-static.service status..."
if systemctl is-active --quiet usb0-static.service; then
    echo "✓ usb0-static.service is ACTIVE"
else
    echo "✗ usb0-static.service is NOT ACTIVE"
    echo "  Status:"
    systemctl status usb0-static.service 2>&1 | grep -E "(Active|Loaded)"
fi
echo ""

# 3. azazel-first-minute.serviceの確認
echo "[3] Checking azazel-first-minute.service status..."
if systemctl is-active --quiet azazel-first-minute.service; then
    echo "✓ azazel-first-minute.service is ACTIVE"
else
    echo "✗ azazel-first-minute.service is NOT ACTIVE"
fi

# ログ確認
RECENT_ERRORS=$(journalctl -u azazel-first-minute.service -n 20 --no-pager 2>&1 | grep -i "error\|fail" | head -3)
if [ -n "$RECENT_ERRORS" ]; then
    echo "  Recent errors:"
    echo "$RECENT_ERRORS" | sed 's/^/    /'
fi
echo ""

# 4. dnsmasqプロセスの確認
echo "[4] Checking dnsmasq process..."
DNSMASQ_PID=$(pgrep -f "dnsmasq.*first_minute" || true)
if [ -n "$DNSMASQ_PID" ]; then
    echo "✓ dnsmasq is running (PID: $DNSMASQ_PID)"
else
    echo "✗ dnsmasq is NOT running"
    # システムにインストールされているか確認
    if ! command -v dnsmasq &> /dev/null; then
        echo "  ERROR: dnsmasq is not installed"
        echo "  Install with: sudo apt-get install dnsmasq"
    fi
fi
echo ""

# 5. dnsmasq設定ファイルの確認
echo "[5] Checking dnsmasq configuration..."
CONF_FILE="/etc/azazel-gadget/dnsmasq-first_minute.conf"
if [ ! -f "$CONF_FILE" ] && [ -f "/etc/azazel-zero/dnsmasq-first_minute.conf" ]; then
    CONF_FILE="/etc/azazel-zero/dnsmasq-first_minute.conf"  # backward compatibility
fi
if [ -f "$CONF_FILE" ]; then
    echo "✓ Config file exists: $CONF_FILE"
    
    # 重要な設定項目を確認
    echo "  Key settings:"
    grep -E "^(interface|listen-address|dhcp-range)" "$CONF_FILE" | sed 's/^/    /'
else
    echo "✗ Config file NOT found: $CONF_FILE"
    echo "  Run: sudo bash bin/install_systemd.sh"
fi
echo ""

# 6. dnsmasqログの確認
echo "[6] Checking dnsmasq log for errors..."
LOG_FILE="/var/log/azazel-dnsmasq.log"
if [ -f "$LOG_FILE" ]; then
    ERRORS=$(tail -20 "$LOG_FILE" 2>/dev/null | grep -i "error\|fail\|cannot\|unable" | head -3)
    if [ -z "$ERRORS" ]; then
        echo "✓ No recent errors in dnsmasq log"
    else
        echo "✗ Recent errors found:"
        echo "$ERRORS" | sed 's/^/    /'
    fi
    
    # DHCPリース配布があったか確認
    LEASES=$(tail -20 "$LOG_FILE" 2>/dev/null | grep -i "dhcp" | head -2)
    if [ -z "$LEASES" ]; then
        echo "⚠ No DHCP activity in recent log"
    else
        echo "✓ DHCP activity detected:"
        echo "$LEASES" | sed 's/^/    /'
    fi
else
    echo "⚠ Log file not found: $LOG_FILE"
fi
echo ""

# 7. ネットワークインターフェース統計
echo "[7] Network statistics..."
echo "  usb0 RX/TX:"
ip -s link show usb0 2>/dev/null | tail -2 | sed 's/^/    /' || echo "    (unavailable)"
echo ""

# 8. ローカルポート確認
echo "[8] Checking if DHCP/DNS ports are listening..."
if ss -ultn | grep -q ":53 "; then
    echo "✓ Port 53 (DNS) is listening"
else
    echo "✗ Port 53 (DNS) is NOT listening"
fi

if ss -ultn | grep -q ":67 "; then
    echo "✓ Port 67 (DHCP) is listening"
else
    echo "✗ Port 67 (DHCP) is NOT listening"
fi
echo ""

# 9. 修復チェック
echo "[9] Quick fixes..."
echo "  If usb0 is not UP:"
echo "    sudo ip link set usb0 up"
echo "    sudo ip addr add 10.55.0.10/24 dev usb0"
echo ""
echo "  If dnsmasq is not running:"
echo "    sudo systemctl restart azazel-first-minute.service"
echo ""
echo "  To view detailed logs:"
echo "    journalctl -u azazel-first-minute.service -f"
echo "    tail -f /var/log/azazel-dnsmasq.log"
echo ""

echo "=================================================="
echo "End of Diagnostics"
echo "=================================================="
