# Azazel-Zero

[English](/README.md) | [日本語](/README_ja.md)

## コンセプト

**Azazel-Zero** は Raspberry Pi Zero 2 W 上で動作する **「身代わり防壁（Substitute Barrier）」** のプロトタイプです。  
Azazel System の**遅滞防御（delaying action）**を実用的に具現化しつつ、**身代わり防壁**と**防壁迷路（Barrier Maze）**という原点に立ち返ります。

### Azazel-Pi との比較

- **Azazel-Pi**  
  - Raspberry Pi 5 をベースにした **Portable Security Gateway (Cyber Scapegoat Gateway)**  
  - **一時的に構築する小規模ネットワーク** を低コストで守るための **コンセプトモデル**  
  - 複数の技術要素を試験するための実験色が強い

- **Azazel-Zero**  
  - **用途を絞り、不要機能をそぎ落とした軽量版**。実運用を前提に設計  
  - **携帯性と実用性を重視した物理的バリア**  
  - コンセプトモデルの Azazel-Pi と異なり、**現場投入を想定した実用モデル**

---

## 設計方針

- **携帯性**: 胸ポケットに収まるサイズ  
- **不可避性**: 端末と外部ネットワークの間に強制的に割り込む  
- **シンプルさ**: USB を挿すだけでファイアウォールが成立  
- **遅延防御**: 攻撃者の時間を浪費させる（Azazel System の中核）

---

## 実装

### ベース

- **Raspberry Pi Zero 2 W**

### ネットワーク

- **USB OTG ガジェットモード**  
  - 1 本の USB ケーブルで給電と仮想ネットワークを同時に提供  
  - ノート PC に挿すだけで即起動

### 防御機能（軽量）

- **iptables/nftables** によるブロック・遅延  
- **tc (Traffic Control)** で遅延・ジッターを注入  
- **カスタム Python スクリプト** による動的制御と通知  
- **Wi-Fi セーフティセンサー**（Python + `iw` + `tcpdump`）で Evil AP / MITM / DNS・DHCP スプーフィングを検出し、危険タグを発行して自動切断へ接続

### ステータス表示

- **E-Paper（電子ペーパー）**  
  - 2.13 インチ モノクロ（250×122）  
  - 脅威レベル/アクション/RTT/キュー状態/キャプティブポータル検出を簡潔に表示

---

## Threat Evaluation Pipeline

Pi Zero 2 W でも動作する決定論的な 2 層構成の判定エンジンを採用しています。

- **第1層: Wi-Fi セーフティセンサー**  
  - `py/azazel_zero/sensors/wifi_safety.py` が `iw dev … link` と短時間の `tcpdump` から ARP/DHCP/DNS の異常を検出。  
  - `evil_ap`, `mitm`, `arp_spoof`, `dhcp_spoof`, `dns_spoof`, `tls_downgrade`, `captive_portal`, `phish` などのタグとメタ情報を生成。

- **第2層: Mock-LLM Core**  
  - `py/azazel_zero/core/mock_llm_core.py` が入力を従来カテゴリ（`scan`, `bruteforce`, `exploit`, `malware`, `sqli`, `dos`, `unknown`）へマッピング。  
  - 正規表現 + ランダムから、ハッシュに基づく決定論リプライへ刷新し、リスク（1–5）と理由文を安定出力。  
  - プロファイル `"zero"` は Wi-Fi タグに Evil AP / MITM があれば自動でリスクを引き上げ、Danger/Disconnect 判定を確実化。

- **Threat Judge ラッパー**  
  - `py/azazel_zero/app/threat_judge.py` がタグと最終判定をまとめ、UI や自動化が扱いやすい JSON を返却（例: `risk >= 4` または `evil_ap` タグで即切断）。

重量級 ML は将来の研究テーマとして残しつつ、現状の決定論的スタックだけで携帯シールドに必要な自動判定を実現しています。

---

## Operator Console & Automation

- **tmux コンソール**  
  - `py/azazel_menu.py` は Wi-Fi セレクタ、Portal/Shield/Lockdown スクリプト、OpenCanary 制御、E-Paper テストをまとめた curses メニュー。  
  - `py/azazel_status.py` は SSID/BSSID、USB ガジェット IP、RSSI、キャプティブポータル指標などを表示するテレメトリパネル。
- **ブートストラップツール**  
  - `tools/bootstrap_zero.sh` が依存パッケージ、systemd ユニット、Suricata の最小ルール、スモークテストを一括実行。  
  - `--no-epd`, `--no-enable`, `--no-suricata`, `--dry-run` でラボ向け/本番向けに柔軟対応。

---

## セットアップ手順（概要）

※ 詳細は [docs/setup-zero.md](docs/setup-zero.md) を参照してください。

### クイックスタート

再現性の高い構築を行う場合は自動セットアップスクリプトが利用できます。

```bash
sudo chmod +x tools/bootstrap_zero.sh
sudo tools/bootstrap_zero.sh
```

オプション:

- `--no-epd` : E-Paper 関連の依存をスキップ  
- `--no-enable` : systemd サービスの有効化を行わない  
- `--no-suricata` : Suricata の軽量ルール設定をスキップ  
- `--dry-run` : 実行内容のみを表示

1. **Raspberry Pi OS Lite (64bit)** をインストール  
2. **USB ガジェットモード** を設定  
   - `/boot/config.txt` に `dtoverlay=dwc2` を追記  
   - `/boot/cmdline.txt` に `modules-load=dwc2,g_ether` を追記  
3. **E-Paper 制御ライブラリ**（例: Waveshare Python）を導入  
4. 脅威レベルや遅延状況を表示する **UI スクリプト** を設置  
5. **systemd サービス**としてシールド/UI を自動起動

## 起動時 E-Paper スプラッシュ（~/Azazel-Zero）

※ 詳細手順は [Boot_E-Paper_Splash_ja.md](/docs/Boot_E-Paper_Splash_ja.md) を参照してください。

起動時に Waveshare 製 E-Paper へ **SSID** と **IPv4** を表示します。  
スクリプト: `py/boot_splash_epd.py`

**セットアップ**

1. 依存関係をまとめて導入: `sudo bash bin/install_dependencies.sh --with-epd`  
2. テスト: `sudo python3 ~/Azazel-Zero/py/boot_splash_epd.py`  
3. サービス `azazel-epd.service` を有効化（パスは `/etc/default/azazel-zero` で管理）

パネルドライバが `epd2in13_V4` でない場合は `V3` もしくは `V2` に変更してください。

### Waveshare 機能ライブラリ導入（Raspberry Pi Zero 2 W）

`bin/install_waveshare_epd.sh` は公式手順を自動化したスクリプトです。以下を実行すれば Waveshare デモがすぐ動作します。

```bash
sudo bash bin/install_waveshare_epd.sh
```

スクリプト内容（手動実行も可）:

```bash
# 依存パッケージ
sudo apt-get update
sudo apt-get install python3-pip
sudo apt-get install python3-pil
sudo apt-get install python3-numpy
sudo python3 -m pip install spidev

# gpiozero（未導入の場合のみ）
sudo apt-get update
sudo apt install python3-gpiozero
sudo apt install python-gpiozero

# Waveshare デモ取得
git clone https://github.com/waveshare/e-Paper.git
cd e-Paper/RaspberryPi_JetsonNano/
wget https://files.waveshare.com/upload/7/71/E-Paper_code.zip
unzip E-Paper_code.zip -d e-Paper
# 代替: 7zip を使用
sudo apt-get install p7zip-full
7z x E-Paper_code.zip -O./e-Paper

# デモ実行（2.13in mono V4）
cd e-Paper/RaspberryPi_JetsonNano/python/examples/
python3 epd_2in13b_V4_test.py
```

`install_waveshare_epd.sh` は `/opt/waveshare-epd` へライブラリを配置し、`E-Paper_code.zip` を取得します。`--run-demo` を付けると最後にデモ実行まで自動化します。
