from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict
from urllib.request import urlopen


def _service_active(name: str) -> bool:
    try:
        res = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        return res.returncode == 0 and res.stdout.strip() == "active"
    except Exception:
        return False


def _pid_running(pid_file: Path, expected_cmd: str = "") -> bool:
    try:
        if not pid_file.exists():
            return False
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
    except PermissionError:
        pass
    except Exception:
        return False

    if expected_cmd:
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", errors="ignore")
            if expected_cmd not in cmdline:
                return False
        except Exception:
            return False
    return True


def _process_running(pattern: str) -> bool:
    try:
        res = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            timeout=1.0,
        )
        return res.returncode == 0
    except Exception:
        return False


def _ntfy_health_ok() -> bool:
    mgmt_ip = os.environ.get("MGMT_IP", "10.55.0.10")
    ntfy_port = os.environ.get("NTFY_PORT", "8081")
    url = f"http://{mgmt_ip}:{ntfy_port}/v1/health"
    try:
        with urlopen(url, timeout=1.0) as resp:
            if resp.status != 200:
                return False
            body = resp.read(256).decode("utf-8", errors="ignore")
            return '"healthy":true' in body
    except Exception:
        return False


def get_monitoring_state() -> Dict[str, str]:
    """Return ON/OFF status for local monitoring daemons."""
    opencanary_ok = _service_active("opencanary@az_canary.service") or _service_active("opencanary.service")
    suricata_ok = _service_active("suricata.service")
    ntfy_ok = _service_active("ntfy.service") and _ntfy_health_ok()
    opencanary_pid = Path("/home/azazel/canary-venv/bin/opencanaryd.pid")
    suricata_pid = Path("/run/suricata.pid")
    return {
        "opencanary": "ON"
        if (opencanary_ok or _pid_running(opencanary_pid, "opencanary") or _process_running("[o]pencanary.tac"))
        else "OFF",
        "suricata": "ON" if (suricata_ok or _pid_running(suricata_pid, "suricata")) else "OFF",
        "ntfy": "ON" if ntfy_ok else "OFF",
    }
