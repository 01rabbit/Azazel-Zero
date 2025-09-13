# Azazel-Zero Boot Splash

[English](/docs/Boot_E-Paper_Splash.md) | [日本語](/docs/Boot_E-Paper_Splash_ja.md)

## Overview

Azazel-Zero provides a "boot splash" feature that displays the wireless LAN SSID and IP address on an electronic paper (E-Paper) display at startup, allowing you to easily check the network status at a glance. This feature runs on a Raspberry Pi and is designed to quickly grasp access information without any physical operation.

## Required Dependencies

### Hardware Connection (Using 20-pin GPIO Header)

The Waveshare E-Paper module used in this feature is connected via the 20-pin GPIO header of the Raspberry Pi Zero.  
Please ensure the following conditions are met in advance.

- **Enable SPI**:  
  Run `sudo raspi-config` → "Interface Options" → enable "SPI".

- **Connection Pin Example (2.13inch e-Paper HAT)**  
  
  | e-Paper Pin | Raspberry Pi GPIO Pin       |
  |-------------|-----------------------------|
  | VCC         | 3.3V (Pin 1)                |
  | GND         | GND (Pin 6)                 |
  | DIN         | MOSI (GPIO10, Pin 19)       |
  | CLK         | SCLK (GPIO11, Pin 23)       |
  | CS          | CE0  (GPIO8, Pin 24)        |
  | DC          | GPIO25 (Pin 22)             |
  | RST         | GPIO17 (Pin 11)             |
  | BUSY        | GPIO24 (Pin 18)             |

> Since wiring may differ depending on the module, please also check the official manual of the E-Paper you are using.

The following packages and libraries are required. Example installation procedures are shown.

### Installation via apt-get

```sh
sudo apt update
sudo apt install python3-pip python3-pil python3-spidev python3-dev python3-setuptools git
```

### Installation via pip

```sh
pip3 install RPi.GPIO
pip3 install spidev
pip3 install pillow
```

### Obtaining the Driver via git (Example: waveshare EPD library)

> For reproducibility, the driver is fixedly placed in `/opt/waveshare-epd`.

```sh
sudo mkdir -p /opt
cd /opt
sudo git clone https://github.com/waveshare/e-Paper waveshare-epd
# (Optional) If running as pi user, adjust ownership accordingly
sudo chown -R pi:pi /opt/waveshare-epd
```

#### Setting Import Path

To ensure that the library can be referenced from `/home/pi/Azazel-Zero/py/boot_splash_epd.py`, please set one of the following.

- **A) Manage environment variables for systemd in `/etc/default/azazel-zero` (Recommended)**  
  Instead of specifying PYTHONPATH directly in the systemd unit file, set it to read the environment file as follows:

  ```ini
  EnvironmentFile=-/etc/default/azazel-zero
  ```

  Write the necessary PYTHONPATH in `/etc/default/azazel-zero` as needed.

- **B) Extend `sys.path` in the script**  
  Add the following near the top of `boot_splash_epd.py`:
  
  ```python
  import sys
  sys.path.extend(["/opt/waveshare-epd", "/opt/waveshare-epd/python"])
  # Then import the driver according to your display
  # e.g., from waveshare_epd import epd2in13_V4
  ```

## Script Placement and One-Time Test

The main script for this feature is placed as follows.

```txt
/home/pi/Azazel-Zero/py/boot_splash_epd.py
```

### One-Time Test Method

Run the following command to execute the script once and check the display operation.

```sh
cd /home/pi/Azazel-Zero/py
python3 boot_splash_epd.py
```

#### Notes on Driver Generations

- Depending on the generation of the E-Paper you use (V4/V3/V2), the imported driver name and initialization code may differ. If errors occur, check the corresponding driver inside `waveshare_epd` and modify the import statements at the top of the script accordingly.

## systemd Service Registration

To run automatically at startup, register as a systemd service.

### Unit File Example

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

### Enable and Start

```sh
sudo systemctl daemon-reload
sudo systemctl enable azazel-epd
sudo systemctl start azazel-epd
```

## Common Issues and Solutions

- **Driver name mismatch**: If the waveshare driver name or path differs, you may encounter `ModuleNotFoundError` etc. Refer to the `waveshare_epd` directory inside the `py` directory or the official repository documentation, and correct the import statements accordingly.
- **SPI not enabled**: Enable the SPI interface using `raspi-config` or similar.
- **Fonts not installed**: For Japanese display or special fonts, place the required font files under `/usr/share/fonts/truetype` or similar, and adjust the path in the script accordingly.
- **SSID always shows N/A**: This occurs if the wireless LAN interface name (e.g., wlan0) differs or is not connected. Confirm manually with the `iwgetid` command and adjust the relevant part of the script.
- **IP address not displayed / DHCP delay**: If DHCP acquisition is delayed, the IP may be blank immediately after startup. Specifying `After=network-online.target` in the systemd unit can help.
- **Permission issues**: Root privileges may be required for SPI or GPIO access. If running as `User=pi` does not work, try testing with `sudo` or consider adding the user to the `gpio` group.

## Summary

This feature allows you to instantly display the SSID and IP address on the E-Paper at Raspberry Pi startup, making it easier to grasp network status and perform initial setup. The EPD-related service is unified under `azazel-epd.service`, and future expansions such as icon display or integration with Mattermost notifications are possible.
