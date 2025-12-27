#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

# Ensure local package import works when run from repo root
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from azazel_zero.app.threat_judge import judge_zero


def run_once(prompt: str, iface: str, known_db: str, gateway_ip: Optional[str]) -> dict:
    return judge_zero(prompt, iface, known_db, gateway_ip)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Azazel-Zero threat judge once or in a loop (no systemd).")
    parser.add_argument("--iface", default="wlan0", help="Wi-Fi interface (default: wlan0)")
    parser.add_argument("--known-db", default="", help="Known SSID/BSSID DB JSON path (optional)")
    parser.add_argument("--gateway-ip", default=None, help="Gateway IP for ARP spoof heuristics (optional)")
    parser.add_argument("--prompt", default="wifi_safety_check", help="Prompt/label to feed the judge")
    parser.add_argument("--interval", type=float, default=0.0, help="Loop interval seconds (0 to run once)")
    args = parser.parse_args()

    try:
        while True:
            verdict = run_once(args.prompt, args.iface, args.known_db, args.gateway_ip)
            print(json.dumps({"ts": time.time(), **verdict}, ensure_ascii=False))
            if args.interval <= 0:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
