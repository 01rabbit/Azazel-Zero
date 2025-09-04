# Azazel‑Zero: Installation and Initial Setup of Required Tools

[English](/docs/tools_setup.md) | [日本語](/docs/tools_setup_ja.md)

## Basic Package Installation

Install the basic tools and dependencies using the following commands.

```bash
sudo apt update
sudo apt install -y build-essential libpcap-dev libpcre3-dev libyaml-dev libmagic-dev libnet1-dev libgeoip-dev python3 python3-pip python3-venv git curl
```

---

## Installation and Configuration of Suricata

### Installing Suricata

Install Suricata from the official repository.

```bash
sudo add-apt-repository ppa:oisf/suricata-stable
sudo apt update
sudo apt install -y suricata
```

### Interface Configuration (af-packet)

Please configure the `af-packet` section in `/etc/suricata/suricata.yaml` as shown below.  
Add `wlan0` and `usb0` as the interfaces to monitor, and enable eve-log output.  
*Replace only the relevant section as needed.*

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

### Configuration

1. Edit the configuration file to specify the network interfaces.

    ```bash
    sudo nano /etc/suricata/suricata.yaml
    ```

    Change the `interface` in the `af-packet` section to the interface you want to monitor (e.g., `eth0`).

2. Update the rules

    ```bash
    sudo suricata-update
    ```

3. Enable and start the Suricata service

    ```bash
    sudo systemctl enable suricata
    sudo systemctl start suricata
    ```

---

## Installation and Configuration of OpenCanary

### Installing OpenCanary

Create a Python virtual environment and install OpenCanary.

```bash
python3 -m venv opencanary-env
source opencanary-env/bin/activate
pip install opencanary
```

### Minimal Configuration Example

For resource-constrained environments such as Raspberry Pi Zero, a lightweight service configuration is recommended.  
Below is a minimal configuration example for `/home/azazel/.opencanary.conf`.

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

### OpenCanary Configuration

1. Create the initial configuration file.

    ```bash
    opencanaryd --copyconfig
    ```

2. Edit `~/.opencanary.conf` to adjust detection items and notification settings.

    ```bash
    nano ~/.opencanary.conf
    ```

3. Register OpenCanary as a service (example)

    ```bash
    sudo nano /etc/systemd/system/opencanary.service
    ```

    Enter the following content.

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

4. Enable and start the service

    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable opencanary
    sudo systemctl start opencanary
    ```

---

## Script Deployment

Clone the necessary scripts for operation from the repository and grant execution permissions.

```bash
git clone https://github.com/your_org/azazel-zero-scripts.git ~/azazel-zero-scripts
chmod +x ~/azazel-zero-scripts/*.sh
```

---

## Operation Mode Configuration

### tc and iptables Setup

Configure `tc` and `iptables` for network traffic control and filtering.

```bash
sudo apt install -y iproute2 iptables
```

Refer to the examples below as needed.

```bash
# Example: Bandwidth control using tc
sudo tc qdisc add dev eth0 root tbf rate 10mbit burst 32kbit latency 400ms

# Example: Blocking specific ports using iptables
sudo iptables -A INPUT -p tcp --dport 23 -j DROP
```

Please consolidate these settings into a script to apply them automatically at startup.

---

## Verification

- Check Suricata logs to confirm packets are being properly analyzed.

    ```bash
    sudo tail -f /var/log/suricata/suricata.log
    ```

- Check OpenCanary logs to verify detection of unauthorized access.

    ```bash
    tail -f ~/.opencanary.log
    ```

- Confirm that network control settings are applied by checking the status of `tc` and `iptables`.

    ```bash
    sudo tc qdisc show dev eth0
    sudo iptables -L -v
    ```

---

## Troubleshooting

- If Suricata does not start, check the syntax of the configuration file.

    ```bash
    suricata -T -c /etc/suricata/suricata.yaml
    ```

- If OpenCanary does not start, verify dependencies and the Python environment.

- If network control settings are not applied, check for errors in interface names or commands.

---

## Security Policy

- Limit operations requiring administrative privileges to the minimum necessary.
- Set appropriate permissions on configuration files and logs to restrict access from unauthorized users.
- Regularly update rules and detection settings to stay protected against the latest threats.
- Manage monitoring logs securely to prevent external leaks, and consider encryption if necessary.

---

This completes the installation and initial setup of required tools for Azazel‑Zero. Please ensure to verify the operation of each tool before starting operation.
