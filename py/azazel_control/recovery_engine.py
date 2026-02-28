#!/usr/bin/env python3
"""Wi-Fi recovery steps from light to heavy."""

from __future__ import annotations

import subprocess
from typing import Any, Dict, List


class RecoveryEngine:
    """Execute staged recovery actions for the Wi-Fi interface."""

    def _run(self, cmd: List[str], timeout: int = 10) -> Dict[str, Any]:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return {
                "cmd": cmd,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip(),
            }
        except subprocess.TimeoutExpired:
            return {
                "cmd": cmd,
                "returncode": None,
                "stdout": "",
                "stderr": "timeout",
            }
        except Exception as exc:  # pragma: no cover
            return {
                "cmd": cmd,
                "returncode": None,
                "stdout": "",
                "stderr": str(exc),
            }

    def run(self, level: int, iface: str) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = []

        if level == 1:
            cmds = [
                ["nmcli", "dev", "disconnect", iface],
                ["nmcli", "dev", "connect", iface],
                ["nmcli", "dev", "wifi", "rescan", "ifname", iface],
            ]
        elif level == 2:
            cmds = [
                ["ip", "link", "set", iface, "down"],
                ["rfkill", "unblock", "wifi"],
                ["ip", "link", "set", iface, "up"],
            ]
        elif level == 3:
            cmds = [["systemctl", "restart", "NetworkManager"]]
        elif level == 4:
            cmds = [
                ["modprobe", "-r", "brcmfmac"],
                ["modprobe", "brcmfmac"],
            ]
        else:
            return {"ok": False, "level": level, "error": "unknown recovery level", "steps": []}

        ok = True
        for cmd in cmds:
            result = self._run(cmd)
            steps.append(result)
            if result.get("returncode") not in (0, None):
                ok = False

        return {
            "ok": ok,
            "level": level,
            "steps": steps,
        }


__all__ = ["RecoveryEngine"]
