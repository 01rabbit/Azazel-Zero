# Azazel-Gadget 統合インストーラ

**最小限の操作で完全インストール完了！**

> [!IMPORTANT]
> **開発ステータス（更新日: 2026-05-16）:** Azazel-Zero は開発終了です。  
> 本インストーラと機能は Azazel-Gadget に統合済みです。

## 🚀 クイックスタート

### 初回インストール

```bash
cd ~/azazel
sudo ./install.sh
```

### ネットワーク変更で再起動が促進された場合

```bash
sudo reboot
# 再起動後...
sudo ./install.sh --resume
```

### オプション機能を含める

```bash
sudo ./install.sh --with-webui --with-canary --with-ntfy --with-portal-viewer
```

---

## 📋 何が自動化されるか

✅ **Stage 00: Prerequisites Check**
- root 権限確認
- OS（Raspberry Pi OS）確認
- ディスク容量確認（最小 2GB）
- ネットワークインターフェース確認（wlan0, usb0）

✅ **Stage 10: Dependency Installation**
- APT パッケージ更新
- 基本パッケージ（nftables, dnsmasq, suricata など）
- Textual TUI 依存（`python3-textual`、提供環境のみ）
- Python venv（OpenCanary, Web UI）
- オプション機能

✅ **Stage 20: Network Configuration**
- usb0 を 10.55.0.10 で UP
- NAT ルール適用
- IP フォワーディング有効化
- **ネットワーク変更を検出**して再起動を促促

✅ **Stage 30: Configuration Deployment**
- /etc/azazel-gadget/ にテンプレート配置
- 環境ファイル作成
- 秘匿情報チェック

✅ **Stage 40: Systemd Service Registration**
- systemd ユニット配置
- サービス有効化＆起動
- 主要サービスのテスト

✅ **Stage 99: Validation & Completion**
- 全サービス起動確認
- ポート疎通確認（DHCP/DNS）
- ログ確認
- 完了メッセージ

---

## 🎛️ オプション

```bash
sudo ./install.sh [OPTIONS]
```

| オプション | 説明 |
|-----------|------|
| `--with-canary` | OpenCanary（ハニーポット） |
| `--with-epd` | Waveshare E-Paper（デフォルト有効） |
| `--with-webui` | Web UI ダッシュボード（HTTPS/Caddy 含む） |
| `--with-ntfy` | ntfy.sh push notification |
| `--with-portal-viewer` | noVNC ベースの Captive Portal Viewer（ポート 6080） |
| `--all` | すべて有効化 |
| `--dry-run` | プレビューのみ（変更なし） |
| `--resume` | 再起動後の再開 |
| `--debug` | デバッグログ出力 |

### 例
```bash
# すべて有効
sudo ./install.sh --all

# WebUI + OpenCanary のみ
sudo ./install.sh --with-webui --with-canary

# プレビュー
sudo ./install.sh --dry-run
```

---

## ⚡ ネットワーク再起動処理

インストール中にネットワーク構成が変わる場合があります（wlan0 の IP 割り当て変更など）。
この場合、インストーラから以下のプロンプトが表示されます：

```
╔════════════════════════════════════════════════════════════════╗
║                                                                ║
║  ⚠️  ネットワーク構成が変更されました！                       ║
║                                                                ║
║  1) 物理的に再起動：                                         ║
║     sudo reboot                                              ║
║                                                                ║
║  2) 再起動完了を待つ（1-2分）                                ║
║                                                                ║
║  3) インストーラを再実行：                                   ║
║     sudo ./install.sh --resume                               ║
║                                                                ║
╚════════════════════════════════════════════════════════════════╝
```

**対応**:
```bash
# 1. 再起動
sudo reboot

# 2. 再起動完了後、再開
sudo ./install.sh --resume
```

---

## 🏗️ ディレクトリ構造

```
installer/
├── README.md                # このファイル
├── _lib.sh                  # 共通ライブラリ（内部用）
├── defaults/                # 設定テンプレート
│   ├── first_minute.yaml
│   ├── dnsmasq-first_minute.conf
│   ├── opencanary.conf
│   ├── known_wifi.json
│   └── iptables-rules.v4
├── stages/                  # インストールステージ
│   ├── 00_precheck.sh       # 前提条件チェック
│   ├── 10_dependencies.sh   # パッケージインストール
│   ├── 20_network.sh        # ネットワーク設定
│   ├── 30_config.sh         # 設定ファイル配置
│   ├── 40_services.sh       # systemd 登録
│   └── 99_validate.sh       # 検証＆完了
├── logs/                    # インストールログ
│   └── install_YYYYMMDD-HHMMSS.log
├── snapshot/                # （非推奨：マイグレーション用）
│   └── [古いスナップショット]
└── profiles/                # （廃止予定：プロファイルシステム）
    └── [古いプロファイル]
```

---

## 🔧 トラブルシューティング

### インストール失敗時

**ログ確認**:
```bash
ls -la installer/logs/
tail -100 installer/logs/install_YYYYMMDD-HHMMSS.log
```

### DHCP/DNS 問題

**診断ツール実行**:
```bash
sudo bash bin/diagnose_dhcp.sh
```

**詳細**: [開発アーカイブ: DHCP_DNS_TROUBLESHOOTING.md](../docs/dev-archive/DHCP_DNS_TROUBLESHOOTING.md)

### サービスが起動しない

```bash
# ステータス確認
sudo systemctl status azazel-first-minute.service

# ログ確認
sudo journalctl -u azazel-first-minute.service -n 50

# 再起動
sudo systemctl restart azazel-first-minute.service
```

---

## ✅ インストール後の確認

### サービス確認
```bash
sudo systemctl status usb0-static.service
sudo systemctl status azazel-first-minute.service
sudo systemctl status azazel-web.service
sudo systemctl status caddy.service   # --with-webui の場合
```

### ネットワーク確認
```bash
# DHCP ポートがリッスン中か
sudo ss -ultn | grep 67

# DNS ポートがリッスン中か
sudo ss -ultn | grep 53

# dnsmasq ログ
tail -20 /var/log/azazel-dnsmasq.log
```

### ラップトップからのテスト（Linux）
```bash
# usb0 に接続した場合
dhclient usb0

# IP 確認
ip addr show usb0

# DNS テスト
nslookup example.com 10.55.0.10
```

---

## 📝 設定カスタマイズ

インストール後：

```bash
# メイン設定
sudo nano /etc/azazel-gadget/first_minute.yaml

# DHCP/DNS 設定
sudo nano /etc/azazel-gadget/dnsmasq-first_minute.conf

# 修正後
sudo systemctl restart azazel-first-minute.service
```

---

## 📚 詳細ドキュメント

- [開発アーカイブ: DHCP_DNS_TROUBLESHOOTING.md](../docs/dev-archive/DHCP_DNS_TROUBLESHOOTING.md) - ネットワーク設定のトラブル
- [SYSTEM_SPECIFICATION.md](../SYSTEM_SPECIFICATION.md) - システム全体仕様
- [開発アーカイブ: INSTALLER_UNIFIED_DESIGN.md](../docs/dev-archive/INSTALLER_UNIFIED_DESIGN.md) - インストーラ設計（開発資料）

---

## 非推奨ツール（互換性のため保持）

以下は古いプロファイルシステムです。**新規ユーザーは使用しないでください**：

- `collect_snapshot.sh` - （非推奨）機構成採取
- `generate_profile.py` - （廃止予定）プロファイル生成
- `apply.sh` - （廃止予定）プロファイル適用

---

## 📞 サポート

問題が発生した場合：

1. ログを確認
   ```bash
   tail -100 installer/logs/install_YYYYMMDD-HHMMSS.log
   ```

2. 診断ツール実行
   ```bash
   sudo bash bin/diagnose_dhcp.sh
   ```

3. GitHub Issues で報告
   https://github.com/01rabbit/Azazel-Gadget/issues
