#!/bin/bash
################################################################################
# installer/_lib.sh - Unified Installer Common Library
# 
# インストーラ全体で共有される関数とユーティリティ
# 直接実行せず、他のスクリプトから source される
################################################################################

set -euo pipefail

# ============================================================================
# グローバル変数
# ============================================================================

# インストーラのルート
INSTALLER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$INSTALLER_ROOT")"
DEFAULTS_DIR="$INSTALLER_ROOT/defaults"
STAGES_DIR="$INSTALLER_ROOT/stages"
LOGS_DIR="$INSTALLER_ROOT/logs"

# インストール状態ファイル（再開用）
INSTALL_STATE_FILE="/tmp/azazel-install-state-$(id -u).json"

# タイムスタンプ
INSTALL_TIMESTAMP=$(date +%Y%m%d-%H%M%S)

# ロギング
LOG_FILE="$LOGS_DIR/install_$INSTALL_TIMESTAMP.log"

# ============================================================================
# ロギング関数
# ============================================================================

log() {
    local level="$1"
    shift
    local msg="[$level] $*"
    echo "$msg" | tee -a "$LOG_FILE"
}

log_info() { log "INFO" "$@"; }
log_warn() { log "WARN" "$@"; }
log_error() { log "ERROR" "$@"; }
log_debug() { 
    if [[ "${DEBUG:-0}" == "1" ]]; then
        log "DEBUG" "$@"
    fi
}

# ============================================================================
# エラーハンドリング
# ============================================================================

die() {
    log_error "$@"
    exit 1
}

ensure_root() {
    if [[ $EUID -ne 0 ]]; then
        die "このスクリプトはroot権限で実行する必要があります"
    fi
}

# ============================================================================
# OS/環境チェック
# ============================================================================

check_os() {
    if ! grep -q "Raspberry Pi" /proc/cpuinfo 2>/dev/null && \
       ! grep -q "BCM" /proc/cpuinfo 2>/dev/null; then
        log_warn "Raspberry Pi ではないマシンで実行されています"
    fi
    
    if ! grep -q "aarch64\|arm64" /proc/cpuinfo; then
        log_warn "64-bit ARM CPU ではありません（32-bit の可能性）"
    fi
    
    if ! grep -iq "Debian\|Raspbian\|Ubuntu" /etc/os-release; then
        die "Debian/Raspberry Pi OS 系統以外は非対応です"
    fi
}

check_disk_space() {
    local min_space_mb=2000  # 2GB
    local free_space_kb=$(df /home | awk 'NR==2 {print $4}')
    local free_space_mb=$((free_space_kb / 1024))
    
    if [[ $free_space_mb -lt $min_space_mb ]]; then
        die "ディスク空き容量が不足しています (最小: ${min_space_mb}MB, 現在: ${free_space_mb}MB)"
    fi
    
    log_info "ディスク空き容量: ${free_space_mb}MB (OK)"
}

# ============================================================================
# ネットワークチェック
# ============================================================================

check_interface() {
    local iface="$1"
    if ! ip link show "$iface" >/dev/null 2>&1; then
        return 1
    fi
    return 0
}

get_interface_ip() {
    local iface="$1"
    ip -4 addr show "$iface" 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1 || echo ""
}

is_interface_up() {
    local iface="$1"
    ip link show "$iface" | grep -q "UP" && return 0 || return 1
}

# ============================================================================
# 状態ファイル管理
# ============================================================================

init_state_file() {
    mkdir -p "$(dirname "$INSTALL_STATE_FILE")"
    
    # 新規作成
    cat > "$INSTALL_STATE_FILE" <<EOF
{
    "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
    "stage": 0,
    "wlan0_ip_before": "$(get_interface_ip wlan0 || echo 'unknown')",
    "usb0_ip_before": "$(get_interface_ip usb0 || echo 'unknown')",
    "options": "$*",
    "passed_stages": []
}
EOF
    log_info "インストール状態ファイル作成: $INSTALL_STATE_FILE"
}

get_state() {
    local key="$1"
    if [[ -f "$INSTALL_STATE_FILE" ]]; then
        jq -r ".$key // empty" "$INSTALL_STATE_FILE" 2>/dev/null || echo ""
    fi
}

set_state() {
    local key="$1"
    local value="$2"
    
    if [[ ! -f "$INSTALL_STATE_FILE" ]]; then
        init_state_file
    fi
    
    # JSON 更新（jq 使用）
    jq ".\"$key\" = \"$value\"" "$INSTALL_STATE_FILE" > "${INSTALL_STATE_FILE}.tmp" 2>/dev/null || {
        # jq がない場合は sed で代用（簡易版）
        sed -i "s/\"$key\": \"[^\"]*\"/\"$key\": \"$value\"/" "$INSTALL_STATE_FILE"
        return
    }
    mv "${INSTALL_STATE_FILE}.tmp" "$INSTALL_STATE_FILE"
}

mark_stage_passed() {
    local stage="$1"
    log_info "Stage $stage 完了"
    # 状態ファイルに記録（簡易版）
    date >> "$LOG_FILE"
}

# ============================================================================
# パッケージ管理
# ============================================================================

install_package() {
    local pkg="$1"
    
    # dpkg-query で正確にパッケージの状態を確認
    if dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q "install ok installed"; then
        log_debug "パッケージ既にインストール: $pkg"
        return 0
    fi
    
    log_info "パッケージをインストール: $pkg"
    if apt-get install -y "$pkg" >> "$LOG_FILE" 2>&1; then
        log_debug "✓ $pkg インストール成功"
        return 0
    else
        log_warn "⚠ $pkg のインストールに失敗（スキップして続行）"
        echo "[WARN] apt-get install -y $pkg の出力:" >> "$LOG_FILE"
        apt-get install -y "$pkg" >> "$LOG_FILE" 2>&1 || true
        return 1
    fi
}

install_packages() {
    local packages=("$@")
    
    # apt update は呼び出し側で実施済みと想定（重複回避）
    local failed_packages=()
    
    for pkg in "${packages[@]}"; do
        if ! install_package "$pkg"; then
            failed_packages+=("$pkg")
        fi
    done
    
    # 失敗したパッケージがあれば警告（致命的ではない）
    if [[ ${#failed_packages[@]} -gt 0 ]]; then
        log_warn "以下のパッケージのインストールに失敗しました（続行）："
        for pkg in "${failed_packages[@]}"; do
            log_warn "  - $pkg"
        done
    fi
    
    return 0
}

# ============================================================================
# ファイル配置
# ============================================================================

install_config() {
    local src="$1"
    local dest="$2"
    local mode="${3:-0644}"
    
    if [[ ! -f "$src" ]]; then
        die "ソースファイルが見つかりません: $src"
    fi
    
    log_info "設定ファイルを配置: $dest"
    mkdir -p "$(dirname "$dest")"
    install -m "$mode" "$src" "$dest" || die "ファイル配置失敗: $dest"
}

install_script() {
    local src="$1"
    local dest="$2"
    if [[ ! -f "$src" ]]; then
        die "ソースファイルが見つかりません: $src"
    fi

    log_info "スクリプトを配置: $dest"
    mkdir -p "$(dirname "$dest")"

    local tmp
    tmp="$(mktemp)"
    # Keep scripts executable by normalizing UTF-8 BOM and CRLF line endings.
    awk 'NR==1{sub(/^\xef\xbb\xbf/,"")} {sub(/\r$/,"")} {print}' "$src" > "$tmp"
    install -m 0755 "$tmp" "$dest" || {
        rm -f "$tmp"
        die "ファイル配置失敗: $dest"
    }
    rm -f "$tmp"
}

# ============================================================================
# Systemd 管理
# ============================================================================

enable_service() {
    local service="$1"
    log_info "サービスを有効化＆開始: $service"
    systemctl daemon-reload
    systemctl enable "$service" >> "$LOG_FILE" 2>&1 || die "systemctl enable 失敗: $service"
    systemctl start "$service" >> "$LOG_FILE" 2>&1 || die "systemctl start 失敗: $service"
}

check_service() {
    local service="$1"
    if systemctl is-active --quiet "$service"; then
        log_info "サービスは起動中: $service"
        return 0
    else
        log_warn "サービスが起動していません: $service"
        return 1
    fi
}

# ============================================================================
# ネットワーク設定適用
# ============================================================================

setup_usb0() {
    log_info "usb0 を UP＆設定"
    
    if ! check_interface usb0; then
        die "インターフェース usb0 が見つかりません"
    fi
    
    ip link set usb0 up >> "$LOG_FILE" 2>&1 || die "usb0 UP 失敗"
    ip addr flush dev usb0 >> "$LOG_FILE" 2>&1 || true
    ip addr add 10.55.0.10/24 dev usb0 >> "$LOG_FILE" 2>&1 || die "usb0 アドレス設定失敗"
    
    log_info "usb0 設定完了: 10.55.0.10/24"
}

# ============================================================================
# ネットワーク変更検出
# ============================================================================

detect_network_change() {
    local wlan0_ip_before=$(get_state "wlan0_ip_before")
    local wlan0_ip_after=$(get_interface_ip wlan0 || echo "unknown")
    
    if [[ "$wlan0_ip_before" != "unknown" ]] && [[ "$wlan0_ip_after" != "unknown" ]] && \
       [[ "$wlan0_ip_before" != "$wlan0_ip_after" ]]; then
        log_warn "ネットワーク構成が変わりました"
        log_warn "  Before: $wlan0_ip_before"
        log_warn "  After:  $wlan0_ip_after"
        return 0  # Network changed
    fi
    
    return 1  # No change
}

prompt_reboot() {
    cat <<EOF

╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║  ⚠️  ネットワーク構成が変更されました！                       ║
║                                                                ║
║  デバイスがUSBガジェット (usb0, 10.55.0.10) に移行しました。  ║
║  接続されているラップトップのネットワークが自動再設定され、   ║
║  その後に接続が一時的に失われます。                          ║
║                                                                ║
║  【対応】次の手順を実行してください：                        ║
║                                                                ║
║  1) 物理的に再起動（以下を実行）：                           ║
║     sudo reboot                                              ║
║                                                                ║
║  2) 再起動完了を待つ（1-2分）                                ║
║                                                                ║
║  3) インストーラを再実行（再開モード）：                     ║
║     sudo ./install.sh --resume                               ║
║                                                                ║
║  詳細は以下を参照：                                          ║
║  https://github.com/01rabbit/Azazel-Gadget/tree/main/installer  ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝

EOF
}

# ============================================================================
# ドライラン / バックアップ
# ============================================================================

is_dry_run() {
    [[ "${DRY_RUN:-0}" == "1" ]]
}

run_cmd() {
    local cmd="$*"
    
    if is_dry_run; then
        echo "DRY-RUN: $cmd"
        return 0
    fi
    
    log_debug "実行: $cmd"
    eval "$cmd" >> "$LOG_FILE" 2>&1 || {
        die "コマンド実行失敗: $cmd"
    }
}

# ============================================================================
# 検証
# ============================================================================

validate_stage() {
    local stage_name="$1"
    shift
    
    log_info "Stage $stage_name を検証中..."
    
    local all_passed=true
    while [[ $# -gt 0 ]]; do
        local check="$1"
        shift
        
        if eval "$check" >/dev/null 2>&1; then
            log_info "✓ $check"
        else
            log_error "✗ $check"
            all_passed=false
        fi
    done
    
    if [[ "$all_passed" == "true" ]]; then
        log_info "$stage_name 検証成功"
        return 0
    else
        return 1
    fi
}

# ============================================================================
# 初期化
# ============================================================================

init_installer() {
    mkdir -p "$LOGS_DIR"
    ensure_root
    check_os
    
    echo "═══════════════════════════════════════════════════════════"
    echo "Azazel-Gadget Unified Installer"
    echo "Start: $(date)"
    echo "Log: $LOG_FILE"
    echo "═══════════════════════════════════════════════════════════"
    echo ""
}

echo "✓ Installer library loaded: ${BASH_SOURCE[0]}"
