# Azazel-Zero セットアップガイド（Bullseye 32bit）

[English](/docs/setup-zero.md) | [日本語](/docs/setup-zero_ja.md)

## 目的

このガイドでは、Raspberry Pi Zero 2 W に Raspberry Pi OS Lite (Bullseye 32bit) をインストールし、USBガジェットモードを有効化して USB 経由でネットワーク接続を実現する手順を説明します。これにより、ラップトップなどのホストPCから USB ケーブル経由で Pi にアクセスし、インターネット共有が可能になります。

---

## 基本セットアップ手順

### 1. SDカードの準備

Raspberry Pi Imager を使って以下の設定で OS を書き込みます。

- OS: Raspberry Pi OS Lite (Bullseye)
- 書き込みオプション（歯車マーク）で設定：
  - ホスト名: `azazel-zero`
  - SSH 有効化し、任意のユーザー名とパスワードを設定
  - Wi-Fi 設定（SSID、パスワード、国コード JP）
  - ロケール設定（タイムゾーン、キーボードレイアウト JP）

書き込み完了後、SDカードを Raspberry Pi Zero 2 W に挿入してください。

### 2. USBガジェットモードの有効化

1. `boot/config.txt` の末尾に以下を追記します。

    ```txt
    dtoverlay=dwc2
    ```

2. `boot/cmdline.txt` の末尾に、改行せずに次の内容を追加します。

    ```txt
    modules-load=dwc2,g_ether
    ```

### 3. USB経由のネットワーク設定

1. Pi を起動しログインしたら、`usb0` インターフェースに固定IPを割り当てます。

    ```bash
    sudo tee -a /etc/dhcpcd.conf >/dev/null <<'EOF'
    interface usb0
    static ip_address=10.0.0.1/24
    EOF
    sudo systemctl restart dhcpcd
    ```

2. DHCP サーバーとして `dnsmasq` をインストールし、設定します。

    ```bash
    sudo apt update
    sudo apt install -y dnsmasq
    sudo tee /etc/dnsmasq.d/usb0.conf >/dev/null <<'EOF'
    interface=usb0
    dhcp-range=10.0.0.2,10.0.0.10,255.255.255.0,24h
    bind-interfaces
    EOF
    sudo systemctl restart dnsmasq
    ```

3. NAT（ネットワークアドレス変換）とパケットフォワードを有効化します。

    ```bash
    echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-azazel.conf
    sudo sysctl --system
    sudo iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
    sudo apt install -y iptables-persistent
    ```

### 4. 動作確認

1. Pi が Wi-Fi に正常に接続されているか確認します。

    ```bash
    iwgetid -r
    ip -4 addr show wlan0
    ping -c2 8.8.8.8 -I wlan0
    ```

2. ラップトップを USB ケーブルで接続すると、`10.0.0.x` の IP アドレスが割り当てられます。

3. ラップトップから Pi への疎通を確認します。

    ```bash
    ping 10.0.0.1       # Pi への接続確認
    ping 8.8.8.8        # Pi 経由でインターネット接続確認
    ```

---

## 拡張: USBガジェットモードによるネットワーク接続

USBガジェットモードを有効化することで、Raspberry Pi Zero 2 W を USB ネットワークデバイスとして動作させることができます。これにより、USB ケーブルを介してホストPCと Pi 間でネットワーク通信が可能になります。

具体的には、`dtoverlay=dwc2` と `modules-load=dwc2,g_ether` の設定を行い、Pi 側で `usb0` インターフェースに固定IPを割り当て、`dnsmasq` を用いて DHCP サーバーを動作させます。さらに、NAT とパケットフォワード設定により、Pi の Wi-Fi 接続を USB 経由で共有できるようになります。

この方法は、Wi-Fi 環境が不安定な場合や、USB ケーブル一本で接続を完結させたい場合に非常に有効です。

---

## まとめ

- Raspberry Pi Imager で事前に Wi-Fi と SSH を設定することで、初回起動からスムーズに接続可能です。
- `dwc2` と `g_ether` モジュールにより、USB ケーブルを使ったネットワーク接続が実現できます。
- `dnsmasq` と `iptables` の設定で、Pi が USB 接続経由の Wi-Fiアダプタとして機能します。
