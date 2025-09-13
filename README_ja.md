# Azazel-Zero

[English](/README.md) | [日本語](/README_ja.md)

## コンセプト

**Azazel-Zero** は、Raspberry Pi Zero 2 W 上で動作する **「身代わり防壁」** のプロトタイプです。

このシステムは、**Azazel System の「遅延防御」コンセプトを現実的に実装する** ことを目的としています。  
同時に、Azazel System の原点である **「身代わり防壁」** および **「防壁迷路」** というアイデアに立ち返っています。

### Azazel-Pi との比較

- **Azazel-Pi**  
  - Raspberry Pi 5 をベースにした **ポータブル・セキュリティゲートウェイ（サイバースケープゴートゲートウェイ）**  
  - **一時的に構築する小規模ネットワーク** の低コスト防御を提供する **コンセプトモデル**  
  - 技術要素の検証用として実験的な側面が強い

- **Azazel-Zero**  
  - **用途を絞り不要な機能を削減した、実運用向けの軽量バージョン**  
  - **携帯性と実用性を重視した物理的バリア** として構築  
  - Azazel-Pi のコンセプトモデルとは異なり、**現場運用を想定した実用モデル**

---

## 設計方針

- **携帯性**：胸ポケットに収まる小型サイズ  
- **不可避性**：機器と外部ネットワークの間に強制的に割り込む  
- **シンプルさ**：USBを挿すだけでファイアウォールが成立  
- **遅延防御**：攻撃者の時間を浪費させる（Azazel System の中核コンセプト）

---

## 実装

### ベース

- **Raspberry Pi Zero 2 W**

### ネットワーク

- **USB OTG ガジェットモード**  
  - 1本のUSBケーブルで給電と仮想ネットワークを同時に提供  
  - ノートPC等に挿すだけですぐ起動

### 防御機能（軽量版）

- **iptables/nftables** によるブロック・遅延  
- **tc (Traffic Control)** でネットワーク遅延やジッターを挿入  
- **カスタムPythonスクリプト**による動的制御・通知

### ステータス表示

- **E-Paper（電子ペーパー）**  
  - 2.13インチ モノクロ（250×122）ディスプレイを使用  
  - 脅威レベル・アクション・RTT・キュー状態・キャプティブポータル検知などを簡潔にUI表示

---

## AI要素（研究テーマ）

Azazel-Zero は軽量ファイアウォールとして設計されており、AIは必須ではありません。  
しかし、現在の技術動向や可能性を踏まえ、**研究テーマとして検討対象**としています。

- **制約**  
  - Zero 2 W はCPU・RAMに制限があり、大規模なAIは現実的でない  
  - GPUアクセラレーションなし

- **可能性**  
  - scikit-learn等による軽量な機械学習モデル（例：異常検知 Isolation Forest, one-class SVM など）  
  - TensorFlow Lite 等による小規模な推論（例：正常/攻撃トラフィック分類）

- **位置付け**  
  - 現時点では未実装  
  - 今後 **「学習型シールド」** への拡張余地あり

---

## セットアップ手順（概要）

※ 詳細なセットアップ手順は [docs/setup-zero.md](docs/setup-zero.md) を参照してください。

1. **Raspberry Pi OS Lite (64bit)** をインストール  
2. **USBガジェットモード** を設定  
   - `/boot/config.txt` に `dtoverlay=dwc2` を追記  
   - `/boot/cmdline.txt` に `modules-load=dwc2,g_ether` を追記  
3. **E-Paper制御ライブラリ**（例：Waveshare Pythonライブラリ）をインストール  
4. 脅威レベルや遅延状況を表示する **UIスクリプト** を作成  
5. **systemdサービス**としてシールド・UIが起動時に自動起動するよう設定

## 起動時E-Paperスプラッシュ（Azazel-Zero、リポジトリ: ~/Azazel-Zero）

※ 詳細なセットアップ手順は [Boot-E-Paper_Splash](/docs/Boot_E-Paper_Splash_ja.md)を参照してください。

起動時にWaveshare製E-Paperへ **SSID** および **IPv4** を表示します。  
スクリプト: `py/boot_splash_epd.py`

**セットアップ手順**  

1) 依存関係はスクリプトで一括導入可能:  
   `sudo bash bin/install_dependencies.sh --with-epd`
2) テスト: `sudo python3 ~/Azazel-Zero/py/boot_splash_epd.py`  
3) サービス `azazel-epd.service` を有効化（環境変数 `/etc/default/azazel-zero` でパスを管理）。

お使いのパネルドライバが `epd2in13_V4` でない場合は、インポート行を `V3` または `V2` へ変更してください。
