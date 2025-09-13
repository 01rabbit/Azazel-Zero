# Azazel-Zero

[English](/README.md) | [日本語](/README_ja.md)

## Concept

**Azazel-Zero** is a prototype of a **"Substitute Barrier"** implemented on Raspberry Pi Zero 2 W.  

This system is designed to **practically realize the delaying action of the Azazel System**.  
At the same time, it returns to the original concepts of the Azazel System: the ideas of a **"Substitute Barrier"** and a **"Barrier Maze"**.  

### Comparison with Azazel-Pi

- **Azazel-Pi**  
  - Built on Raspberry Pi 5 as a **Portable Security Gateway (Cyber Scapegoat Gateway)**.  
  - Designed as a **concept model** to provide low-cost protection for **small-scale networks temporarily constructed**.  
  - Strongly experimental in nature, serving as a testbed for multiple technical elements.  

- **Azazel-Zero**  
  - A **lightweight version**, intended for real-world operation by **limiting use cases and stripping away unnecessary features**.  
  - Built as a **portable physical barrier**, prioritizing mobility and practicality.  
  - Unlike the concept-model Azazel-Pi, Azazel-Zero is positioned as a **field-ready practical model**.  

---

## Design Principles

- **Portability**: Small enough to fit in a breast pocket  
- **Inevitability**: Forces itself between the device and the external network  
- **Simplicity**: Insertion of USB establishes the firewall  
- **Delaying Action**: Wastes the attacker’s time (a core concept of the Azazel System)  

---

## Implementation

### Base

- **Raspberry Pi Zero 2 W**

### Networking

- **USB OTG Gadget Mode**
  - Provides both power supply and virtual network via a single USB cable  
  - Runs immediately when powered by a laptop  

### Defense Functions (Lightweight)

- Blocking and delaying with **iptables/nftables**  
- Network delay and jitter insertion with **tc (Traffic Control)**  
- Dynamic control and notification with **custom Python scripts**  

### Status Display

- **E-Paper (E-Ink)**  
  - Uses a 2.13-inch monochrome (250×122) display  
  - UI shows threat level, actions, RTT, queue status, and captive portal detection in a concise format  

---

## AI Elements (Research Topic)

Azazel-Zero is designed as a lightweight firewall, and AI is not essential.  
However, considering current trends and technological potential, it is worth examining **as a research topic**.  

- **Limitations**  
  - Zero 2 W has limited CPU and RAM; large-scale AI is not feasible  
  - No GPU acceleration  

- **Possibilities**  
  - Lightweight ML models with scikit-learn (e.g., anomaly detection: Isolation Forest, one-class SVM)  
  - Small-scale inference with TensorFlow Lite (e.g., classifying normal vs. attack traffic)  

- **Positioning**  
  - Not implemented at present  
  - Potential for future expansion into a **"learning shield"**  

---

## Setup Steps (Overview)

※ For detailed setup instructions, please refer to [docs/setup-zero.md](docs/setup-zero.md).

1. Install **Raspberry Pi OS Lite (64bit)**  
2. Configure **USB Gadget Mode**  
   - Add `dtoverlay=dwc2` to `/boot/config.txt`  
   - Add `modules-load=dwc2,g_ether` to `/boot/cmdline.txt`  
3. Install **E-Paper control libraries** (e.g., Waveshare Python libraries)  
4. Create a **UI script** to display threat levels and delay status  
5. Configure as a **systemd service** so the shield and UI run at boot  

## Boot E-Paper Splash (Azazel-Zero, repo in ~/Azazel-Zero)

※ For detailed setup instructions, please refer to [Boot_E-Paper_Splash.md](/docs/Boot_E-Paper_Splash.md).

At boot, shows **SSID** and **IPv4** on a Waveshare e-Paper.  
Script: `py/boot_splash_epd.py`

**Setup**  

1) Dependencies can be installed in one step:  
   `sudo bash bin/install_dependencies.sh --with-epd`
2) Test: `sudo python3 ~/Azazel-Zero/py/boot_splash_epd.py`  
3) Enable service `azazel-epd.service` (paths are managed via `/etc/default/azazel-zero`).

If your panel driver is not `epd2in13_V4`, change it to `V3` or `V2` in the import line.
