# Azazel-Zero Setup Guide (Raspberry Pi OS Lite Trixie / 64-bit)

English | [日本語](/docs/setup-zero_ja.md)

## Purpose and Target Architecture

This guide explains how to install Raspberry Pi OS Lite (Trixie 64-bit) on a Raspberry Pi Zero 2 W and:

- Enable USB gadget (OTG) using **Mode B**: `dwc2 + g_ether`
- Fix `usb0` to **10.55.0.1/24**
- Use `wlan0` as the upstream Wi-Fi interface for Internet access
- Enable routing / NAT (iptables) from `usb0 → wlan0`

so that you can build the following path:

> Upstream Internet (Wi-Fi) → Raspberry Pi (Azazel-Zero) → Laptop (via USB)

Configuration of the laptop side (USB NIC IP / default gateway / DNS) is assumed to be done by the user according to the environment.

---

## 0. Prerequisites

- Hardware  
  - Raspberry Pi Zero 2 W  
  - USB data-capable cable + USB gadget adapter for Zero  
- OS  
  - Raspberry Pi OS Lite (64-bit, Trixie)  
- Example host environment  
  - macOS (other OSes are also possible with equivalent steps)

---

## 1. Write Raspberry Pi OS Lite to the SD card

1. Launch Raspberry Pi Imager on your laptop.  
2. Select **“Raspberry Pi OS Lite (64-bit)”** as the OS.  
3. Choose the target SD card and start writing.  
4. After the write completes, remove and reinsert the SD card, then mount the **boot partition** (`bootfs` / `boot`, etc.) on the host.

In the following, this partition is referred to as `BOOT`.

---

## 2. Configuration on the BOOT partition (Mode B)

### 2-1. Create `userconf.txt` (user `pi` / password `raspberry`)

To skip the interactive first-boot setup, create `userconf.txt`:

```bash
cd /Volumes/bootfs   # On some systems this may be /Volumes/boot
HASH="$(echo 'raspberry' | openssl passwd -6 -stdin)"
printf "pi:%s\n" "$HASH" > userconf.txt
```

### 2-2. Enable SSH (create `ssh` file)

```bash
touch ssh
```

This enables SSH from the very first boot.

### 2-3. Enable OTG (`dwc2 + g_ether`)

#### `config.txt`

Open `BOOT/config.txt` and append the following at the end:

```ini
dtoverlay=dwc2
```

#### `cmdline.txt`

`BOOT/cmdline.txt` is a file with **exactly one line**.  
Without adding any newlines, append the following immediately after `rootwait`:

```text
... rootwait modules-load=dwc2,g_ether ...
```

> Example: add a single space after `rootwait`, then write `modules-load=dwc2,g_ether`.

### 2-4. Fixed `usb0` IP using `user-data` (cloud-init + systemd)

If `BOOT/user-data` already exists, **delete all its content first** and then overwrite it with the configuration below.  
If it does not exist, create it with the content below.

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
      # Wait a bit until usb0 appears
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

With this in place, `usb0-static.service` runs at boot and **sets `usb0=10.55.0.1/24` every time**.  
It ensures that NetworkManager or other automatic mechanisms do not override the address.

### 2-5. (Optional) g_ether compatibility settings

To improve compatibility with Windows / Linux hosts, you can create `/etc/modprobe.d/g_ether.conf` on the Pi **after boot** and write:

```conf
options g_ether use_eem=0
```

This is optional and can be configured later, so here it is described only as a note.

### 2-6. Unmount the SD card

```bash
sync
diskutil eject /Volumes/bootfs 2>/dev/null || diskutil eject /Volumes/boot
```

---

## 3. First boot and logging in over USB (host-side configuration)

1. Insert the SD card into the Raspberry Pi Zero 2 W.  
2. Connect the **USB data port of the Zero 2 W (the side labeled “USB”)** to your laptop via the USB gadget adapter.  
3. Wait about 30–60 seconds for the Pi to boot.

Example (macOS host):

1. Check the interface name of the USB NIC:

   ```bash
   ifconfig | egrep '^(en[0-9]+:|status:|inet )'
   ```

   Identify the interface corresponding to `RNDIS/Ethernet Gadget` or `Raspberry Pi USB` (e.g. `en17`).

2. Assign an IP address to the USB NIC (example with `en17`):

   ```bash
   IF=en17  # Replace with the actual interface name
   sudo ifconfig "$IF" inet 10.55.0.2 netmask 255.255.255.0 up
   ```

3. Verify connectivity and log in to the Pi:

   ```bash
   ping -c 2 10.55.0.1
   ssh pi@10.55.0.1   # Password: raspberry
   ```

At this point:

- Pi side: `usb0 = 10.55.0.1/24`  
- Laptop: `USB NIC = 10.55.0.2/24`  

and you should be able to log in to the Pi over USB via SSH.

---

## 4. Enabling and connecting Wi-Fi on the Raspberry Pi

From here on, work on the Pi over SSH (`pi@10.55.0.1`).

### 4-1. Check and unblock rfkill

```bash
rfkill list
sudo rfkill unblock wifi
sudo rfkill unblock all
rfkill list
```

Confirm that `Soft blocked` / `Hard blocked` for `Wireless LAN` are both `no`.

### 4-2. Turn Wi-Fi radio ON and bring the interface UP

On Trixie, NetworkManager is enabled by default, so use `nmcli`:

```bash
sudo nmcli r wifi on
nmcli dev status

sudo ip link set wlan0 up
ip -br link | grep wlan0
```

If `wlan0` is shown as `UP`, you are ready.

### 4-3. Scan for nearby access points

```bash
sudo iw dev wlan0 scan | egrep 'SSID|freq|signal' | head -n 20
nmcli dev wifi list
```

Verify that the desired **2.4 GHz SSID** (for example, `JCOM_NYRY`) is visible.

### 4-4. Connect to Wi-Fi and persist the profile

```bash
sudo nmcli dev wifi connect "JCOM_NYRY" password "YOUR_WIFI_PASSWORD" ifname wlan0
```

Then check the state:

```bash
ip -4 a show wlan0
ip r
ping -c 3 8.8.8.8
ping -c 3 google.com
```

If `wlan0` has a local IP address (e.g. `192.168.40.x`) and can reach the Internet, the connection is successful.

Enable autoconnect:

```bash
nmcli con show
sudo nmcli con mod "JCOM_NYRY" connection.autoconnect yes
```

This ensures that the Pi reconnects to the same SSID automatically after reboot.

---

## 5. Routing / NAT on the Pi (upstream → Pi → downstream)

The following steps turn the Pi into a **USB–Wi-Fi router**.

- Upstream interface: `wlan0` (Wi-Fi)  
- Downstream interface: `usb0` (USB gadget / laptop side)

Configuration of the default gateway on the laptop side is left to the user and is described only as a prerequisite.

### 5-1. Persist IPv4 forwarding

First, enable forwarding temporarily for testing:

```bash
sudo sysctl -w net.ipv4.ip_forward=1
```

Then add persistent configuration:

```bash
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-ipforward.conf
sudo sysctl --system
```

IPv4 forwarding will now remain enabled after reboot.

### 5-2. Persist NAT / FORWARD rules with a script + systemd

#### 5-2-1. Create the NAT script

```bash
sudo tee /usr/local/sbin/azazel-nat.sh >/dev/null <<'EOF'
#!/bin/sh
set -eu

OUT_IF="wlan0"
IN_IF="usb0"

# NAT (POSTROUTING) - check first to avoid duplicates
iptables -t nat -C POSTROUTING -o "$OUT_IF" -j MASQUERADE 2>/dev/null || \
iptables -t nat -A POSTROUTING -o "$OUT_IF" -j MASQUERADE

# FORWARD (usb0 -> wlan0)
iptables -C FORWARD -i "$IN_IF" -o "$OUT_IF" -j ACCEPT 2>/dev/null || \
iptables -A FORWARD -i "$IN_IF" -o "$OUT_IF" -j ACCEPT

# FORWARD (wlan0 -> usb0, return traffic)
iptables -C FORWARD -i "$OUT_IF" -o "$IN_IF" -m state --state ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
iptables -A FORWARD -i "$OUT_IF" -o "$IN_IF" -m state --state ESTABLISHED,RELATED -j ACCEPT
EOF

sudo chmod 0755 /usr/local/sbin/azazel-nat.sh
```

#### 5-2-2. Create the systemd unit

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

Check the status:

```bash
sudo systemctl status azazel-nat.service --no-pager
sudo iptables -t nat -L -n -v
sudo iptables -L FORWARD -n -v
```

If you see a `MASQUERADE` rule in `POSTROUTING` and `usb0` ↔ `wlan0` rules in `FORWARD`, the configuration is applied correctly.

---

## 6. Laptop-side prerequisites and connectivity test

If you configure the USB NIC on your laptop as follows:

- IP address: `10.55.0.2/24`  
- Default gateway: `10.55.0.1`  
- DNS: your home router’s IP or `8.8.8.8`, etc.

then all traffic will flow as:

> Laptop → usb0 (10.55.0.1) → wlan0 → Upstream Internet

Example connectivity checks:

1. From laptop to the Pi:

   ```bash
   ping 10.55.0.1
   ```

2. From laptop to the Internet:

   ```bash
   ping 8.8.8.8
   ping google.com
   ```

---

## 7. Positioning for future Azazel-Zero development

The configuration in this guide provides the **minimal router foundation** for treating Azazel-Zero as:

- `usb0`: ingress from downstream clients (user devices)  
- `wlan0`: egress to the upstream Internet  

On top of this, you can build the full Azazel-Zero gateway by layering:

- Traffic detection with Suricata  
- Delay-to-Win behavior using tc / iptables / nftables  
- Decoy services such as OpenCanary  
- Scoring with Mock-LLM / real LLMs  

to achieve the complete Azazel-Zero feature set.
