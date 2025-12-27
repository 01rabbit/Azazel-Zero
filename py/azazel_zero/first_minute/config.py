from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover - dependency notice
    raise SystemExit("PyYAML is required: sudo apt-get install -y python3-yaml") from exc


@dataclass
class FirstMinuteConfig:
    interfaces: Dict[str, str]
    paths: Dict[str, str]
    dnsmasq: Dict[str, Any]
    state_machine: Dict[str, Any]
    probes: Dict[str, Any]
    policy: Dict[str, Any]
    status_api: Dict[str, Any]
    suricata: Dict[str, Any]
    deception: Dict[str, Any]

    @staticmethod
    def load(path: str | Path) -> "FirstMinuteConfig":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config not found: {p}")
        data = yaml.safe_load(p.read_text()) or {}
        # Provide minimal defaults for keys that may be missing to avoid KeyErrors
        defaults = {
            "interfaces": {"upstream": "wlan0", "downstream": "usb0", "mgmt_ip": "192.168.7.1", "mgmt_subnet": "192.168.7.0/24"},
            "paths": {},
            "dnsmasq": {"enable": True},
            "state_machine": {},
            "probes": {},
            "policy": {},
            "status_api": {"host": "192.168.7.1", "port": 8081},
            "suricata": {"enabled": False},
            "deception": {"enable_if_opencanary_present": True},
        }
        for key, val in defaults.items():
            data.setdefault(key, val)
        return FirstMinuteConfig(**data)

    @property
    def runtime_dir(self) -> Path:
        return Path(self.paths.get("runtime_dir", "/run/azazel-zero"))

    @property
    def log_dir(self) -> Path:
        return Path(self.paths.get("log_dir", "/var/log/azazel-zero"))

    @property
    def pid_file(self) -> Path:
        return Path(self.paths.get("pid_file", "/run/azazel-zero/first_minute.pid"))

    @property
    def dns_log_path(self) -> Path:
        return Path(self.paths.get("dns_log", "/var/log/azazel-dnsmasq.log"))

    @property
    def nft_template_path(self) -> Path:
        return Path(self.paths.get("nft_template", "/etc/azazel-zero/nftables/first_minute.nft"))

    @property
    def dnsmasq_conf_path(self) -> Path:
        return Path(self.paths.get("dnsmasq_conf", "/etc/azazel-zero/dnsmasq-first_minute.conf"))

    def ensure_dirs(self) -> None:
        # Try desired locations; if not writable (e.g., non-root), fall back to repo-local .azazel-zero
        try_dirs = [self.runtime_dir, self.log_dir]
        try:
            for d in try_dirs:
                d.mkdir(parents=True, exist_ok=True)
            return
        except PermissionError:
            pass

        fallback_base = Path(__file__).resolve().parents[3] / ".azazel-zero"
        fallback_runtime = fallback_base / "run"
        fallback_log = fallback_base / "log"
        fallback_base.mkdir(parents=True, exist_ok=True)
        fallback_runtime.mkdir(parents=True, exist_ok=True)
        fallback_log.mkdir(parents=True, exist_ok=True)
        self.paths["runtime_dir"] = str(fallback_runtime)
        self.paths["log_dir"] = str(fallback_log)
        self.paths["pid_file"] = str(fallback_runtime / "first_minute.pid")
        self.paths["dns_log"] = str(fallback_log / "azazel-dnsmasq.log")
        for d in [fallback_runtime, fallback_log]:
            d.mkdir(parents=True, exist_ok=True)

    def env(self) -> Dict[str, str]:
        env = os.environ.copy()
        env.setdefault("UPSTREAM_IFACE", self.interfaces["upstream"])
        env.setdefault("DOWNSTREAM_IFACE", self.interfaces["downstream"])
        return env
