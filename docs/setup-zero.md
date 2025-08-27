# Azazel-Zero Setup Guide (Bullseye 32bit)

[English](/docs/setup-zero.md) | [日本語](/docs/setup-zero_ja.md)

## Purpose

This guide explains the steps to install Raspberry Pi OS Lite (Bullseye 32bit) on a Raspberry Pi Zero 2 W and enable USB gadget mode to achieve network connection via USB. This allows access to the Pi from a host PC such as a laptop through a USB cable and enables internet sharing.

---

## Basic Setup Guide

1. Prepare the SD Card using Raspberry Pi Imager:

   - OS: Raspberry Pi OS Lite (Bullseye)
   - In the write options (gear icon), configure:
     - Hostname: `azazel-zero`
     - Enable SSH and set a username and password of your choice
     - Wi-Fi settings (SSID, password, country code JP)
     - Locale settings (timezone, keyboard layout JP)

2. Boot the Raspberry Pi Zero 2 W with the prepared SD card.

3. Update the system and install required packages:

    ```bash
    sudo apt update
    sudo apt upgrade -y
    sudo apt install -y git python3-pip
    ```

4. Clone the Waveshare driver repository and install the driver:

    ```bash
    git clone https://github.com/waveshare/LCD-show.git
    cd LCD-show/
    sudo ./LCD32-show
    ```

5. Place any required scripts or configuration files into appropriate directories as needed.

6. Perform a one-time test to verify the display and basic functionality.

---

## Extension: USB Gadget Mode

To enable network connection via USB cable, configure the Raspberry Pi as a USB Ethernet gadget:

1. Append the following line at the end of `boot/config.txt`:

    ```txt
    dtoverlay=dwc2
    ```

2. Append the following content to the end of `boot/cmdline.txt` without adding a newline:

    ```txt
    modules-load=dwc2,g_ether
    ```

3. After booting and logging into the Pi, assign a static IP to the `usb0` interface:

    ```bash
    sudo tee -a /etc/dhcpcd.conf >/dev/null <<'EOF'
    interface usb0
    static ip_address=10.0.0.1/24
    EOF
    sudo systemctl restart dhcpcd
    ```

4. Install and configure `dnsmasq` as a DHCP server:

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

5. Enable NAT (Network Address Translation) and packet forwarding:

    ```bash
    echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-azazel.conf
    sudo sysctl --system
    sudo iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
    sudo apt install -y iptables-persistent
    ```

6. Verify operation:

    - Confirm that the Pi is connected to Wi-Fi:

      ```bash
      iwgetid -r
      ip -4 addr show wlan0
      ping -c2 8.8.8.8 -I wlan0
      ```

    - When connecting a laptop via USB cable, an IP address in the range `10.0.0.x` will be assigned.

    - Verify connectivity from the laptop to the Pi:

      ```bash
      ping 10.0.0.1       # Check connection to Pi
      ping 8.8.8.8        # Check internet connection via Pi
      ```

---

## Summary

- Pre-configuring Wi-Fi and SSH using Raspberry Pi Imager allows smooth connection from the first boot.
- Installing Waveshare drivers and placing necessary scripts ensures proper hardware operation.
- The `dwc2` and `g_ether` modules enable network connection via USB cable.
- Configuring `dnsmasq` and `iptables` allows the Pi to function as a Wi-Fi adapter over USB connection.
