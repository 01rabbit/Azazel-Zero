# Azazel-Zero

[English](/README.md) | [日本語](/README_JA.md)

## コンセプト
**Azazel-Zero** は、Raspberry Pi Zero 2 W を基盤とした  
「身代わり防壁（Substitute Firewall）」の実装試みである。  

本システムは **Azazel System の遅滞行動（Delaying Action）を実用化**させることを目的に設計された。  
また、Azazel Systemの着想にあった **「身代わり防壁」や「防壁迷路」** の思想へ原点回帰している。  

### Azazel-Piとの対比
- **Azazel-Pi**  
  - Raspberry Pi 5 を用いた **可搬型セキュリティゲートウェイ（Cyber Scapegoat Gateway）**。  
  - コンセプトモデルとして、小規模かつ臨時的なネットワークを **低コストで保護**することを狙った設計。  
  - 実験的性格が強く、複数の要素技術を盛り込んだ試験台の役割を持つ。  

- **Azazel-Zero**  
  - 実運用を前提に、**余計な機能を削ぎ落とした軽量版**。  
  - 「持ち歩ける物理盾」として、携帯性と実用性を第一に構築。  
  - コンセプトモデルであるAzazel-Piに対し、**フィールドで即使用できる実用モデル**としての位置付けを持つ。  

---

## 設計思想
- **携帯性**：胸ポケットに収まるサイズと重量  
- **不可避性**：端末と外部ネットの間に強制的に介在  
- **単純操作**：USBを挿すだけで防壁が成立  
- **遅滞行動**：攻撃者に時間を浪費させる（Azazel Systemの共通思想）  

---

## 実装構成

### 基盤
- **Raspberry Pi Zero 2 W**

### ネットワーク
- **USB OTG Gadget モード**
  - 電力供給＋仮想ネットワークを1本のUSBケーブルで実現  
  - ノートPCからの給電で即稼働可能  

### 防御機能（軽量版）
- **iptables/nftables** による遮断・遅延制御  
- **tc (Traffic Control)** による通信遅延・パケット揺らぎ挿入  
- **カスタムPythonスクリプト**による動的制御と通知  

### ステータス表示
- **電子ペーパー (E-Ink)**  
  - 2.13インチ モノクロ (250×122) を採用  
  - UIは脅威レベル・アクション・RTT・Queue・キャプティブ検知などを簡潔表示  

---

## AI的要素（研究課題）
Azazel-Zeroは軽量防壁として設計されており、AIは必須ではない。  
ただし流行と技術的発展性を踏まえ、**研究課題としての可能性**は検討する価値がある。  

- **制約**
  - Zero 2 WはCPU・RAMが限定的、大規模AIは不可  
  - GPUによる加速も期待できない  

- **可能性**
  - scikit-learn等による軽量機械学習モデル（異常検知: Isolation Forest, one-class SVMなど）  
  - TensorFlow Liteによる小規模推論（通常通信 vs 攻撃通信の分類など）  

- **位置付け**
  - 現時点では未実装  
  - 将来的に「学習する盾」として拡張可能性を持つ  

---

## 構築手順（概要）

1. **Raspberry Pi OS Lite (64bit)** を導入  
2. **USB Gadget モード設定**  
   - `/boot/config.txt` に `dtoverlay=dwc2`  
   - `/boot/cmdline.txt` に `modules-load=dwc2,g_ether`  
3. **電子ペーパー制御ライブラリ**を導入（例: Waveshare Pythonライブラリ）  
4. **UIスクリプト**を作成し、脅威レベルや遅滞状況を表示  
5. **systemdサービス化**して起動時に防壁＋UIが稼働  
