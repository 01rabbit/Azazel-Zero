# azazel_zero/sensors/wifi_safety.py
from __future__ import annotations
from typing import Dict, Any, List, Optional, Tuple
import json, subprocess, time, re, shutil
from pathlib import Path

_MAC_RE = re.compile(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", re.I)

def _run(cmd: List[str], timeout: float = 2.5) -> str:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=timeout, text=True)
    return p.stdout or ""

def get_link_state(iface: str) -> Dict[str, str]:
    # iw dev wlan0 link -> includes: Connected to <BSSID> / SSID: <ssid>
    out = _run(["iw", "dev", iface, "link"])
    bssid = ""
    ssid = ""
    if "Not connected" in out:
        return {"connected": "0"}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("Connected to"):
            m = _MAC_RE.search(line)
            if m: bssid = m.group(0).lower()
        elif line.startswith("SSID:"):
            ssid = line.split("SSID:", 1)[1].strip()
    return {"connected": "1", "ssid": ssid, "bssid": bssid}

def load_known_db(path: str) -> Dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if p.is_dir() or (not p.exists()):
        return {}
    return json.loads(p.read_text(encoding="utf-8"))

def check_ap_fingerprint(link: Dict[str, str], known_db: Dict[str, Any]) -> List[str]:
    tags: List[str] = []
    if link.get("connected") != "1":
        return tags
    ssid = link.get("ssid") or ""
    bssid = (link.get("bssid") or "").lower()

    if not ssid:
        return tags
    prof = known_db.get(ssid)
    if not prof:
        return tags  # unknown SSID: do not accuse; rely on other sensors

    allow = set(x.lower() for x in (prof.get("bssids") or []))
    if allow and bssid and (bssid not in allow):
        tags.append("evil_ap")  # known SSID but unexpected BSSID
    return tags

def tcpdump_watch(iface: str, duration_sec: int = 3) -> str:
    # Capture minimal: ARP + DHCP + DNS
    if shutil.which("tcpdump") is None:
        return ""
    cmd = ["tcpdump", "-l", "-n", "-i", iface, "arp or (udp and (port 67 or 68)) or (udp and port 53)"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    end = time.time() + duration_sec
    buf: List[str] = []
    try:
        while time.time() < end:
            line = p.stdout.readline() if p.stdout else ""
            if not line:
                time.sleep(0.05)
                continue
            buf.append(line.strip())
    finally:
        p.terminate()
    return "\n".join(buf)

def detect_arp_spoof(tcpdump_text: str, gateway_ip: Optional[str]) -> List[str]:
    # Very lightweight heuristic:
    # if we see multiple different MACs claiming gateway_ip -> arp_spoof
    tags: List[str] = []
    if not gateway_ip:
        return tags
    macs = set()
    for line in tcpdump_text.splitlines():
        if ("ARP" in line) and (gateway_ip in line) and ("is-at" in line):
            m = _MAC_RE.search(line)
            if m:
                macs.add(m.group(0).lower())
    if len(macs) >= 2:
        tags.append("arp_spoof")
        tags.append("mitm")  # strong indicator at L2
    return tags

def detect_rogue_dhcp(tcpdump_text: str) -> List[str]:
    # If we observe multiple DHCP Offer/Ack from different servers in short window -> dhcp_spoof
    tags: List[str] = []
    servers = set()
    for line in tcpdump_text.splitlines():
        # tcpdump prints "DHCP-Message (Offer)" etc depending on version; keep fuzzy
        if ("DHCP" in line) and (("Offer" in line) or ("Ack" in line) or ("ACK" in line)):
            m = _MAC_RE.search(line)
            if m:
                servers.add(m.group(0).lower())
    if len(servers) >= 2:
        tags.append("dhcp_spoof")
        tags.append("mitm")
    return tags

def detect_dns_anomaly(tcpdump_text: str) -> List[str]:
    # Cheap heuristic: too many DNS replies pointing to same IP across different names in short window
    tags: List[str] = []
    ips = {}
    for line in tcpdump_text.splitlines():
        if " A " in line and ">" in line:
            # Example: "IP x.x.x.x.53 > y.y.y.y.12345: ... A 1.2.3.4"
            parts = line.split()
            for i, tok in enumerate(parts):
                if tok == "A" and i + 1 < len(parts):
                    ip = parts[i + 1]
                    ips[ip] = ips.get(ip, 0) + 1
    if any(v >= 8 for v in ips.values()):  # threshold: tune later
        tags.append("dns_spoof")
    return tags

def evaluate_wifi_safety(iface: str, known_db_path: str, gateway_ip: Optional[str]) -> Tuple[List[str], Dict[str, Any]]:
    known_db = load_known_db(known_db_path)
    link = get_link_state(iface)
    tags = []
    tags.extend(check_ap_fingerprint(link, known_db))

    cap = tcpdump_watch(iface, duration_sec=3)
    tags.extend(detect_arp_spoof(cap, gateway_ip))
    tags.extend(detect_rogue_dhcp(cap))
    tags.extend(detect_dns_anomaly(cap))

    # de-dup
    uniq = sorted(set(tags))
    meta = {"link": link, "capture_len": len(cap)}
    return uniq, meta
