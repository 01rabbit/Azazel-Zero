# Azazel-Zero Boot Splash

[English](/docs/Boot_E-Paper_Splash.md) | [日本語](/docs/Boot_E-Paper_Splash_ja.md)

## Overview

Azazel-Zero provides a "boot splash" feature that displays the wireless LAN SSID and IP address on an e-paper (E-Paper) display at startup, allowing you to quickly check the network status at a glance. This feature runs on Raspberry Pi and is designed to enable quick access to network information without any physical operation.

## Required Dependencies

### Hardware Connection (Using 20-pin GPIO Header)

The Waveshare E-Paper module used for this feature connects via the 20-pin GPIO header of the Raspberry Pi Zero.  
Please ensure the following conditions are met in advance.

- **Enable SPI:**  
  Run `sudo raspi-config` → "Interface Options" → Enable "SPI".

- **Example Connection Pins (2.13inch e-Paper HAT)**  
  
  | E-Paper Pin | Raspberry Pi GPIO Pin   |
  |-------------|------------------------|
  | VCC         | 3.3V (Pin 1)           |
  | GND         | GND (Pin 6)            |
  | DIN         | MOSI (GPIO10, Pin 19)  |
  | CLK         | SCLK (GPIO11, Pin 23)  |
  | CS          | CE0  (GPIO8, Pin 24)   |
  | DC          | GPIO25 (Pin 22)        |
  | RST         | GPIO17 (Pin 11)        |
  | BUSY        | GPIO24 (Pin 18)        |

> Since wiring may differ depending on the module, please also check the official manual of the e-Paper you are using.

The following packages and libraries are required. Below are example installation steps.

### Install with apt-get

```sh
sudo apt update
sudo apt install python3-pip python3-pil python3-spidev python3-dev python3-setuptools git
```

### Install with pip

```sh
pip3 install RPi.GPIO
pip3 install spidev
pip3 install pillow
```

### Get Driver with git (Example: waveshare EPD library)

> For reproducibility, the driver is fixed to `/opt/waveshare-epd`.

```sh
sudo mkdir -p /opt
cd /opt
sudo git clone https://github.com/waveshare/e-Paper waveshare-epd
# (Optional) If running as pi user, adjust ownership
sudo chown -R pi:pi /opt/waveshare-epd
```

#### Import Path Settings

To ensure the library can be referenced from `/home/pi/Azazel-Zero/py/boot_splash_epd.py`, configure one of the following:

- **A) Specify PYTHONPATH in systemd environment variables (recommended)**  
  Add the following line in the example unit file:

  ```ini
  Environment=PYTHONPATH=/opt/waveshare-epd:/opt/waveshare-epd/python
  ```

- **B) Extend `sys.path` in the script**  
  Add the following near the top of `boot_splash_epd.py`:

  ```python
  import sys
  sys.path.extend(["/opt/waveshare-epd", "/opt/waveshare-epd/python"])
  # Then import the driver according to your display, e.g.
  # from waveshare_epd import epd2in13_V4
  ```

## Script Placement and Single-run Test

The main script for this feature is located at:

```txt
/home/pi/Azazel-Zero/py/boot_splash_epd.py
```

### Single-run Test Method

Run the following command to execute the script once and verify the display operation:

```sh
cd /home/pi/Azazel-Zero/py
python3 boot_splash_epd.py
```

#### Note on Driver Versions

- Depending on the generation of your e-paper (V4/V3/V2), the driver name and initialization code may differ. If you encounter errors, check the corresponding driver inside `waveshare_epd` and modify the import statements at the top of the script accordingly.

## systemd Service Registration

To run automatically at startup, register it as a systemd service.

### Example Unit File

`/etc/systemd/system/azazel-boot-splash.service`

```ini
[Unit]
Description=Azazel-Zero E-Paper Boot Splash
After=network.target

[Service]
Type=simple
User=pi
Environment=PYTHONPATH=/opt/waveshare-epd:/opt/waveshare-epd/python
ExecStart=/usr/bin/python3 /home/pi/Azazel-Zero/py/boot_splash_epd.py
WorkingDirectory=/home/pi/Azazel-Zero/py
Restart=no

[Install]
WantedBy=multi-user.target
```

### Enable and Start

```sh
sudo systemctl daemon-reload
sudo systemctl enable azazel-boot-splash
sudo systemctl start azazel-boot-splash
```

## Common Issues and Solutions

- **Driver Name Mismatch:** If the waveshare driver name or path differs, you may get `ModuleNotFoundError`. Check the `waveshare_epd` directory inside `py` or the official repository documentation and fix the import statements accordingly.
- **SPI Not Enabled:** Enable the SPI interface via `raspi-config` or equivalent.
- **Fonts Not Installed:** For Japanese or special fonts, place necessary font files in `/usr/share/fonts/truetype` or similar and adjust the font path in the script.
- **SSID Always N/A:** This can occur if the wireless LAN interface name (e.g., wlan0) is different or not connected. Verify manually with the `iwgetid` command and adjust the script accordingly.
- **IP Address Not Displayed / DHCP Delay:** If DHCP acquisition is slow, IP may be blank just after boot. Specifying `After=network-online.target` in the systemd unit can help.
- **Permission Issues:** SPI or GPIO access may require root privileges. If it does not work with `User=pi`, test with `sudo` or consider adding the user to the `gpio` group.

## Summary

This feature allows the Raspberry Pi to immediately display the SSID and IP address on the e-paper at startup, making it easier to understand network status and perform initial setup. In the future, it can be extended with icon display or integration with notification functions such as Mattermost.
