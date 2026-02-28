#!/usr/bin/env python3
"""Wi-Fi diagnostics collection with masking and bundle export."""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tarfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_DIAG_DIRS = [
    Path("/run/azazel/wifi_diagnostics"),
    Path("/tmp/azazel/wifi_diagnostics"),
]

SENSITIVE_PATTERNS = [
    re.compile(r"(?i)(passphrase\s*[=:]\s*)([^\s]+)"),
    re.compile(r"(?i)(password\s*[=:]\s*)([^\s]+)"),
    re.compile(r"(?i)(psk\s*[=:]\s*)([^\s]+)"),
    re.compile(r"(?i)(wifi-sec\.psk\s*[=:]\s*)([^\s]+)"),
    re.compile(r"(?i)(802-11-wireless-security\.psk\s*[=:]\s*)([^\s]+)"),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_component(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]", "_", value.strip())
    return text[:64] if text else "unknown"


def mask_sensitive_text(text: str) -> str:
    masked = text or ""
    for pattern in SENSITIVE_PATTERNS:
        masked = pattern.sub(lambda m: f"{m.group(1)}***", masked)

    # Handle keyfile style psk="..."
    masked = re.sub(r'(?i)(psk\s*=\s*")[^"]+("?)', r"\1***\2", masked)
    masked = re.sub(r"(?i)(wep-key\d*\s*=\s*)\S+", r"\1***", masked)
    return masked


def _run_cmd(cmd: List[str], timeout: int = 10) -> Dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout": mask_sensitive_text(proc.stdout or ""),
            "stderr": mask_sensitive_text(proc.stderr or ""),
            "elapsed_ms": elapsed_ms,
            "timeout": False,
        }
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "cmd": cmd,
            "returncode": None,
            "stdout": mask_sensitive_text((exc.stdout or "") if isinstance(exc.stdout, str) else ""),
            "stderr": mask_sensitive_text((exc.stderr or "") if isinstance(exc.stderr, str) else ""),
            "elapsed_ms": elapsed_ms,
            "timeout": True,
        }
    except Exception as exc:  # pragma: no cover - defensive
        elapsed_ms = int((time.time() - started) * 1000)
        return {
            "cmd": cmd,
            "returncode": None,
            "stdout": "",
            "stderr": mask_sensitive_text(str(exc)),
            "elapsed_ms": elapsed_ms,
            "timeout": False,
        }


def _pick_diag_dir(candidates: Optional[Iterable[Path]] = None) -> Path:
    for base in list(candidates or DEFAULT_DIAG_DIRS):
        try:
            base.mkdir(parents=True, exist_ok=True)
            probe = base / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return base
        except Exception:
            continue
    fb = Path("/tmp/azazel/wifi_diagnostics")
    fb.mkdir(parents=True, exist_ok=True)
    return fb


@dataclass
class WifiDiagnosticsSession:
    ssid: str
    iface: str
    profile_hint: str = ""
    base_dir: Path = field(default_factory=_pick_diag_dir)
    trial_id: str = field(init=False)
    started_at: str = field(default_factory=now_iso)
    payload: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        token = secrets.token_hex(4)
        self.trial_id = f"wifi-{stamp}-{token}"
        self.payload = {
            "trial_id": self.trial_id,
            "started_at": self.started_at,
            "ssid": self.ssid,
            "iface": self.iface,
            "profile_hint": self.profile_hint,
            "events": [],
            "snapshots": {},
            "logs": {},
            "result": {},
        }

    @property
    def trial_dir(self) -> Path:
        path = self.base_dir / self.trial_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def json_path(self) -> Path:
        return self.trial_dir / "attempt.json"

    @property
    def bundle_path(self) -> Path:
        return self.trial_dir / f"{self.trial_id}.tar.gz"

    def add_event(self, state: str, status: str, detail: Optional[Dict[str, Any]] = None) -> None:
        entry = {
            "ts": now_iso(),
            "state": state,
            "status": status,
        }
        if detail:
            entry["detail"] = _mask_obj(detail)
        self.payload.setdefault("events", []).append(entry)

    def capture_system_baseline(self) -> None:
        commands = {
            "uname": ["uname", "-a"],
            "networkmanager_version": ["NetworkManager", "--version"],
            "nmcli_version": ["nmcli", "--version"],
            "wpa_supplicant_version": ["wpa_supplicant", "-v"],
            "firmware_brcm": ["bash", "-lc", "ls -l /lib/firmware/brcm 2>/dev/null || true"],
        }
        self.payload.setdefault("snapshots", {}).setdefault("versions", {})
        for key, cmd in commands.items():
            self.payload["snapshots"]["versions"][key] = _run_cmd(cmd, timeout=8)

    def capture_runtime_snapshot(self, label: str = "runtime") -> None:
        commands = {
            "rfkill": ["rfkill", "list"],
            "ip_link": ["ip", "link", "show", self.iface],
            "iw_link": ["iw", "dev", self.iface, "link"],
            "iw_info": ["iw", "dev", self.iface, "info"],
            "iw_reg": ["iw", "reg", "get"],
            "nmcli_device_status": ["nmcli", "device", "status"],
            "nmcli_device_show": ["nmcli", "-f", "GENERAL,IP4,IP6", "device", "show", self.iface],
            "nmcli_connection_show": ["nmcli", "connection", "show"],
        }
        if self.profile_hint:
            commands["nmcli_connection_target"] = ["nmcli", "connection", "show", self.profile_hint]

        self.payload.setdefault("snapshots", {}).setdefault(label, {})
        for key, cmd in commands.items():
            self.payload["snapshots"][label][key] = _run_cmd(cmd, timeout=10)

    def capture_logs(self) -> None:
        since = self.started_at.replace("T", " ").replace("Z", "")
        log_cmds = {
            "networkmanager": ["journalctl", "-u", "NetworkManager", "-b", "--since", since, "--no-pager"],
            "wpa_supplicant": ["journalctl", "-t", "wpa_supplicant", "-b", "--since", since, "--no-pager"],
            "dmesg_wifi": ["bash", "-lc", "dmesg -T | egrep -i 'brcm|brcmfmac|cfg80211|wlan0|firmware' || true"],
        }
        self.payload.setdefault("logs", {})
        for key, cmd in log_cmds.items():
            self.payload["logs"][key] = _run_cmd(cmd, timeout=20)

    def set_result(self, result: Dict[str, Any]) -> None:
        self.payload["result"] = _mask_obj(result)
        self.payload["finished_at"] = now_iso()

    def write_json(self) -> Path:
        self.json_path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.json_path

    def build_bundle(self) -> Path:
        self.write_json()
        with tarfile.open(self.bundle_path, "w:gz") as tf:
            tf.add(self.json_path, arcname="attempt.json")
            for key, value in self.payload.get("logs", {}).items():
                out = value.get("stdout", "") if isinstance(value, dict) else ""
                err = value.get("stderr", "") if isinstance(value, dict) else ""
                log_file = self.trial_dir / f"log_{_safe_component(key)}.txt"
                log_file.write_text(out + ("\n" + err if err else ""), encoding="utf-8")
                tf.add(log_file, arcname=log_file.name)
        return self.bundle_path


def _mask_obj(data: Any) -> Any:
    if isinstance(data, dict):
        out: Dict[str, Any] = {}
        for key, value in data.items():
            lk = str(key).lower()
            if lk in {"passphrase", "password", "psk"}:
                out[key] = "***"
            else:
                out[key] = _mask_obj(value)
        return out
    if isinstance(data, list):
        return [_mask_obj(v) for v in data]
    if isinstance(data, str):
        return mask_sensitive_text(data)
    return data


__all__ = [
    "WifiDiagnosticsSession",
    "mask_sensitive_text",
]
