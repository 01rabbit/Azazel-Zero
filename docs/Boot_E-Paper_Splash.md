# Azazel-Zero Boot Splash

[English](/docs/Boot_E-Paper_Splash.md) | [日本語](/docs/Boot_E-Paper_Splash_ja.md)

## Overview

Azazel-Zero provides a "boot splash" feature that displays the wireless LAN SSID and IP address on an E-Paper display during startup, allowing you to quickly check the network status at a glance. This feature runs on a Raspberry Pi and is designed to enable fast access to connection information without any physical operation.

## Required Dependencies

The following packages and libraries are required. Example installation steps are shown below.

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

### Get the driver via git (example: waveshare EPD library)

```sh
cd ~
git clone https://github.com/waveshare/e-Paper
```

(Please select the appropriate repository and driver according to your display.)

## Script Placement and One-time Test

The main script for this feature is located at:

```txt
/home/pi/Azazel-Zero/py/boot_splash_epd.py
```

### One-time Test Method

You can run the script once with the following commands to verify the display operation:

```sh
cd /home/pi/Azazel-Zero/py
python3 boot_splash_epd.py
```

#### Note on Driver Versions

- Depending on the generation of your E-Paper display (V4/V3/V2), the driver name imported and initialization code may differ. If you encounter errors, check the corresponding driver inside `waveshare_epd` and adjust the import statements at the top of the script accordingly.

## systemd Service Registration

To run automatically at startup, register it as a systemd service.

### Unit File Example

`/etc/systemd/system/azazel-boot-splash.service`

```ini
[Unit]
Description=Azazel-Zero E-Paper Boot Splash
After=network.target

[Service]
Type=simple
User=pi
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

- **Driver Name Mismatch**: If the waveshare driver name or path differs, you may get `ModuleNotFoundError` or similar errors. Refer to the `waveshare_epd` directory inside `py` or the official repository documentation and adjust the import statements accordingly.
- **SPI Not Enabled**: Enable the SPI interface via `raspi-config` or similar tools.
- **Fonts Not Installed**: For Japanese or special fonts, place the necessary font files in `/usr/share/fonts/truetype` or similar, and adjust the path in the script accordingly.
- **SSID Always Shows N/A**: This occurs if the wireless LAN interface name (e.g., wlan0) is different or not connected. Confirm manually with the `iwgetid` command and adjust the relevant part of the script.
- **IP Address Not Displayed / DHCP Delay**: If DHCP acquisition is delayed, the IP may be blank right after boot. You can address this by specifying `After=network-online.target` in the systemd unit.
- **Permission Issues**: Access to SPI and GPIO may require root privileges. If running as `User=pi` does not work, try testing with `sudo` or adding the user to the `gpio` group.

## Summary

This feature allows the SSID and IP address to be immediately displayed on an E-Paper display at Raspberry Pi startup, simplifying network status monitoring and initial setup. In the future, it can be extended by integrating icon display or notifications to services like Mattermost.
