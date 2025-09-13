# Azazel-Zero 起動スプラッシュ

[English](/docs/Boot_E-Paper_Splash.md) | [日本語](/docs/Boot_E-Paper_Splash_ja.md)

## 概要

Azazel-Zeroでは、起動時に無線LANのSSIDとIPアドレスを電子ペーパー（E-Paper）ディスプレイに表示することで、ネットワークの状態を一目で確認できる「起動スプラッシュ」機能を提供します。この機能はRaspberry Pi上で動作し、物理的な操作なしにアクセス情報を素早く把握できるよう設計されています。

## 必要な依存関係

### ハードウェア接続（20ピン GPIO ヘッダ利用）

本機能で利用する Waveshare 製 E-Paper モジュールは、Raspberry Pi Zero の 20 ピン GPIO ヘッダ経由で接続します。  
以下の条件を事前に満たしてください。

- **SPI 有効化**:  
  `sudo raspi-config` → 「Interface Options」→「SPI」を有効化してください。

- **接続ピン例 (2.13inch e-Paper HAT)**  
  
  | e-Paper ピン | Raspberry Pi GPIO ピン |
  |--------------|------------------------|
  | VCC          | 3.3V (ピン1)           |
  | GND          | GND (ピン6)            |
  | DIN          | MOSI (GPIO10, ピン19)  |
  | CLK          | SCLK (GPIO11, ピン23)  |
  | CS           | CE0  (GPIO8, ピン24)   |
  | DC           | GPIO25 (ピン22)        |
  | RST          | GPIO17 (ピン11)        |
  | BUSY         | GPIO24 (ピン18)        |

> モジュールによって配線が異なる場合があるため、使用する e-Paper の公式マニュアルも併せて確認してください。

以下のパッケージやライブラリが必要です。インストール手順例を示します。

### apt-get でインストール

```sh
sudo apt update
sudo apt install python3-pip python3-pil python3-spidev python3-dev python3-setuptools git
```

### pip でインストール

```sh
pip3 install RPi.GPIO
pip3 install spidev
pip3 install pillow
```

### git でドライバ取得（例: waveshare の EPD ライブラリ）

> 再現性のため、ドライバは `/opt/waveshare-epd` に固定配置します。

```sh
sudo mkdir -p /opt
cd /opt
sudo git clone https://github.com/waveshare/e-Paper waveshare-epd
# （任意）piユーザで実行する場合は所有権を合わせる
sudo chown -R pi:pi /opt/waveshare-epd
```

#### インポートパスの設定

`/home/pi/Azazel-Zero/py/boot_splash_epd.py` から確実にライブラリを参照できるよう、以下のいずれかを設定してください。

- **A) systemd での環境変数は `/etc/default/azazel-zero` で管理（推奨）**  
  systemd ユニットファイル内で直接 PYTHONPATH を指定せず、以下のように環境ファイルを読み込む設定にします。

  ```ini
  EnvironmentFile=-/etc/default/azazel-zero
  ```

  `/etc/default/azazel-zero` に必要に応じて PYTHONPATH を記述してください。

- **B) スクリプト側で `sys.path` を拡張**  
  `boot_splash_epd.py` の先頭付近に追記：
  
  ```python
  import sys
  sys.path.extend(["/opt/waveshare-epd", "/opt/waveshare-epd/python"])
  # 以降、あなたのディスプレイに合わせてドライバをインポート
  # from waveshare_epd import epd2in13_V4 など
  ```

## スクリプト配置と単発テスト

本機能のメインスクリプトは以下に配置されています。

```txt
/home/pi/Azazel-Zero/py/boot_splash_epd.py
```

### 単発テスト方法

以下のコマンドでスクリプトを単発実行し、表示動作を確認できます。

```sh
cd /home/pi/Azazel-Zero/py
python3 boot_splash_epd.py
```

#### ドライバ世代に関する注意

- ご利用の電子ペーパーの世代（V4/V3/V2）により、importするドライバ名や初期化コードが異なる場合があります。エラーが出る場合は、`waveshare_epd`内の該当ドライバを確認し、スクリプト先頭の import 文を適宜修正してください。

## systemd サービス登録

起動時に自動実行するには、systemd サービスとして登録します。

### ユニットファイル例

`/etc/systemd/system/azazel-epd.service`

```ini
[Unit]
Description=Azazel-Zero E-Paper Display Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=azazel
EnvironmentFile=-/etc/default/azazel-zero
ExecStart=/usr/bin/python3 ${EPD_PY}
WorkingDirectory=${AZAZEL_ROOT}/py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

### 有効化と起動

```sh
sudo systemctl daemon-reload
sudo systemctl enable azazel-epd
sudo systemctl start azazel-epd
```

## よくある問題と対処

- **ドライバ名不一致**: waveshareのドライバ名やパスが異なる場合、`ModuleNotFoundError`等が発生します。`py`ディレクトリ内の`waveshare_epd`ディレクトリや、公式リポジトリのドキュメントを参照し、import文を修正してください。
- **SPIが有効化されていない**: `raspi-config`等でSPIインターフェースを有効化してください。
- **フォント未導入**: 日本語表示や特殊なフォントを使う場合、`/usr/share/fonts/truetype`等に必要なフォントファイルを配置し、スクリプト内のパスを合わせてください。
- **SSIDが常にN/Aになる**: 無線LANインターフェース名（例: wlan0）が異なる、または接続されていない場合に発生します。`iwgetid`コマンドで手動確認し、スクリプトの該当箇所を修正してください。
- **IPアドレスが表示されない/DHCP遅延**: DHCP取得が遅い場合、起動直後はIPが空欄になることがあります。`After=network-online.target`を`systemd`ユニットに指定する等で対処可能です。
- **権限問題**: SPIやGPIOアクセスにroot権限が必要な場合があります。`User=pi`で動かない場合は`sudo`でのテストや、`gpio`グループへの追加を検討してください。

## まとめ

本機能により、Raspberry Pi起動時にSSIDとIPアドレスを電子ペーパーに即座に表示でき、ネットワーク状態の把握や初期セットアップが容易になります。EPD関連のサービスは `azazel-epd.service` に統一されており、今後はアイコン表示やMattermost等への通知機能と連携させることで、さらなる拡張が可能です。
