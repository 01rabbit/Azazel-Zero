# Azazel-Zero セットアップガイド（Raspberry Pi OS Lite Trixie / 64bit）

[English](/docs/setup-zero.md) | 日本語

## 目的と想定構成

このガイドでは、Raspberry Pi Zero 2 W に Raspberry Pi OS Lite（Trixie 64bit）をインストールし、

- **方式B**: `dwc2 + g_ether` で USB ガジェット（OTG）を有効化
- `usb0` を **10.55.0.1/24** で固定
- `wlan0` を上流 Wi-Fi としてインターネット接続
- `usb0 → wlan0` のルーティング / NAT（iptables）を有効化

することで、

> 上流インターネット（Wi-Fi） → ラズパイ（Azazel-Zero） → ラップトップ（USB 経由）

という経路を構築する手順を示します。

ラップトップ側（USB NIC の IP / デフォルトゲートウェイ / DNS）の設定は、利用環境に応じてユーザーが行う前提とします。

---

## 0. 前提

- ハードウェア
  - Raspberry Pi Zero 2 W
  - USB データ対応ケーブル + Zero 用 USB ガジェットアダプタ
- OS
  - Raspberry Pi OS Lite (64-bit, Trixie)
- ホスト環境の例
  - macOS（他 OS でも同様の構成が可能）

---

## 1. Raspberry Pi OS Lite を SD カードに書き込む

1. ラップトップで Raspberry Pi Imager を起動します。
2. OS として **「Raspberry Pi OS Lite (64-bit)」** を選択します。
3. ターゲットの SD カードを選択して書き込みを実行します。
4. 書き込み完了後、SD カードを一度抜き差しし、ホスト側で **boot パーティション**（`bootfs` / `boot` など）をマウントします。

以下、このパーティションを `BOOT` と呼びます。

---

## 2. BOOT パーティション上で行う設定（方式B）

### 2-1. `userconf.txt` の作成（ユーザー `pi` / パスワード `raspberry`）

初回起動時の対話式セットアップをスキップするため、`userconf.txt` を作成します。

```bash
cd /Volumes/bootfs   # 環境により /Volumes/boot の場合あり
HASH="$(echo 'raspberry' | openssl passwd -6 -stdin)"
printf "pi:%s\n" "$HASH" > userconf.txt
```

### 2-2. SSH 有効化（`ssh` ファイル）

```bash
touch ssh
```

これにより、初回起動時から SSH が有効になります。

### 2-3. OTG 有効化（`dwc2 + g_ether`）

#### `config.txt`

`BOOT/config.txt` を開き、末尾に以下を追記します。

```ini
dtoverlay=dwc2
```

#### `cmdline.txt`

`BOOT/cmdline.txt` は **1 行だけ**のファイルです。改行を追加せず、`rootwait` の直後に以下を追記します。

```text
... rootwait modules-load=dwc2,g_ether ...
```

> 例：`rootwait` のあとに半角スペースを入れて `modules-load=dwc2,g_ether` を書き足すイメージです。

### 2-4. `user-data` による `usb0` 固定 IP 設定（cloud-init + systemd）

`BOOT/user-data` が存在する場合は **内容をすべて削除してから**、以下で上書きします。存在しない場合は新規作成します。

```bash
cat > /Volumes/bootfs/user-data <<'EOF'
#cloud-config
hostname: raspberrypi
manage_etc_hosts: true
enable_ssh: true

write_files:
  - path: /usr/local/sbin/usb0-static.sh
    permissions: '0755'
    content: |
      #!/bin/sh
      set -eu
      # usb0 が出てくるまで少し待つ
      for i in $(seq 1 50); do
        ip link show usb0 >/dev/null 2>&1 && break
        sleep 0.2
      done
      ip link set usb0 up || true
      ip addr flush dev usb0 || true
      ip addr add 10.55.0.1/24 dev usb0 || true

  - path: /etc/systemd/system/usb0-static.service
    permissions: '0644'
    content: |
      [Unit]
      Description=Force static IPv4 on usb0 (USB Gadget)
      After=local-fs.target
      Before=network.target

      [Service]
      Type=oneshot
      ExecStart=/usr/local/sbin/usb0-static.sh
      RemainAfterExit=yes

      [Install]
      WantedBy=multi-user.target

runcmd:
  - [ sh, -lc, 'systemctl daemon-reload' ]
  - [ sh, -lc, 'systemctl enable --now usb0-static.service || true' ]
EOF
```

これにより、Pi 起動時に `usb0-static.service` が実行され、**毎回 `usb0=10.55.0.1/24` が設定されます**。NetworkManager などによる自動設定の影響を受けません。

### 2-5. （任意）g_ether 互換性設定

Windows / Linux への互換性を高めたい場合は、Pi 起動後に `/etc/modprobe.d/g_ether.conf` を作成し、以下を記述します。

```conf
options g_ether use_eem=0
```

これは後からでも設定可能なため、ここでは説明のみとします。

### 2-6. SD カードのアンマウント

```bash
sync
diskutil eject /Volumes/bootfs 2>/dev/null || diskutil eject /Volumes/boot
```

---

## 3. 初回起動と USB 経由でのログイン（ラップトップ側設定）

1. SD カードを Raspberry Pi Zero 2 W に挿入します。
2. Zero 2 W の **USB データポート（USB と印字された側）**とラップトップを USB ガジェットアダプタ経由で接続します。
3. 30〜60 秒ほど待ち、Pi が起動するのを待ちます。

ラップトップ（macOS）の例：

1. USB NIC のインターフェース名を確認します。

   ```bash
   ifconfig | egrep '^(en[0-9]+:|status:|inet )'
   ```

   `RNDIS/Ethernet Gadget` や `Raspberry Pi USB` に相当する IF（例: `en17`）を特定します。

2. USB NIC に IP を設定します（例: `en17`）。

   ```bash
   IF=en17  # 実際の IF 名に置き換え
   sudo ifconfig "$IF" inet 10.55.0.2 netmask 255.255.255.0 up
   ```

3. Pi への疎通・ログインを確認します。

   ```bash
   ping -c 2 10.55.0.1
   ssh pi@10.55.0.1   # パスワード: raspberry
   ```

ここまでで、

- Pi 側: `usb0 = 10.55.0.1/24`
- ラップトップ: `USB NIC = 10.55.0.2/24`

となり、USB 経由で SSH ログインできる状態になります。

---

## 4. ラズパイ側での Wi-Fi 有効化と接続

以降は、Pi に SSH ログインした状態（`pi@10.55.0.1`）で作業します。

### 4-1. rfkill の確認と解除

```bash
rfkill list
sudo rfkill unblock wifi
sudo rfkill unblock all
rfkill list
```

`Wireless LAN` の `Soft blocked` / `Hard blocked` が `no` になっていることを確認します。

### 4-2. Wi-Fi ラジオ ON とインターフェース UP

Trixie では NetworkManager が標準で有効なため、`nmcli` を使用します。

```bash
sudo nmcli r wifi on
nmcli dev status

sudo ip link set wlan0 up
ip -br link | grep wlan0
```

`wlan0` が `UP` になっていれば OK です。

### 4-3. 周囲の AP をスキャン

```bash
sudo iw dev wlan0 scan | egrep 'SSID|freq|signal' | head -n 20
nmcli dev wifi list
```

ここで接続したい **2.4GHz 帯 SSID**（例: `JCOM_NYRY`）が見えていることを確認します。

### 4-4. Wi-Fi への接続とプロファイル永続化

```bash
sudo nmcli dev wifi connect "JCOM_NYRY" password "ここにパスワード" ifname wlan0
```

成功後、状態を確認します。

```bash
ip -4 a show wlan0
ip r
ping -c 3 8.8.8.8
ping -c 3 google.com
```

`wlan0` にローカル IP（例: `192.168.40.x`）が割り当てられ、インターネットへ到達できていれば成功です。

自動接続を有効化しておきます。

```bash
nmcli con show
sudo nmcli con mod "JCOM_NYRY" connection.autoconnect yes
```

これにより、再起動後も同じ SSID に自動接続されます。

---

## 5. ラズパイ側のルーティング / NAT 設定（上流 → Pi → 下流）

ここからは、Pi を **USB–Wi-Fi ルータ**として動作させるための設定を行います。

- 上流インターフェース: `wlan0`（Wi-Fi）
- 下流インターフェース: `usb0`（USB ガジェット / ラップトップ側）

ラップトップ側でのデフォルトゲートウェイ設定は、後述する前提条件に沿ってユーザーが行います。

### 5-1. IP フォワーディングを永続化

まず一時的に有効化し、動作を確認します。

```bash
sudo sysctl -w net.ipv4.ip_forward=1
```

その上で永続化設定を追加します。

```bash
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-ipforward.conf
sudo sysctl --system
```

再起動後も IPv4 フォワーディングが有効になります。

### 5-2. NAT / FORWARD ルールをスクリプト + systemd で永続化

#### 5-2-1. NAT スクリプトの作成

```bash
sudo tee /usr/local/sbin/azazel-nat.sh >/dev/null <<'EOF'
#!/bin/sh
set -eu

OUT_IF="wlan0"
IN_IF="usb0"

# NAT (POSTROUTING) - 重複しないようにチェックしてから追加
iptables -t nat -C POSTROUTING -o "$OUT_IF" -j MASQUERADE 2>/dev/null || \
iptables -t nat -A POSTROUTING -o "$OUT_IF" -j MASQUERADE

# FORWARD (usb0 -> wlan0 方向を許可)
iptables -C FORWARD -i "$IN_IF" -o "$OUT_IF" -j ACCEPT 2>/dev/null || \
iptables -A FORWARD -i "$IN_IF" -o "$OUT_IF" -j ACCEPT

# FORWARD (wlan0 -> usb0 方向の戻りパケットを許可)
iptables -C FORWARD -i "$OUT_IF" -o "$IN_IF" -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
iptables -A FORWARD -i "$OUT_IF" -o "$IN_IF" -m state --state ESTABLISHED,RELATED -j ACCEPT
EOF

sudo chmod 0755 /usr/local/sbin/azazel-nat.sh
```

#### 5-2-2. systemd ユニットの作成

```bash
sudo tee /etc/systemd/system/azazel-nat.service >/dev/null <<'EOF'
[Unit]
Description=Azazel NAT and forwarding rules (usb0 -> wlan0)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/azazel-nat.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now azazel-nat.service
```

状態確認：

```bash
sudo systemctl status azazel-nat.service --no-pager
sudo iptables -t nat -L -n -v
sudo iptables -L FORWARD -n -v
```

`POSTROUTING` に `MASQUERADE`、`FORWARD` に `usb0` ↔ `wlan0` のルールが追加されていれば OK です。

---

## 6. ラップトップ側の前提条件と動作確認

ラップトップ側の USB NIC に対して、以下のような設定を行うと、

- IP アドレス: `10.55.0.2/24`
- デフォルトゲートウェイ: `10.55.0.1`
- DNS: 自宅ルータの IP または `8.8.8.8` など

すべてのトラフィックが

> ラップトップ → usb0 (10.55.0.1) → wlan0 → 上流インターネット

という流れになります。

動作確認例：

1. ラップトップから Pi への疎通

   ```bash
   ping 10.55.0.1
   ```

2. ラップトップからインターネットへの疎通

   ```bash
   ping 8.8.8.8
   ping google.com
   ```

---

## 7. 今後 Azazel-Zero として発展させる際の位置づけ

本ガイドの構成は、Azazel-Zero を

- `usb0`: 下流クライアント（ユーザー端末）からの入口
- `wlan0`: 上流インターネットへの出口

として扱うための **最低限のルータ基盤**です。

この上に、

- Suricata によるトラフィック検知
- tc / iptables / nftables による遅滞行動（Delay-to-Win）
- OpenCanary などのデコイサービス
- Mock-LLM / LLM によるスコアリング

を積み上げることで、Azazel-Zero のフル機能ゲートウェイ構成へ発展させることができます。
