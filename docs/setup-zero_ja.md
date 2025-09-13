# Azazel-Zero セットアップガイド（Bullseye 32bit）

[English](/docs/setup-zero.md) | [日本語](/docs/setup-zero_ja.md)

## 目的

このガイドでは、Raspberry Pi Zero 2 W に Raspberry Pi OS Lite (Bullseye 32bit) をインストールし、USBガジェットモードを有効化して USB 経由でネットワーク接続を実現する手順を説明します。これにより、ラップトップなどのホストPCから USB ケーブル経由で Pi にアクセスし、インターネット共有が可能になります。

---

## 基本セットアップ手順

### クイックスタート

再現性のある構築を行う場合は、自動セットアップスクリプトを利用できます。

```bash
sudo chmod +x tools/bootstrap_zero.sh
sudo tools/bootstrap_zero.sh
```

オプション:

- `--no-epd` : E-Paper 関連を省略
- `--no-enable` : systemd サービスの有効化を行わない
- `--no-suricata` : Suricata の軽量ルール設定を省略
- `--dry-run` : 実行される手順を表示するのみ

### 1. OTG 設定

`/boot/config.txt` に追記:

```txt
dtoverlay=dwc2
```

`/boot/cmdline.txt` の `rootwait` の直後に:

```txt
modules-load=dwc2,g_ether
```

また、Windows/Linux 両対応を安定させるために `/etc/modprobe.d/g_ether.conf` を作成し、以下を記載します:

```conf
options g_ether use_eem=0
```

---

### 2. ネットワーク設定

`/etc/network/interfaces.d/usb0` を作成:

```ini
auto usb0
iface usb0 inet static
  address 192.168.7.2
  netmask 255.255.255.0
```

---

### 3. DHCP サーバ設定

`dnsmasq` を導入:

```bash
sudo apt update && sudo apt install -y dnsmasq
```

設定ファイル `/etc/dnsmasq.d/usb-gadget.conf`:

```ini
interface=usb0
bind-interfaces
dhcp-authoritative
dhcp-broadcast
dhcp-range=192.168.7.10,192.168.7.20,255.255.255.0,12h
dhcp-option=3,192.168.7.2
dhcp-option=6,1.1.1.1,8.8.8.8
```

---

### 4. NAT 設定

`/etc/sysctl.conf` に追記:

```conf
net.ipv4.ip_forward=1
```

即時反映:

```bash
sudo sysctl -w net.ipv4.ip_forward=1
```

iptables 設定:

```bash
sudo iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
sudo iptables -A FORWARD -i wlan0 -o usb0 -m state --state RELATED,ESTABLISHED -j ACCEPT
sudo iptables -A FORWARD -i usb0 -o wlan0 -j ACCEPT
```

保存:

```bash
sudo sh -c "iptables-save > /etc/iptables.ipv4.nat"
```

---

### 5. Mac / Windows 側での注意点

- **macOS**  
  - インターネット共有 (bridge100) を必ずオフにする  
  - Wi-Fi を切る  
  - 必要なら IPv6 を無効化:  
  
    ```bash
    networksetup -setv6off "RNDIS/Ethernet Gadget"
    ```

- **Windows**: 自動で「RNDIS Gadget」として認識し、DHCPでIPを取得  
- **Linux**: ECM として NIC が出現  

---

### 6. 動作確認

Pi 側で:

```bash
sudo ip link set usb0 up
```

Mac / PC 側で:

```bash
ping 192.168.7.2
ping 8.8.8.8
curl -I https://example.com
```

---

### 7. クロスプラットフォーム利用の補足

本構成は基本的に macOS / Windows / Linux すべてに対応していますが、環境によって挙動が異なる場合があります。

- **macOS**  
  - IPv6 をオフにしないと自己割り当て IP になる場合があります。  
  - インターネット共有 (bridge100) を無効化してください。  

- **Windows**  
  - RNDIS ドライバで自動認識されます。  
  - DHCP 取得に失敗した場合は、アダプタを一度「無効化 → 有効化」してください。  

- **Linux**  
  - ECM デバイスとして認識されます。  
  - `dmesg | grep usb` で確認可能です。  

---

### 付録: 一発セットアップスクリプト（上級者向け）

```bash
sudo apt update && sudo apt install -y dnsmasq iptables-persistent && \
echo "dtoverlay=dwc2" | sudo tee -a /boot/config.txt && \
sudo sed -i 's/rootwait/rootwait modules-load=dwc2,g_ether/' /boot/cmdline.txt && \
sudo tee /etc/network/interfaces.d/usb0 >/dev/null <<'EOF'
auto usb0
iface usb0 inet static
  address 192.168.7.2
  netmask 255.255.255.0
EOF
sudo tee /etc/dnsmasq.d/usb-gadget.conf >/dev/null <<'EOF'
interface=usb0
bind-interfaces
dhcp-authoritative
dhcp-broadcast
dhcp-range=192.168.7.10,192.168.7.20,255.255.255.0,12h
dhcp-option=3,192.168.7.2
dhcp-option=6,1.1.1.1,8.8.8.8
EOF
echo "net.ipv4.ip_forward=1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -w net.ipv4.ip_forward=1
sudo iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
sudo iptables -A FORWARD -i wlan0 -o usb0 -m state --state RELATED,ESTABLISHED -j ACCEPT
sudo iptables -A FORWARD -i usb0 -o wlan0 -j ACCEPT
sudo sh -c "iptables-save > /etc/iptables.ipv4.nat"
```
