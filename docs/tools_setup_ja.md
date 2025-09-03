# Azazel‑Zero: 必要ツールのインストールと初期設定

---

## 基本パッケージのインストール

以下のコマンドで基本的なツールと依存関係をインストールします。

```bash
sudo apt update
sudo apt install -y build-essential libpcap-dev libpcre3-dev libyaml-dev libmagic-dev libnet1-dev libgeoip-dev python3 python3-pip python3-venv git curl
```

---

## Suricata のインストールと設定

### Suricataのインストール

Suricataを公式リポジトリからインストールします。

```bash
sudo add-apt-repository ppa:oisf/suricata-stable
sudo apt update
sudo apt install -y suricata
```

### インターフェース設定 (af-packet)

`/etc/suricata/suricata.yaml` の `af-packet` セクションを以下のように設定してください。  
監視したいインターフェースとして `wlan0` と `usb0` を追加し、eve-log出力を有効化しています。  
※ 必要に応じて該当セクションのみを置き換えてください。

```yaml
af-packet:
  - interface: wlan0
    cluster-id: 99
    cluster-type: cluster_flow
    defrag: yes
  - interface: usb0
    cluster-id: 98
    cluster-type: cluster_flow
    defrag: yes

outputs:
  - eve-log:
      enabled: yes
      filetype: regular
      filename: /var/log/suricata/eve.json
      types:
        - alert
        - dns
        - http
        - tls
```

### 設定

1. 設定ファイルを編集してネットワークインターフェースを指定します。

    ```bash
    sudo nano /etc/suricata/suricata.yaml
    ```

    `af-packet` セクションの `interface` を監視したいインターフェース名（例: `eth0`）に変更してください。

2. ルールの更新

    ```bash
    sudo suricata-update
    ```

3. Suricataサービスを有効化・起動

    ```bash
    sudo systemctl enable suricata
    sudo systemctl start suricata
    ```

---

## OpenCanary のインストールと設定

### OpenCanaryのインストール

Python仮想環境を作成し、OpenCanaryをインストールします。

```bash
python3 -m venv opencanary-env
source opencanary-env/bin/activate
pip install opencanary
```

### 最小構成例

Raspberry Pi Zeroなどリソースが限られた環境では、軽量なサービス構成がおすすめです。  
以下は `/home/azazel/.opencanary.conf` の最小構成例です。

```json
{
  "device.node_id": "azazel-zero",
  "logger": {
    "class": "PyLogger",
    "kwargs": {
      "filename": "/var/log/opencanary.log"
    }
  },
  "ftp.enabled": true,
  "http.enabled": true,
  "ssh.enabled": true,
  "portscan.enabled": true
}
```

### OpenCanary 設定

1. 初期設定ファイルを作成します。

    ```bash
    opencanaryd --copyconfig
    ```

2. `~/.opencanary.conf` を編集して検知項目や通知設定を調整します。

    ```bash
    nano ~/.opencanary.conf
    ```

3. OpenCanaryをサービスとして登録（例）

    ```bash
    sudo nano /etc/systemd/system/opencanary.service
    ```

    以下を記述してください。

    ```ini
    [Unit]
    Description=OpenCanary Honeypot
    After=network.target

    [Service]
    User=your_user_name
    WorkingDirectory=/home/your_user_name/opencanary-env
    ExecStart=/home/your_user_name/opencanary-env/bin/opencanaryd --start
    Restart=always

    [Install]
    WantedBy=multi-user.target
    ```

4. サービスを有効化・起動

    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable opencanary
    sudo systemctl start opencanary
    ```

---

## スクリプト展開

運用に必要なスクリプトはリポジトリからクローンし、実行権限を付与してください。

```bash
git clone https://github.com/your_org/azazel-zero-scripts.git ~/azazel-zero-scripts
chmod +x ~/azazel-zero-scripts/*.sh
```

---

## 運用モードの設定

### tc と iptables の設定

ネットワークトラフィック制御およびフィルタリングのために `tc` と `iptables` を設定します。

```bash
sudo apt install -y iproute2 iptables
```

必要に応じて以下の例を参考に設定してください。

```bash
# 例: tcによる帯域制御
sudo tc qdisc add dev eth0 root tbf rate 10mbit burst 32kbit latency 400ms

# 例: iptablesによる特定ポートのブロック
sudo iptables -A INPUT -p tcp --dport 23 -j DROP
```

設定は起動時に自動適用されるようスクリプトにまとめてください。

---

## 検証

- Suricata のログを確認して正しくパケットを解析しているか確認します。

    ```bash
    sudo tail -f /var/log/suricata/suricata.log
    ```

- OpenCanary のログを確認し、不正アクセスの検知が行われているか確認します。

    ```bash
    tail -f ~/.opencanary.log
    ```

- ネットワーク制御設定が反映されているか `tc` と `iptables` の状態を確認します。

    ```bash
    sudo tc qdisc show dev eth0
    sudo iptables -L -v
    ```

---

## トラブルシュート

- Suricataが起動しない場合は設定ファイルの文法をチェックしてください。

    ```bash
    suricata -T -c /etc/suricata/suricata.yaml
    ```

- OpenCanaryが起動しない場合は依存関係とPython環境を再確認してください。

- ネットワーク制御が反映されない場合はインターフェース名やコマンドの誤りを確認してください。

---

## セキュリティ方針

- 管理者権限での操作は必要最低限に留めてください。
- 設定ファイルやログの権限は適切に設定し、不要なユーザからのアクセスを制限してください。
- 定期的にルールや検知設定をアップデートし、最新の脅威に対応してください。
- 監視ログは外部に漏れないよう適切に管理し、必要に応じて暗号化を検討してください。

---

以上でAzazel‑Zeroの必要ツールのインストールと初期設定は完了です。運用開始前に各ツールの動作確認を必ず行ってください。
