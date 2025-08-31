# Azazel-Zero Setup Guide (Bullseye 32bit)

[English](/docs/setup-zero.md) | [日本語](/docs/setup-zero_ja.md)

## Purpose

This guide explains how to install Raspberry Pi OS Lite (Bullseye 32bit) on the Raspberry Pi Zero 2 W, enable USB gadget mode, and establish network connectivity via USB.  
This allows the host PC (e.g. laptop) to access the Pi over USB and share the internet connection.

---

## Basic Setup Steps

### 1. OTG Configuration

Add the following to `/boot/config.txt`:

```txt
dtoverlay=dwc2
```

In `/boot/cmdline.txt`, immediately after `rootwait`:

```txt
modules-load=dwc2,g_ether
```

Additionally, for stable Windows/Linux support, create `/etc/modprobe.d/g_ether.conf` and add:

```conf
options g_ether use_eem=0
```

---

### 2. Network Configuration

Create `/etc/network/interfaces.d/usb0`:

```ini
auto usb0
iface usb0 inet static
  address 192.168.7.2
  netmask 255.255.255.0
```

---

### 3. DHCP Server Setup

Install `dnsmasq`:

```bash
sudo apt update && sudo apt install -y dnsmasq
```

Create config `/etc/dnsmasq.d/usb-gadget.conf`:

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

### 4. NAT Setup

Enable in `/etc/sysctl.conf`:

```conf
net.ipv4.ip_forward=1
```

Apply immediately:

```bash
sudo sysctl -w net.ipv4.ip_forward=1
```

Configure iptables:

```bash
sudo iptables -t nat -A POSTROUTING -o wlan0 -j MASQUERADE
sudo iptables -A FORWARD -i wlan0 -o usb0 -m state --state RELATED,ESTABLISHED -j ACCEPT
sudo iptables -A FORWARD -i usb0 -o wlan0 -j ACCEPT
```

Save rules:

```bash
sudo sh -c "iptables-save > /etc/iptables.ipv4.nat"
```

---

### 5. Notes for macOS / Windows

- **macOS**  
  - Disable Internet Sharing (bridge100).  
  - Disable Wi-Fi.  
  - If necessary, disable IPv6:  
  
    ```bash
    networksetup -setv6off "RNDIS/Ethernet Gadget"
    ```

- **Windows**: Detected as "RNDIS Gadget" and automatically obtains an IP via DHCP.  
- **Linux**: Detected as ECM device.  

---

### 6. Verification

On the Pi:

```bash
sudo ip link set usb0 up
```

On the host (Mac/PC):

```bash
ping 192.168.7.2
ping 8.8.8.8
curl -I https://example.com
```

---

### 7. Cross-Platform Notes

This setup works across macOS, Windows, and Linux, but behaviors may differ:

- **macOS**  
  - May assign self-allocated IP unless IPv6 is disabled.  
  - Ensure Internet Sharing (bridge100) is disabled.  

- **Windows**  
  - Automatically recognized as RNDIS.  
  - If DHCP fails, disable and re-enable the adapter.  

- **Linux**  
  - Detected as ECM.  
  - Check with:  

    ```bash
    dmesg | grep usb
    ```

---

### Appendix: One-Liner Setup Script (Advanced Users)

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
