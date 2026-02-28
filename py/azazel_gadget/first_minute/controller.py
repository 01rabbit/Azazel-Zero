from __future__ import annotations

import json
import ipaddress
import logging
import os
import re
import signal
import socket
from urllib.parse import urlparse
import subprocess
import threading
import time
import shutil
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from azazel_gadget.sensors.wifi_safety import evaluate_wifi_safety
from azazel_gadget.tactics_engine import ConfigHash, DecisionLogger
from azazel_gadget.tactics_engine.decision_logger import (
    StateSnapshot, InputSnapshot, ScoreDelta, ChosenAction, DecisionRecord,
)

from .config import FirstMinuteConfig
from .dns_observer import DNSObserver, seed_probe_ips
from .nft import NftManager
from .notifier import NtfyNotifier  # ★ ntfy 通知統合
from .probes import ProbeOutcome, run_all
from .state_machine import FirstMinuteStateMachine, Stage
from .tc import TcManager
from .web_api import add_history_event
from azazel_gadget.path_schema import snapshot_path_candidates, warn_if_legacy_path


# Global lock for status_ctx to ensure thread-safe access
_status_ctx_lock = threading.Lock()
EXCLUDED_IFACE_PREFIXES = (
    "lo",
    "docker",
    "veth",
    "br",
    "tun",
    "tap",
    "wg",
    "virbr",
)


class StatusHandler(BaseHTTPRequestHandler):
    def __init__(self, ctx: Dict[str, object], ctx_lock: threading.Lock, *args, **kwargs):
        self.ctx = ctx
        self.ctx_lock = ctx_lock
        super().__init__(*args, **kwargs)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        with self.ctx_lock:
            if self.path == "/details":
                payload = {
                    "status": "ok",
                    "details": self.ctx.get("last_probe"),
                    "state": self.ctx.get("stage") or self.ctx.get("state"),
                    "suspicion": self.ctx.get("suspicion", 0),
                    "reason": self.ctx.get("reason", ""),
                }
                self.wfile.write(json.dumps(payload, default=str).encode("utf-8"))
                return
            self.wfile.write(json.dumps(self.ctx, default=str).encode("utf-8"))

    def do_POST(self):
        """Handle POST requests for action endpoints.
        
        Supported endpoints:
        - /action/reprobe: Force re-run safety probes
        - /action/refresh: Force refresh EPD display and state
        - /action/contain: Activate CONTAIN stage
        - /action/release: Release manual CONTAIN override
        - /action/stage_open: Return to NORMAL (open stage)
        - /action/disconnect: Disconnect downstream clients
        """
        if self.path == "/action/reprobe":
            # Signal controller to re-run probes
            with self.ctx_lock:
                self.ctx["force_reprobe"] = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "action": "reprobe"}).encode("utf-8"))
        elif self.path == "/action/refresh":
            # Signal controller to force EPD update
            with self.ctx_lock:
                self.ctx["force_epd_update"] = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "action": "refresh"}).encode("utf-8"))
        elif self.path == "/action/contain":
            # Signal controller to force CONTAIN stage
            with self.ctx_lock:
                self.ctx["force_contain"] = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "action": "contain"}).encode("utf-8"))
        elif self.path == "/action/release":
            # Signal controller to release manual CONTAIN override
            with self.ctx_lock:
                self.ctx["force_release"] = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "action": "release"}).encode("utf-8"))
        elif self.path == "/action/stage_open":
            # Signal controller to force NORMAL stage
            with self.ctx_lock:
                self.ctx["force_stage_open"] = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "action": "stage_open"}).encode("utf-8"))
        elif self.path == "/action/disconnect":
            # Signal controller to disconnect downstream clients
            with self.ctx_lock:
                self.ctx["force_disconnect"] = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "action": "disconnect"}).encode("utf-8"))
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "unknown endpoint"}).encode("utf-8"))

    def log_message(self, fmt, *args):  # pragma: no cover - avoid noisy logs
        return


def make_status_server(host: str, port: int, ctx: Dict[str, object], ctx_lock: threading.Lock) -> ThreadingHTTPServer:
    def handler(*args, **kwargs):
        return StatusHandler(ctx, ctx_lock, *args, **kwargs)

    return ThreadingHTTPServer((host, port), handler)


class FirstMinuteController:
    def __init__(self, cfg: FirstMinuteConfig, dry_run: bool = False, no_dns_start: bool = False, pretty_console: bool = False):
        self.cfg = cfg
        self.dry_run = dry_run
        self.no_dns_start = no_dns_start
        self.pretty_console = pretty_console
        self.logger = logging.getLogger("first_minute")
        self.stop_event = threading.Event()
        self.state_machine = FirstMinuteStateMachine(cfg.state_machine)
        self.current_stage: Stage = Stage.INIT
        self.last_probe: Optional[ProbeOutcome] = None
        self.cfg.interfaces.setdefault("upstream", "auto")
        self.cfg.interfaces.setdefault("captive_probe", "auto")
        initial_upstream = self._detect_best_upstream()
        if not initial_upstream:
            initial_upstream = str(self.cfg.interfaces.get("upstream", "") or "").strip()
        if not initial_upstream or initial_upstream.lower() == "auto":
            initial_upstream = "lo"
        if initial_upstream:
            cfg.interfaces["upstream"] = initial_upstream
        self._resolved_captive_probe_iface = ""
        self._resolved_captive_probe_reason = "NOT_FOUND"
        self.nft = NftManager(
            cfg.nft_template_path,
            initial_upstream,
            cfg.interfaces["downstream"],
            cfg.interfaces["mgmt_ip"],
            cfg.interfaces["mgmt_subnet"],
            int(cfg.policy.get("probe_allow_ttl", 120)),
            int(cfg.policy.get("dynamic_allow_ttl", 300)),
        )
        self.tc = TcManager(cfg.interfaces["downstream"], initial_upstream)
        self.dns_thread: Optional[DNSObserver] = None
        
        # ★ ntfy 通知クライアントを初期化
        self.notifier: Optional[NtfyNotifier] = None
        if cfg.notify.get("enabled", False):
            self._init_notifier()
        
        # Tactics Engine: config_hash を計算して status_ctx に追加
        self.config_hash = ConfigHash.compute(config_file=Path(cfg.yaml_path) if cfg.yaml_path else None)
        self.last_decision_id: Optional[str] = None
        self.decision_logger = DecisionLogger(output_dir=Path("/opt/azazel/logs/tactics_engine"))
        
        # Web UI 用：起動時刻を記録
        self._start_time = time.time()
        
        # Status context with thread lock for safe concurrent access
        self.status_ctx_lock = threading.Lock()
        self.status_ctx: Dict[str, object] = {
            "state": "INIT",
            "suspicion": 0,
            "last_probe": None,
            "config_hash": self.config_hash,
            "last_decision_id": self.last_decision_id,
            "start_time": self._start_time,
            "upstream_if": initial_upstream,
            "captive_probe_iface": "",
        }
        # Manual action flags to force and maintain state
        self.forced_contain_until: float = 0.0  # Timestamp until which to maintain temporary CONTAIN
        self.manual_contain_active: bool = False
        self.manual_contain_min_until: float = 0.0
        self.release_confirm_pending: bool = False
        self.release_confirm_until: float = 0.0
        # Manual contain/release guard rails (configurable; defaults favor immediate release).
        try:
            self.manual_contain_min_duration_sec = max(
                0.0, float(cfg.state_machine.get("manual_contain_min_duration_sec", 0.0))
            )
        except (TypeError, ValueError):
            self.manual_contain_min_duration_sec = 0.0
        try:
            self.manual_release_confirm_window_sec = max(
                0.0, float(cfg.state_machine.get("manual_release_confirm_window_sec", 0.0))
            )
        except (TypeError, ValueError):
            self.manual_release_confirm_window_sec = 0.0
        self.status_server: Optional[ThreadingHTTPServer] = None
        self.web_server: Optional[object] = None
        self.processes: Dict[str, subprocess.Popen] = {}
        self._last_dnsmasq_restart: float = 0.0
        self._legacy_dnsmasq_warned = False
        self.last_console = 0.0
        self.snapshot_paths = snapshot_path_candidates(home=Path.home())
        # Sync should only read runtime snapshots; home fallbacks can be stale across users/services.
        self.snapshot_sync_paths = [p for p in self.snapshot_paths if str(p).startswith("/run/")]
        if not self.snapshot_sync_paths:
            self.snapshot_sync_paths = self.snapshot_paths[:2]
        self.snapshot_path = self.snapshot_paths[0]
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        # Keep Wi-Fi connection state in memory to preserve across snapshots
        self.persistent_connection_state: Dict[str, object] = {}
        self.epd_last_update = 0.0
        self.epd_last_fp: Optional[tuple] = None
        # Track signal strength separately to skip updates when only signal changes
        self.epd_last_signal_bucket: Optional[str] = None
        # Failed update retry guards (avoid immediate re-run of same EPD payload).
        self.epd_last_failed_fp: Optional[tuple] = None
        self.epd_retry_after = 0.0
        self.epd_fail_count = 0
        self._eve_offset = 0
        self._eve_inode: Optional[int] = None
        self._eve_partial = ""
        self._eve_initialized = False
        self._eve_parse_errors = {"json_decode_fail": 0, "skipped_lines": 0}
        try:
            # デフォルト30秒：電子ペーパーの刷新頻度を抑制、不要な更新を削減
            self.epd_min_interval = float(os.environ.get("AZAZEL_EPD_MIN_INTERVAL", "30"))
        except ValueError:
            self.epd_min_interval = 8.0
        try:
            # 3色パネル向けに長めの既定値（秒）
            self.epd_timeout_sec = float(os.environ.get("AZAZEL_EPD_TIMEOUT", "120"))
        except ValueError:
            self.epd_timeout_sec = 120.0
        try:
            self.epd_fail_backoff_base_sec = float(
                os.environ.get("AZAZEL_EPD_FAIL_BACKOFF_BASE", str(max(30.0, self.epd_min_interval)))
            )
        except ValueError:
            self.epd_fail_backoff_base_sec = max(30.0, self.epd_min_interval)
        try:
            self.epd_fail_backoff_max_sec = float(os.environ.get("AZAZEL_EPD_FAIL_BACKOFF_MAX", "300"))
        except ValueError:
            self.epd_fail_backoff_max_sec = 300.0
        self.epd_enabled = os.environ.get("AZAZEL_EPD", "1").strip().lower() not in ("0", "false", "no", "off")
        self.epd_alerts_in_scapegoat = (
            os.environ.get("AZAZEL_EPD_ALERTS_IN_SCAPEGOAT", "0").strip().lower() in ("1", "true", "yes", "on")
        )
        try:
            self.epd_lock_wait_sec = max(0.0, float(os.environ.get("AZAZEL_EPD_LOCK_WAIT_SEC", "2.5")))
        except ValueError:
            self.epd_lock_wait_sec = 2.5
        self.health_last_update = 0.0
        self.health_last_fp: Optional[tuple] = None
        try:
            self.health_min_interval = float(os.environ.get("AZAZEL_HEALTH_INTERVAL", "20"))
        except ValueError:
            self.health_min_interval = 20.0
        # Suricata alert context
        self._last_suricata_severity: int = 0
        self._opencanary_ports: set[int] = set()
        self._opencanary_cfg_mtime_ns: Optional[int] = None
        self._canary_delay_targets: Dict[Tuple[str, int], float] = {}
        self._canary_delay_enabled = bool(self.cfg.deception.get("delay_on_canary_attack", True))
        try:
            self._canary_delay_window_sec = max(5.0, float(self.cfg.deception.get("canary_delay_window_sec", 45.0)))
        except (TypeError, ValueError):
            self._canary_delay_window_sec = 45.0
        try:
            self._canary_delay_ms = max(1, int(float(self.cfg.deception.get("canary_delay_ms", 650))))
        except (TypeError, ValueError):
            self._canary_delay_ms = 650
        try:
            self._canary_delay_jitter_ms = max(0, int(float(self.cfg.deception.get("canary_delay_jitter_ms", 120))))
        except (TypeError, ValueError):
            self._canary_delay_jitter_ms = 120
        try:
            self._canary_delay_loss_percent = max(0.0, float(self.cfg.deception.get("canary_delay_loss_percent", 0.0)))
        except (TypeError, ValueError):
            self._canary_delay_loss_percent = 0.0

    def preflight(self) -> None:
        if os.geteuid() != 0:
            raise SystemExit("First-Minute Control requires root.")
        for bin_name in ("nft", "tc", "ip"):
            if not shutil.which(bin_name):  # type: ignore[name-defined]
                raise SystemExit(f"{bin_name} not found in PATH")

    def apply_sysctl(self) -> None:
        # Use loose rp_filter for gateway mode to avoid asymmetric-route drops
        # when multiple uplinks (e.g. eth0 + wlan0) coexist.
        upstream = str(self.cfg.interfaces.get("upstream", "") or "").strip()
        downstream = str(self.cfg.interfaces.get("downstream", "") or "").strip()
        cmds = [
            ["sysctl", "-w", "net.ipv4.ip_forward=1"],
            ["sysctl", "-w", "net.ipv4.conf.all.rp_filter=2"],
            ["sysctl", "-w", "net.ipv4.conf.default.rp_filter=2"],
        ]
        for iface in {upstream, downstream}:
            if iface and self._iface_exists(iface):
                cmds.append(["sysctl", "-w", f"net.ipv4.conf.{iface}.rp_filter=2"])
        for cmd in cmds:
            subprocess.run(cmd, check=False)

    def _iface_exists(self, iface: str) -> bool:
        return bool(iface) and Path(f"/sys/class/net/{iface}").exists()

    def _is_wireless_iface(self, iface: str) -> bool:
        return bool(iface) and Path(f"/sys/class/net/{iface}/wireless").exists()

    def _iface_excluded(self, iface: str) -> bool:
        if not iface:
            return True
        for prefix in EXCLUDED_IFACE_PREFIXES:
            if iface == prefix or iface.startswith(prefix):
                return True
        return False

    def _default_routes(self) -> list[Dict[str, object]]:
        routes: list[Dict[str, object]] = []
        try:
            out = subprocess.check_output(["ip", "-j", "-4", "route", "show", "default"], text=True, timeout=2)
            raw = json.loads(out) or []
        except Exception:
            return routes
        for entry in raw:
            dev = str(entry.get("dev") or "")
            if not dev:
                continue
            try:
                metric = int(entry.get("metric", 0) or 0)
            except (TypeError, ValueError):
                metric = 0
            routes.append(
                {
                    "dev": dev,
                    "via": str(entry.get("gateway") or ""),
                    "metric": metric,
                    "line": str(entry),
                }
            )
        routes.sort(key=lambda item: (int(item.get("metric", 0)), str(item.get("dev", ""))))
        return routes

    def _collect_iface_inventory(self, exclude: Optional[set[str]] = None) -> Dict[str, Dict[str, Any]]:
        exclude = exclude or set()
        inventory: Dict[str, Dict[str, Any]] = {}
        try:
            link_raw = subprocess.check_output(["ip", "-j", "link"], text=True, timeout=2)
            link_data = json.loads(link_raw) or []
        except Exception:
            link_data = []
        try:
            addr_raw = subprocess.check_output(["ip", "-j", "-4", "addr"], text=True, timeout=2)
            addr_data = json.loads(addr_raw) or []
        except Exception:
            addr_data = []

        addr_map: Dict[str, list[str]] = {}
        for entry in addr_data:
            ifname = str(entry.get("ifname") or "")
            if not ifname:
                continue
            addrs: list[str] = []
            for info in entry.get("addr_info", []) or []:
                if info.get("family") != "inet":
                    continue
                if info.get("scope") == "host":
                    continue
                local = str(info.get("local") or "")
                if local:
                    addrs.append(local)
            addr_map[ifname] = addrs

        default_routes = self._default_routes()
        route_by_dev: Dict[str, int] = {}
        for route in default_routes:
            dev = str(route.get("dev") or "")
            if not dev:
                continue
            metric = int(route.get("metric", 0) or 0)
            if dev not in route_by_dev or metric < route_by_dev[dev]:
                route_by_dev[dev] = metric

        for entry in link_data:
            ifname = str(entry.get("ifname") or "")
            if not ifname or ifname in exclude or self._iface_excluded(ifname):
                continue
            operstate = str(entry.get("operstate") or "").upper()
            is_up = operstate == "UP"
            ips = addr_map.get(ifname, [])
            has_ipv4 = bool(ips)
            is_wireless = self._is_wireless_iface(ifname)
            has_default_route = ifname in route_by_dev
            inventory[ifname] = {
                "is_up": is_up,
                "has_ipv4": has_ipv4,
                "ipv4": ips[0] if ips else "",
                "has_default_route": has_default_route,
                "default_metric": route_by_dev.get(ifname, 10**9),
                "is_wireless": is_wireless,
            }
        return inventory

    def _sort_any_iface(self, iface: str, meta: Dict[str, Any]) -> tuple:
        return (int(meta.get("default_metric", 10**9)), iface)

    def _sort_wireless_iface(self, iface: str) -> tuple:
        return (0 if iface.startswith("wlan") else 1, iface)

    def _na_reason(self, inventory: Dict[str, Dict[str, Any]]) -> str:
        if not inventory:
            return "NOT_FOUND"
        has_up = any(bool(m.get("is_up")) for m in inventory.values())
        has_ip_ready = any(bool(m.get("is_up")) and bool(m.get("has_ipv4")) for m in inventory.values())
        if has_ip_ready:
            return "NOT_FOUND"
        if has_up:
            return "NO_IP"
        return "LINK_DOWN"

    def _default_gateway_for_iface(self, iface: str) -> str:
        for route in self._default_routes():
            if route.get("dev") == iface and route.get("via"):
                return str(route["via"])
        return ""

    def _detect_best_upstream(self) -> str:
        downstream = str(self.cfg.interfaces.get("downstream", "usb0") or "usb0")
        configured = str(self.cfg.interfaces.get("upstream", "") or "").strip()
        auto_mode = configured.lower() in ("", "auto")
        inventory = self._collect_iface_inventory(exclude={downstream})

        if not auto_mode and configured in inventory:
            meta = inventory.get(configured, {})
            if bool(meta.get("is_up")) and bool(meta.get("has_ipv4")):
                return configured

        route_ready: list[tuple[str, Dict[str, Any]]] = []
        for iface, meta in inventory.items():
            if bool(meta.get("is_up")) and bool(meta.get("has_ipv4")) and bool(meta.get("has_default_route")):
                route_ready.append((iface, meta))
        if route_ready:
            # In auto mode, prefer wireless uplink when both wired/wireless routes exist.
            wireless_route_ready = [(iface, meta) for iface, meta in route_ready if bool(meta.get("is_wireless"))]
            if wireless_route_ready:
                wireless_route_ready.sort(
                    key=lambda item: (
                        self._sort_wireless_iface(item[0]),
                        int(item[1].get("default_metric", 10**9)),
                        item[0],
                    )
                )
                return wireless_route_ready[0][0]
            route_ready.sort(key=lambda item: self._sort_any_iface(item[0], item[1]))
            return route_ready[0][0]

        if not auto_mode and configured in inventory:
            return configured

        ip_ready: list[tuple[str, Dict[str, Any]]] = []
        for iface, meta in inventory.items():
            if bool(meta.get("is_up")) and bool(meta.get("has_ipv4")):
                ip_ready.append((iface, meta))
        if ip_ready:
            ip_ready.sort(key=lambda item: self._sort_any_iface(item[0], item[1]))
            return ip_ready[0][0]

        if not auto_mode and configured and self._iface_exists(configured):
            return configured

        if inventory:
            return sorted(inventory.keys())[0]

        if configured and configured.lower() != "auto":
            return configured
        return ""

    def resolve_captive_probe_iface(self) -> Dict[str, str]:
        configured = str(self.cfg.interfaces.get("captive_probe", "auto") or "auto").strip()
        upstream = str(self.cfg.interfaces.get("upstream", "") or "").strip()
        downstream = str(self.cfg.interfaces.get("downstream", "usb0") or "usb0")
        policy_raw = str(getattr(self.cfg, "captive_probe_policy", "wifi_prefer") or "wifi_prefer").strip().lower()
        policy = policy_raw if policy_raw in ("wifi_prefer", "upstream_same", "any") else "wifi_prefer"
        inventory = self._collect_iface_inventory(exclude={downstream})
        source = "config" if configured.lower() not in ("", "auto") else "auto"

        if source == "config":
            meta = inventory.get(configured)
            if meta and bool(meta.get("is_up")) and bool(meta.get("has_ipv4")):
                return {"iface": configured, "reason": "OK", "src": source, "policy": policy}
            if not meta:
                return {"iface": "", "reason": "NOT_FOUND", "src": source, "policy": policy}
            return {
                "iface": "",
                "reason": "LINK_DOWN" if not bool(meta.get("is_up")) else "NO_IP",
                "src": source,
                "policy": policy,
            }

        candidates: list[tuple[str, Dict[str, Any]]] = []
        if policy == "upstream_same":
            meta = inventory.get(upstream)
            if meta and bool(meta.get("is_up")) and bool(meta.get("has_ipv4")):
                return {"iface": upstream, "reason": "OK", "src": source, "policy": policy}
            if not meta:
                return {"iface": "", "reason": "NOT_FOUND", "src": source, "policy": policy}
            return {
                "iface": "",
                "reason": "LINK_DOWN" if not bool(meta.get("is_up")) else "NO_IP",
                "src": source,
                "policy": policy,
            }

        if policy == "wifi_prefer":
            for iface, meta in inventory.items():
                if bool(meta.get("is_wireless")) and bool(meta.get("is_up")) and bool(meta.get("has_ipv4")):
                    candidates.append((iface, meta))
            if candidates:
                candidates.sort(key=lambda item: self._sort_wireless_iface(item[0]))
                return {"iface": candidates[0][0], "reason": "OK", "src": source, "policy": policy}

            fallback_route: list[tuple[str, Dict[str, Any]]] = []
            for iface, meta in inventory.items():
                if bool(meta.get("is_up")) and bool(meta.get("has_ipv4")) and bool(meta.get("has_default_route")):
                    fallback_route.append((iface, meta))
            if fallback_route:
                fallback_route.sort(key=lambda item: self._sort_any_iface(item[0], item[1]))
                selected = fallback_route[0][0]
                return {
                    "iface": selected,
                    "reason": f"fallback_to_{selected}",
                    "src": source,
                    "policy": policy,
                }

            fallback_up_ip: list[tuple[str, Dict[str, Any]]] = []
            for iface, meta in inventory.items():
                if bool(meta.get("is_up")) and bool(meta.get("has_ipv4")):
                    fallback_up_ip.append((iface, meta))
            if fallback_up_ip:
                fallback_up_ip.sort(key=lambda item: self._sort_any_iface(item[0], item[1]))
                selected = fallback_up_ip[0][0]
                return {
                    "iface": selected,
                    "reason": f"fallback_to_{selected}",
                    "src": source,
                    "policy": policy,
                }
            return {"iface": "", "reason": self._na_reason(inventory), "src": source, "policy": policy}

        for iface, meta in inventory.items():
            if bool(meta.get("is_up")) and bool(meta.get("has_ipv4")) and bool(meta.get("has_default_route")):
                candidates.append((iface, meta))
        if candidates:
            candidates.sort(key=lambda item: self._sort_any_iface(item[0], item[1]))
            return {"iface": candidates[0][0], "reason": "OK", "src": source, "policy": policy}
        candidates = []
        for iface, meta in inventory.items():
            if bool(meta.get("is_up")) and bool(meta.get("has_ipv4")):
                candidates.append((iface, meta))
        if candidates:
            candidates.sort(key=lambda item: self._sort_any_iface(item[0], item[1]))
            return {"iface": candidates[0][0], "reason": "OK", "src": source, "policy": policy}
        return {"iface": "", "reason": self._na_reason(inventory), "src": source, "policy": policy}

    def _refresh_captive_probe_iface(self) -> Dict[str, str]:
        resolved = self.resolve_captive_probe_iface()
        iface = resolved.get("iface", "")
        reason = resolved.get("reason", "NOT_FOUND")
        policy = resolved.get("policy", "wifi_prefer")
        source = resolved.get("src", "auto")

        prev_iface = getattr(self, "_resolved_captive_probe_iface", "")
        prev_reason = getattr(self, "_resolved_captive_probe_reason", "")
        changed = (iface != prev_iface) or (reason != prev_reason)

        self._resolved_captive_probe_iface = iface
        self._resolved_captive_probe_reason = reason
        if changed:
            if iface:
                self.logger.info("captive_probe_iface resolved: %s (policy=%s, src=%s)", iface, policy, source)
            else:
                self.logger.info("skip captive probe: reason=%s", reason)

        if hasattr(self, "status_ctx_lock"):
            with self.status_ctx_lock:
                self.status_ctx["captive_probe_iface"] = iface or ""
        return resolved

    def _rebuild_network_managers(self) -> None:
        upstream = self.cfg.interfaces.get("upstream", "")
        downstream = self.cfg.interfaces["downstream"]
        self.nft = NftManager(
            self.cfg.nft_template_path,
            upstream,
            downstream,
            self.cfg.interfaces["mgmt_ip"],
            self.cfg.interfaces["mgmt_subnet"],
            int(self.cfg.policy.get("probe_allow_ttl", 120)),
            int(self.cfg.policy.get("dynamic_allow_ttl", 300)),
        )
        self.tc = TcManager(downstream, upstream)

    def _refresh_upstream_iface(self, force: bool = False, reapply_rules: bool = False) -> bool:
        selected = self._detect_best_upstream()
        current = str(self.cfg.interfaces.get("upstream", "") or "")
        if not selected:
            return False
        if not force and selected == current:
            return False

        self.cfg.interfaces["upstream"] = selected
        self._rebuild_network_managers()
        self.logger.info("upstream interface updated: %s -> %s", current or "-", selected)

        if hasattr(self, "status_ctx_lock"):
            with self.status_ctx_lock:
                self.status_ctx["upstream_if"] = selected

        if reapply_rules and not self.dry_run:
            try:
                self.nft.apply_base()
                stage = self.current_stage
                if stage not in (Stage.PROBE, Stage.DEGRADED, Stage.NORMAL, Stage.CONTAIN, Stage.DECEPTION):
                    stage = Stage.NORMAL
                self.apply_stage(stage)
                self.seed_probe_destinations()
            except Exception as exc:
                self.logger.warning("failed to reapply rules after uplink change: %s", exc)

        return True

    def start_dnsmasq(self) -> None:
        if self.no_dns_start or not self.cfg.dnsmasq.get("enable", True):
            self.logger.info("dnsmasq start skipped (--no-dns-start or disabled in config)")
            return

        self._stop_conflicting_dnsmasq_service()

        existing = self.processes.get("dnsmasq")
        if existing and existing.poll() is None:
            self.logger.debug("dnsmasq already running (pid=%s)", existing.pid)
            return
        if existing and existing.poll() is not None:
            self.processes.pop("dnsmasq", None)
        
        # コンフィグファイル存在確認
        conf_path = self.cfg.dnsmasq_conf_path
        if not conf_path.exists():
            self.logger.error(f"dnsmasq config not found: {conf_path}")
            return
        
        # usb0インターフェースが UP していることを確認
        downstream = self.cfg.interfaces.get("downstream", "usb0")
        try:
            result = subprocess.run(["ip", "link", "show", downstream], capture_output=True, text=True, timeout=3)
            if result.returncode != 0:
                self.logger.warning(f"Interface {downstream} not found yet. Retrying...")
                # 再試行：インターフェースが起動するまで待機
                for attempt in range(10):
                    time.sleep(0.5)
                    result = subprocess.run(["ip", "link", "show", downstream], capture_output=True, text=True, timeout=3)
                    if result.returncode == 0:
                        self.logger.info(f"Interface {downstream} is now available")
                        break
                else:
                    self.logger.error(f"Interface {downstream} did not appear after 5 seconds")
                    return
            
            # インターフェースがUPしているか確認
            if "UP" not in result.stdout:
                self.logger.warning(f"Interface {downstream} exists but is not UP. Bringing up...")
                subprocess.run(["ip", "link", "set", downstream, "up"], timeout=3)
        except Exception as e:
            self.logger.warning(f"Could not verify interface {downstream}: {e}")
        
        # dnsmasq は foreground で起動し、controller でライフサイクルを管理する
        cmd = ["dnsmasq", "--keep-in-foreground", f"--conf-file={conf_path}"]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                universal_newlines=True,
                bufsize=1,
            )
            self.processes["dnsmasq"] = proc
            
            # ログ出力をリアルタイムで読み込むスレッド
            def read_dnsmasq_output():
                try:
                    while True:
                        line = proc.stdout.readline()
                        if not line:
                            break
                        if line.strip():
                            self.logger.debug(f"[dnsmasq] {line.rstrip()}")
                except Exception:
                    pass
            
            output_thread = threading.Thread(target=read_dnsmasq_output, daemon=True)
            output_thread.start()

            # 起動直後に落ちるケース（port競合など）を明示的に検知
            time.sleep(0.5)
            if proc.poll() is not None:
                self.logger.error(
                    f"dnsmasq exited immediately (rc={proc.returncode}) using config: {conf_path}"
                )
                self.processes.pop("dnsmasq", None)
                return

            self.logger.info(f"dnsmasq started with PID {proc.pid} using config: {conf_path}")
        except FileNotFoundError:
            self.logger.error("dnsmasq binary not found. Install with: apt-get install dnsmasq")
        except Exception as e:
            self.logger.error(f"Failed to start dnsmasq: {e}")

    def _stop_conflicting_dnsmasq_service(self) -> None:
        # Avoid port conflicts with the distro-managed dnsmasq.service.
        if self.dry_run or not shutil.which("systemctl"):
            return

        try:
            active = subprocess.run(
                ["systemctl", "is-active", "--quiet", "dnsmasq.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode == 0
            if not active:
                return

            if not self._legacy_dnsmasq_warned:
                self.logger.warning(
                    "dnsmasq.service is active; stopping/disabling it to avoid first-minute DNS/DHCP conflicts"
                )
                self._legacy_dnsmasq_warned = True

            subprocess.run(
                ["systemctl", "stop", "dnsmasq.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            subprocess.run(
                ["systemctl", "disable", "dnsmasq.service"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except Exception as exc:
            self.logger.debug("failed to stop conflicting dnsmasq.service: %s", exc)

    def ensure_dnsmasq_running(self) -> None:
        if self.dry_run or self.no_dns_start or not self.cfg.dnsmasq.get("enable", True):
            return

        proc = self.processes.get("dnsmasq")
        if proc and proc.poll() is None:
            return

        now = time.time()
        if now - self._last_dnsmasq_restart < 5.0:
            return

        rc = proc.poll() if proc else "none"
        self.logger.warning("dnsmasq is not running (rc=%s), attempting restart", rc)
        self._last_dnsmasq_restart = now
        self.start_dnsmasq()

    def stop_dnsmasq(self) -> None:
        proc = self.processes.get("dnsmasq")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        # 旧実装で daemonize されたプロセスが残っている場合の掃除
        subprocess.run(
            ["pkill", "-f", f"dnsmasq.*{self.cfg.dnsmasq_conf_path.name}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

    def start_dns_observer(self) -> None:
        self.dns_thread = DNSObserver(self.cfg.dns_log_path, self.nft, self.stop_event)
        self.dns_thread.start()

    def start_status_api(self) -> None:
        host = self.cfg.status_api.get("host", "127.0.0.1")
        port = int(self.cfg.status_api.get("port", 8082))
        # ステータス API（JSON）
        self.status_server = make_status_server(host, port, self.status_ctx, self.status_ctx_lock)
        thread = threading.Thread(target=self.status_server.serve_forever, daemon=True)
        thread.start()
        self.logger.info(f"Status API started on {host}:{port}")

    def _default_connection_state(self) -> Dict[str, object]:
        return {
            "wifi_state": "DISCONNECTED",
            "usb_nat": "OFF",
            "internet_check": "N/A",
            "ssid": "",
            "ip_wlan": "",
            "gateway_ip": "",
            "bssid": "",
            "captive_probe_iface": self._resolved_captive_probe_iface or "",
            "captive_portal": "NA",
            "captive_portal_reason": self._resolved_captive_probe_reason if not self._resolved_captive_probe_iface else "NOT_CHECKED",
            "captive_checked_at": "",
            "captive_portal_url": "",
            "captive_probe_url": "",
            "captive_effective_url": "",
            "captive_location": "",
            "captive_portal_detail": {
                "status": "NA",
                "reason": self._resolved_captive_probe_reason if not self._resolved_captive_probe_iface else "NOT_CHECKED",
                "checked_at": "",
                "portal_url": "",
                "probe_url": "",
                "effective_url": "",
                "location": "",
            },
        }

    @staticmethod
    def _normalize_http_url(candidate: object) -> str:
        text = str(candidate or "").strip()
        if not text or any(ch in text for ch in ("\r", "\n")):
            return ""
        parsed = urlparse(text)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return ""
        return text

    def _choose_portal_url(self, status: str, detail: Dict[str, object]) -> str:
        if str(status or "").upper() not in {"YES", "SUSPECTED"}:
            return ""
        for key in ("location", "effective_url", "url"):
            normalized = self._normalize_http_url(detail.get(key, ""))
            if normalized:
                return normalized
        return ""

    @staticmethod
    def _derive_internet_check(wifi_state: object, captive_status: object, current: object) -> str:
        """Derive Internet status from latest captive-portal result."""
        wifi = str(wifi_state or "").upper()
        captive = str(captive_status or "").upper()
        prev = str(current or "").upper()

        if wifi == "DISCONNECTED":
            return "N/A"
        if wifi != "CONNECTED":
            return prev if prev else "UNKNOWN"
        if captive == "NO":
            return "OK"
        if captive in {"YES", "SUSPECTED"}:
            return "FAIL"
        return prev if prev else "UNKNOWN"

    def _normalize_connection_state(self, raw: Optional[Dict[str, object]]) -> Dict[str, object]:
        normalized = self._default_connection_state()
        if isinstance(raw, dict):
            normalized.update(raw)

        wifi_state = str(normalized.get("wifi_state", "DISCONNECTED") or "DISCONNECTED")
        normalized["wifi_state"] = wifi_state
        if wifi_state == "DISCONNECTED":
            for key in ("ssid", "ip_wlan", "gateway_ip", "bssid"):
                normalized[key] = ""
            normalized["usb_nat"] = "OFF"
            if not normalized.get("internet_check"):
                normalized["internet_check"] = "N/A"
            normalized["captive_portal_url"] = ""
            normalized["captive_probe_url"] = ""
            normalized["captive_effective_url"] = ""
            normalized["captive_location"] = ""

        if not normalized.get("captive_probe_iface"):
            normalized["captive_probe_iface"] = self._resolved_captive_probe_iface or ""

        if self.last_probe:
            normalized["captive_probe_iface"] = self.last_probe.captive_iface or str(normalized.get("captive_probe_iface") or "")
            normalized["captive_portal"] = self.last_probe.captive_status
            normalized["captive_portal_reason"] = self.last_probe.captive_reason
            normalized["captive_checked_at"] = self.last_probe.captive_checked_at
            captive_detail = self.last_probe.details.get("captive", {}) if isinstance(self.last_probe.details, dict) else {}
            if isinstance(captive_detail, dict):
                normalized["captive_probe_url"] = str(captive_detail.get("url", "") or "")
                normalized["captive_effective_url"] = str(captive_detail.get("effective_url", "") or "")
                normalized["captive_location"] = str(captive_detail.get("location", "") or "")
                portal_url = self._choose_portal_url(self.last_probe.captive_status, captive_detail)
                if portal_url:
                    normalized["captive_portal_url"] = portal_url

        if not normalized.get("captive_portal"):
            normalized["captive_portal"] = "NA"
        if not normalized.get("captive_portal_reason"):
            normalized["captive_portal_reason"] = self._resolved_captive_probe_reason or "NOT_CHECKED"
        if not normalized.get("captive_checked_at"):
            normalized["captive_checked_at"] = ""
        if not normalized.get("captive_portal_url"):
            normalized["captive_portal_url"] = ""
        if not normalized.get("captive_probe_url"):
            normalized["captive_probe_url"] = ""
        if not normalized.get("captive_effective_url"):
            normalized["captive_effective_url"] = ""
        if not normalized.get("captive_location"):
            normalized["captive_location"] = ""

        normalized["internet_check"] = self._derive_internet_check(
            normalized.get("wifi_state", ""),
            normalized.get("captive_portal", "NA"),
            normalized.get("internet_check", ""),
        )

        normalized["captive_portal_detail"] = {
            "status": normalized.get("captive_portal", "NA"),
            "reason": normalized.get("captive_portal_reason", "NOT_CHECKED"),
            "checked_at": normalized.get("captive_checked_at", ""),
            "portal_url": normalized.get("captive_portal_url", ""),
            "probe_url": normalized.get("captive_probe_url", ""),
            "effective_url": normalized.get("captive_effective_url", ""),
            "location": normalized.get("captive_location", ""),
        }
        return normalized

    def write_snapshot(self, summary: Dict[str, object], link_meta: Dict[str, object], skip_sync: bool = False) -> None:
        """Write a UI snapshot JSON for the TUI to consume."""
        # Step 1: Update persistent connection state from any available file
        # This ensures we capture any updates from wifi_connect.py
        if not skip_sync:
            self._sync_connection_state()
        self.logger.info("snapshot: write_snapshot() called with new persistent state logic")
        
        link = link_meta.get("link", {}) if link_meta else {}
        
        # Gather system metrics
        cpu_temp = self._get_cpu_temp()
        cpu_usage = self._get_cpu_usage()
        mem_usage = self._get_memory_usage()
        
        # Gather Wi-Fi channel scan data
        channel_congestion = "unknown"
        channel_ap_count = 0
        if self._is_wireless_iface(self.cfg.interfaces.get("upstream", "")):
            try:
                from azazel_gadget.sensors.wifi_channel_scanner import scan_wifi_channels
                scan_result = scan_wifi_channels(self.cfg.interfaces.get("upstream", ""))
                if scan_result.get("scan_success"):
                    channel_congestion = scan_result.get("congestion_level", "unknown")
                    channel_ap_count = scan_result.get("ap_count", 0)
            except Exception as e:
                self.logger.debug(f"snapshot: Wi-Fi channel scan failed: {e}")
        
        # Gather Suricata alerts from eve.json (if available)
        suricata_critical = 0
        suricata_warning = 0
        try:
            eve_log = Path("/var/log/suricata/eve.json")
            if eve_log.exists():
                # Read the last 50 lines from eve.json and count alert events by severity
                with open(eve_log, "r") as f:
                    # Seek to end and read backwards
                    lines = f.readlines()[-50:]
                
                for line in lines:
                    try:
                        event = json.loads(line)
                        if event.get("event_type") == "alert":
                            # Alert severity: 1=critical, 2=warning, 3=notice
                            severity = event.get("alert", {}).get("severity", 3)
                            if severity == 1:
                                suricata_critical += 1
                            elif severity == 2:
                                suricata_warning += 1
                    except (json.JSONDecodeError, KeyError):
                        continue
        except Exception as e:
            self.logger.debug(f"snapshot: Suricata alerts gathering failed: {e}")
        
        attack_summary: Dict[str, object] = {
            "suricata_alert": False,
            "suricata_severity": 0,
            "canary_target_alert": False,
            "canary_delay_active": False,
            "canary_delay_target_count": 0,
            "canary_delay_targets": [],
        }
        try:
            with self.status_ctx_lock:
                last_signals = self.status_ctx.get("last_signals", {})
                if not isinstance(last_signals, dict):
                    last_signals = {}
                canary_delay_targets = self.status_ctx.get("canary_delay_targets", [])
                if not isinstance(canary_delay_targets, list):
                    canary_delay_targets = []
                attack_summary = {
                    "suricata_alert": bool(last_signals.get("suricata_alert", False)),
                    "suricata_severity": int(last_signals.get("suricata_severity", 0) or 0),
                    "canary_target_alert": bool(last_signals.get("canary_target_alert", False)),
                    "canary_delay_active": bool(self.status_ctx.get("canary_delay_active", False)),
                    "canary_delay_target_count": int(self.status_ctx.get("canary_delay_target_count", 0) or 0),
                    "canary_delay_targets": [str(item) for item in canary_delay_targets],
                }
        except Exception:
            pass

        snap = {
            "now_time": time.strftime("%H:%M:%S"),
            "snapshot_epoch": time.time(),
            "ssid": (link or {}).get("ssid", "-"),
            "bssid": (link or {}).get("bssid", "-"),
            "channel": (link or {}).get("channel", "-"),
            "signal_dbm": (link or {}).get("signal", "-"),
            "gateway_ip": (link or {}).get("gateway") or self.cfg.interfaces.get("gateway_ip", "-"),
            "down_if": self.cfg.interfaces.get("downstream", "usb0"),
            "down_ip": self.cfg.interfaces.get("mgmt_ip", "-"),
            "up_if": self.cfg.interfaces.get("upstream", ""),
            "user_state": self._user_state_from_stage(self.current_stage),
            "recommendation": summary.get("reason", "Checking"),
            "reasons": [summary.get("reason", "")] if summary.get("reason") else [],
            "next_action_hint": "Waiting for re-evaluation",
            "quic": "blocked" if self.current_stage in (Stage.PROBE, Stage.DEGRADED, Stage.CONTAIN) else "allowed",
            "doh": "blocked",
            "dns_mode": "forced via Azazel DNS",
            "degrade": {
                "on": self.current_stage in (Stage.PROBE, Stage.DEGRADED),
                "rtt_ms": 180 if self.current_stage in (Stage.PROBE, Stage.DEGRADED) else 0,
                "rate_mbps": 2.0 if self.current_stage == Stage.DEGRADED else 1.0 if self.current_stage == Stage.PROBE else 0,
            },
            "probe": {
                "tls_ok": (self.last_probe.tls_mismatch is False) if self.last_probe else 0,
                "tls_total": 1 if self.last_probe else 0,
                "blocked": 1 if (self.last_probe and self.last_probe.tls_mismatch) else 0,
            },
            "evidence": self._evidence_lines(link_meta),
            "internal": {
                "state_name": self.state_machine.ctx.state.value,
                "suspicion": summary.get("suspicion", 0),
                "decay": self.state_machine.ctx.last_transition,
            },
            # System metrics (shared with Web UI)
            "cpu_percent": cpu_usage,
            "mem_percent": mem_usage,
            "temp_c": cpu_temp,
            # Channel metrics (from Wi-Fi scan)
            "channel_congestion": channel_congestion,
            "channel_ap_count": channel_ap_count,
            # Suricata IDS alerts
            "suricata_critical": suricata_critical,
            "suricata_warning": suricata_warning,
            # Attack/deception context shared across WebUI/TUI/EPD.
            "attack": attack_summary,
            # Default connection section (will be overwritten with persisted state below)
            "connection": self._default_connection_state(),
        }
        try:
            self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Preserve Wi-Fi connection state from previous snapshot
            # This allows wifi_connect.py to update connection info without being overwritten
            # Check both primary and fallback paths to ensure we don't lose data
            existing_connection = None
            for snap_path in self.snapshot_sync_paths:
                try:
                    if not snap_path.exists():
                        continue
                    warn_if_legacy_path(snap_path, logger=self.logger)
                    existing = json.loads(snap_path.read_text(encoding="utf-8"))
                    existing_connection = existing.get("connection")
                    if existing_connection:
                        self.logger.debug(
                            f"snapshot: preserving connection state from {snap_path}: {existing_connection}"
                        )
                        break
                except Exception as e:
                    self.logger.debug(f"snapshot: failed to read {snap_path}: {e}")
            
            # Merge persistent connection state (from memory or file)
            # This ensures Wi-Fi connection data is never lost across snapshot writes
            if self.persistent_connection_state:
                snap["connection"] = self._normalize_connection_state(self.persistent_connection_state.copy())
                self.logger.debug(f"snapshot: using persistent connection state (from memory): {self.persistent_connection_state}")
            elif existing_connection:
                snap["connection"] = self._normalize_connection_state(existing_connection.copy())
                self.logger.debug(f"snapshot: loaded connection state from file: {existing_connection}")
            else:
                snap["connection"] = self._normalize_connection_state(None)
                self.logger.debug("snapshot: no connection state available")
            self.persistent_connection_state = snap["connection"].copy()
            
            # Write snapshot to runtime paths only to avoid stale home snapshot bleed-through.
            payload_text = json.dumps(snap, ensure_ascii=False)
            for snap_path in self.snapshot_sync_paths:
                try:
                    snap_path.parent.mkdir(parents=True, exist_ok=True)
                    tmp_path = snap_path.with_suffix(snap_path.suffix + ".tmp")
                    tmp_path.write_text(payload_text, encoding="utf-8")
                    os.replace(tmp_path, snap_path)
                    warn_if_legacy_path(snap_path, logger=self.logger)
                except Exception as e:
                    self.logger.debug(f"snapshot: failed to write {snap_path}: {e}")
        except Exception as e:
            self.logger.warning(f"snapshot: failed overall: {e}")

    def _sync_connection_state(self) -> None:
        """Sync persistent connection state from files.
        
        This reads from both the primary and fallback snapshot paths to detect
        when wifi_connect.py has written new connection data. This is essential
        because write_snapshot() executes every 2 seconds and would otherwise
        overwrite the connection section that wifi_connect.py just wrote.
        """
        self.logger.debug(f"sync: checking for connection state updates (current: {self.persistent_connection_state})")
        
        for path in self.snapshot_sync_paths:
            try:
                if path.exists():
                    warn_if_legacy_path(path, logger=self.logger)
                    data = json.loads(path.read_text(encoding="utf-8"))
                    conn = data.get("connection")
                    self.logger.debug(f"sync: read from {path}: {conn}")
                    
                    # If we find new/different connection data, use it
                    if conn:
                        # Convert to tuple for comparison (dicts are unhashable)
                        current_tuple = tuple(sorted(self.persistent_connection_state.items())) if self.persistent_connection_state else ()
                        incoming_tuple = tuple(sorted(conn.items()))
                        
                        if current_tuple != incoming_tuple:
                            self.persistent_connection_state = conn.copy()
                            self.logger.info(f"sync: UPDATED persistent state from {path}: {conn}")
                            return  # Found and updated, exit
                        else:
                            self.logger.debug(f"sync: connection unchanged from {path}")
                    else:
                        self.logger.debug(f"sync: no connection section in {path}")
            except Exception as e:
                self.logger.debug(f"sync: failed to read {path}: {e}")

    def _get_interface_ip(self, iface: str) -> str:
        if not iface:
            return "-"
        try:
            out = subprocess.check_output(["ip", "-4", "addr", "show", iface], text=True, timeout=1.5)
        except Exception:
            return "-"
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                return line.split()[1].split("/")[0]
        return "-"

    def _parse_signal_dbm(self, signal: object) -> Optional[int]:
        if signal is None:
            return None
        try:
            val = int(float(str(signal).strip()))
        except Exception:
            return None
        if 0 <= val <= 100:
            return int(val * 0.6 - 90)
        return val

    def _epd_signal_bucket(self, signal_dbm: Optional[int]) -> str:
        if signal_dbm is None:
            return "none"
        if signal_dbm >= -60:
            return "strong"
        if signal_dbm >= -70:
            return "medium"
        if signal_dbm >= -80:
            return "weak"
        return "none"

    def _epd_fingerprint(
        self,
        mode: str,
        ssid: str,
        up_ip: str,
        signal_bucket: str,
        mode_label: str,
        msg: str,
    ) -> tuple:
        # For NORMAL state: only SSID and IP matter; signal strength changes don't warrant refresh
        if mode == "normal":
            return (mode, ssid, up_ip, mode_label)
        # For other states: include message
        return (mode, msg)

    def _get_current_mode_label(self) -> str:
        """Read current gateway mode label from mode.json (best-effort)."""
        candidates = [
            Path("/etc/azazel/mode.json"),
            Path("/etc/azazel-gadget/mode.json"),
            Path("/etc/azazel-zero/mode.json"),
        ]
        for path in candidates:
            try:
                if not path.exists():
                    continue
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                mode = str(data.get("current_mode", "") or "").strip().upper()
                if mode in {"PORTAL", "SHIELD", "SCAPEGOAT"}:
                    return mode
            except Exception:
                continue
        return "SHIELD"

    def _is_opencanary_on(self) -> bool:
        """Best-effort runtime check for OpenCanary process/service state."""
        try:
            for unit in ("opencanary@az_canary.service", "opencanary.service"):
                res = subprocess.run(
                    ["systemctl", "is-active", unit],
                    capture_output=True,
                    text=True,
                    timeout=2.0,
                    check=False,
                )
                if res.returncode == 0 and res.stdout.strip() == "active":
                    return True
            proc = subprocess.run(
                ["pgrep", "-f", "[o]pencanary.tac"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            return proc.returncode == 0
        except Exception:
            return False

    def _get_risk_status(self, stage: Stage) -> str:
        """Map stage to risk status string for EPD display."""
        status_map = {
            Stage.NORMAL: "SAFE",
            Stage.INIT: "CHECKING",
            Stage.PROBE: "CHECKING",
            Stage.DEGRADED: "LIMITED",
            Stage.CONTAIN: "CONTAINED",
            Stage.DECEPTION: "DECEPTION"
        }
        return status_map.get(stage, "UNKNOWN")
    
    def _get_risk_status(self, stage: Stage) -> str:
        """Map stage to risk status string for EPD display."""
        status_map = {
            Stage.NORMAL: "SAFE",
            Stage.INIT: "CHECKING",
            Stage.PROBE: "CHECKING",
            Stage.DEGRADED: "LIMITED",
            Stage.CONTAIN: "CONTAINED",
            Stage.DECEPTION: "DECEPTION"
        }
        return status_map.get(stage, "UNKNOWN")

    def _maybe_update_epd(self, stage: Stage, summary: Dict[str, object], link_meta: Dict[str, object], force: bool = False) -> None:
        if self.dry_run or not self.epd_enabled:
            return
        now = time.time()
        if not force and (now - self.epd_last_update) < self.epd_min_interval:
            return

        link = link_meta.get("link", {}) if link_meta else {}
        up_ip = self._get_interface_ip(self.cfg.interfaces.get("upstream", ""))
        reason = str(summary.get("reason", "") or "")

        epd_script = Path(__file__).resolve().parents[2] / "azazel_epd.py"
        if not epd_script.exists():
            return

        # link_meta は接続状態を反映している。未接続時は EPD 入力を固定値にする。
        # これにより、未接続中の iface IP 揺らぎで不要更新されるのを防ぐ。
        connected = str(link.get("connected", "0")) == "1"
        if connected:
            ssid = str(link.get("ssid") or "No SSID")
            signal_dbm = self._parse_signal_dbm(link.get("signal"))
            signal_bucket = self._epd_signal_bucket(signal_dbm)
            epd_ip = up_ip if up_ip and up_ip != "-" else "No IP"
        else:
            ssid = "No SSID"
            signal_dbm = None
            signal_bucket = "none"
            epd_ip = "No IP"
        
        # EPD表示用メッセージを作成
        if stage == Stage.DEGRADED:
            # WARNING画面は上段固定で "CAUTION" を表示するため、
            # ここでは下段2行目に載せる「理由」を優先して渡す。
            if "contain" in reason.lower() and "degraded" in reason.lower():
                msg = "RECOVERED"  # CONTAIN→DEGRADED の場合
            elif reason:
                msg = reason
            else:
                msg = "LIMITED"
        else:
            msg = (reason or stage.value)[:12]  # その他のステートも12文字制限

        # Get risk assessment (from suspicion score and stage)
        suspicion = int(summary.get("suspicion", 0))
        risk_status = self._get_risk_status(stage)
        mode_label = self._get_current_mode_label()

        # In SCAPEGOAT mode, keep base EPD screen (mode/SSID) and suppress alert-style
        # stage rendering from first-minute path. Mode refresh pipeline owns the screen.
        if (
            mode_label == "SCAPEGOAT"
            and stage in (Stage.DEGRADED, Stage.CONTAIN, Stage.DECEPTION)
            and not self.epd_alerts_in_scapegoat
        ):
            self.logger.debug("EPD: Skipping stage alert render in SCAPEGOAT mode (stage=%s)", stage.value)
            return
        
        if stage in (Stage.INIT, Stage.PROBE, Stage.NORMAL):
            mode = "normal"
            # フィンガープリント：信号強度を除外、risk_statusとsuspicionを含む
            fp = self._epd_fingerprint(mode, ssid, epd_ip, risk_status, mode_label, str(suspicion))
            
            self.logger.debug(f"EPD: fingerprint check - current={fp}, last={self.epd_last_fp}, match={fp == self.epd_last_fp}")
            self.logger.debug(f"EPD: signal_bucket - current={signal_bucket}, last={self.epd_last_signal_bucket}")
            
            # 主要な状態が変わったかチェック
            if not force and fp == self.epd_last_fp:
                # 主要な状態（SSID/IP/stage/risk）は変わっていない
                if signal_bucket == self.epd_last_signal_bucket:
                    # 信号強度のアイコンも変わっていない → 更新スキップ
                    self.logger.debug(f"EPD: Skipping update - no meaningful changes")
                    return
                else:
                    # 信号強度のアイコンが変わった（例：strong→medium）
                    self.logger.info(f"EPD: Updating display - signal icon changed ({self.epd_last_signal_bucket}→{signal_bucket})")
                    # ここで更新処理に進む（下記のcmd実行へ）
            cmd = [
                "python3", str(epd_script),
                "--state", mode,
                "--ssid", ssid,
                "--mode-label", mode_label,
                "--risk-status", risk_status,
                "--suspicion", str(suspicion),
            ]
            if signal_dbm is not None:
                cmd += ["--signal", str(signal_dbm)]
        elif stage == Stage.DEGRADED:
            mode = "warning"
            fp = self._epd_fingerprint(mode, "", "", "", "", msg)
            self.logger.debug(f"EPD: fingerprint check - current={fp}, last={self.epd_last_fp}, match={fp == self.epd_last_fp}")
            if not force and fp == self.epd_last_fp:
                self.logger.debug(f"EPD: Skipping update - fingerprint unchanged")
                return
            cmd = ["python3", str(epd_script), "--state", mode, "--msg", msg]
        elif stage == Stage.CONTAIN:
            mode = "danger"
            # ★ Phase 2: CONTAIN状態で統一メッセージを表示
            contain_msg = "ATTACK DETECTED"
            fp = self._epd_fingerprint(mode, "", "", "", "", contain_msg)
            self.logger.debug(f"EPD: fingerprint check - current={fp}, last={self.epd_last_fp}, match={fp == self.epd_last_fp}")
            if not force and fp == self.epd_last_fp:
                self.logger.debug(f"EPD: Skipping update - fingerprint unchanged")
                return
            cmd = ["python3", str(epd_script), "--state", mode, "--msg", contain_msg]
        elif stage == Stage.DECEPTION:
            mode = "danger"
            # Deception stage indicates active hostile activity under canary decoy.
            delay_active = bool(self._active_canary_delay_targets(now))
            deception_msg = "DELAY ACTIVE" if delay_active else "DECEPTION MODE"
            fp = self._epd_fingerprint(mode, "", "", "", "", deception_msg)
            self.logger.debug(f"EPD: fingerprint check - current={fp}, last={self.epd_last_fp}, match={fp == self.epd_last_fp}")
            if not force and fp == self.epd_last_fp:
                self.logger.debug(f"EPD: Skipping update - fingerprint unchanged")
                return
            cmd = ["python3", str(epd_script), "--state", mode, "--msg", deception_msg]
        else:
            self.logger.debug(f"EPD: Unknown stage {stage}, skipping update")
            return

        if not force and fp == self.epd_last_failed_fp and now < self.epd_retry_after:
            remaining = self.epd_retry_after - now
            self.logger.debug(f"EPD: Skipping retry - previous attempt failed, retry in {remaining:.1f}s")
            return

        # Serialize EPD access across first-minute, mode-refresh, and suri-epaper.
        if shutil.which("flock"):
            timeout_sec = f"{self.epd_lock_wait_sec:g}"
            exec_cmd = ["flock", "-w", timeout_sec, "/run/azazel-epd.lock", *cmd]
        else:
            exec_cmd = cmd

        # Execute EPD update command
        self.logger.info(f"EPD: Updating display - mode={mode}, stage={stage.value}, forced={force}")
        try:
            result = subprocess.run(exec_cmd, timeout=self.epd_timeout_sec, check=False)
            if result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, exec_cmd)
            self.epd_last_update = now
            self.epd_last_fp = fp
            # 信号強度も更新 (NORMAL状態時)
            if stage in (Stage.INIT, Stage.PROBE, Stage.NORMAL):
                self.epd_last_signal_bucket = signal_bucket
            # Clear failure retry guards on success.
            self.epd_last_failed_fp = None
            self.epd_retry_after = 0.0
            self.epd_fail_count = 0
            self.logger.info(f"EPD: Update successful")
        except subprocess.TimeoutExpired:
            self.epd_fail_count += 1
            backoff = min(
                self.epd_fail_backoff_max_sec,
                self.epd_fail_backoff_base_sec * (2 ** max(0, self.epd_fail_count - 1)),
            )
            backoff = max(backoff, self.epd_min_interval)
            self.epd_last_failed_fp = fp
            self.epd_retry_after = now + backoff
            # Treat failed attempt as a recent try to avoid hot-loop retries.
            self.epd_last_update = now
            self.logger.warning(
                f"EPD: Update timed out after {self.epd_timeout_sec:.0f}s; "
                f"retrying in {backoff:.0f}s (fail_count={self.epd_fail_count})"
            )
            return
        except Exception as e:
            self.epd_fail_count += 1
            backoff = min(
                self.epd_fail_backoff_max_sec,
                self.epd_fail_backoff_base_sec * (2 ** max(0, self.epd_fail_count - 1)),
            )
            backoff = max(backoff, self.epd_min_interval)
            self.epd_last_failed_fp = fp
            self.epd_retry_after = now + backoff
            self.epd_last_update = now
            self.logger.warning(
                f"EPD: Update failed: {e}; retrying in {backoff:.0f}s (fail_count={self.epd_fail_count})"
            )
            return

    def _maybe_write_wifi_health(self, link_meta: Dict[str, object]) -> None:
        now = time.time()
        link = link_meta.get("link", {}) if link_meta else {}
        tags = link_meta.get("wifi_tags", []) if link_meta else []

        try:
            from azazel_gadget.core.mock_llm_core import MockLLMCore
            from azazel_gadget.sensors.wifi_health_monitor import health_paths
        except Exception:
            return

        core = MockLLMCore(profile="zero")
        verdict = core.evaluate("wifi_health", features={"tags": tags, "service": "wifi"})
        risk = int(verdict.risk)
        status = "ok" if risk <= 2 else "warn"
        summary = {
            "ts": now,
            "iface": self.cfg.interfaces.get("upstream", ""),
            "link": link,
            "tags": tags,
            "risk": risk,
            "category": verdict.category,
            "reason": verdict.reason,
            "status": status,
        }

        fp = (
            str(link.get("ssid", "")),
            str(link.get("bssid", "")),
            tuple(tags),
            risk,
            verdict.category,
            verdict.reason[:60],
            status,
        )
        if self.health_last_fp == fp and (now - self.health_last_update) < self.health_min_interval:
            return

        out_path, _ = health_paths()
        try:
            out_path.write_text(json.dumps(summary, ensure_ascii=False))
            self.health_last_fp = fp
            self.health_last_update = now
        except Exception:
            pass

    def _evidence_lines(self, link_meta: Dict[str, object]) -> List[str]:
        lines: List[str] = []
        tags = link_meta.get("wifi_tags") or []
        if tags:
            lines.append("• wifi tags: " + ",".join(tags))
        if self.last_probe:
            lines.append(f"• captive portal: {self.last_probe.captive_status} ({self.last_probe.captive_reason})")
            lines.append(f"• tls mismatch: {'yes' if self.last_probe.tls_mismatch else 'no'}")
            lines.append(f"• dns mismatch: {self.last_probe.dns_mismatch}")
        if not lines:
            lines.append("• no recent evidence")
        lines.append(
            f"↳ decision: state={self.state_machine.ctx.state.value} suspicion={round(self.state_machine.ctx.suspicion,2)}"
        )
        return lines

    def _user_state_from_stage(self, stage: Stage) -> str:
        if stage == Stage.PROBE or stage == Stage.INIT:
            return "CHECKING"
        if stage == Stage.NORMAL:
            return "SAFE"
        if stage == Stage.DEGRADED:
            return "LIMITED"
        if stage == Stage.CONTAIN:
            return "CONTAINED"
        if stage == Stage.DECEPTION:
            return "DECEPTION"
        return "CHECKING"

    def _get_cpu_temp(self) -> float:
        """Get CPU temperature in Celsius"""
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp_millidegrees = int(f.read().strip())
                return round(temp_millidegrees / 1000, 1)
        except Exception:
            return 0.0

    def _get_cpu_usage(self) -> float:
        """Get CPU usage percentage"""
        try:
            result = subprocess.run(
                ['top', '-bn1'],
                capture_output=True,
                text=True,
                timeout=2
            )
            match = re.search(r'%Cpu\(s\):\s+([\d.]+)\s+us', result.stdout)
            if match:
                return round(float(match.group(1)), 1)
        except Exception:
            pass
        return 0.0

    def _get_memory_usage(self) -> float:
        """Get memory usage percentage"""
        try:
            result = subprocess.run(
                ['free', '-b'],
                capture_output=True,
                text=True,
                timeout=2
            )
            lines = result.stdout.split('\n')
            if len(lines) > 1:
                mem_line = lines[1].split()
                if len(mem_line) >= 3:
                    total = int(mem_line[1])
                    used = int(mem_line[2])
                    percentage = round((used / total) * 100, 1)
                    return percentage
        except Exception:
            pass
        return 0.0

    def apply_stage(self, stage: Stage) -> None:
        if self.dry_run:
            self.logger.info("dry-run stage change -> %s", stage.value)
            return
        self.nft.set_stage(stage)
        self.tc.apply(stage)

    def seed_probe_destinations(self) -> None:
        hosts = []
        captive = self.cfg.probes.get("captive_portal", {}) or {}
        tls_list = self.cfg.probes.get("tls", []) or []
        for entry in tls_list:
            host = entry.get("host")
            if host:
                hosts.append(host)
        if captive.get("url"):
            parsed = urlparse(captive.get("url"))
            if parsed.hostname:
                hosts.append(parsed.hostname)
        ips = []
        for host in hosts:
            try:
                info = socket.getaddrinfo(host, None)
                ips.extend([i[4][0] for i in info if i[4]])
            except socket.gaierror:
                continue
        seed_probe_ips(self.nft, ips)

    def start(self) -> None:
        self.cfg.ensure_dirs()
        self.preflight()
        if not self.dry_run:
            self.apply_sysctl()
            self._refresh_upstream_iface(force=True, reapply_rules=False)
            self._refresh_captive_probe_iface()
            self.nft.apply_base()
            # まずは開放状態 (NORMAL) から開始し、脅威を検知した場合のみ縮退させる
            self.apply_stage(Stage.NORMAL)
            self.start_dnsmasq()
            self.start_dns_observer()
            self.start_status_api()
            self.seed_probe_destinations()
        else:
            self._refresh_captive_probe_iface()
        self.run_loop()

    def stop(self) -> None:
        self.stop_event.set()
        self.stop_dnsmasq()
        if self.status_server:
            self.status_server.shutdown()
        if self.web_server:
            self.web_server.shutdown()
        if not self.dry_run:
            self.tc.clear()
            self.nft.clear()

    def handle_signals(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop_event.set())

    def _parse_eve_timestamp(self, ts: object) -> Optional[float]:
        if not isinstance(ts, str) or not ts:
            return None
        norm = ts
        if norm.endswith("Z"):
            norm = norm[:-1] + "+00:00"
        if len(norm) >= 5 and norm[-5] in ("+", "-") and norm[-3] != ":":
            norm = f"{norm[:-2]}:{norm[-2:]}"
        try:
            return datetime.fromisoformat(norm).timestamp()
        except ValueError:
            pass
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                return datetime.strptime(norm, fmt).timestamp()
            except ValueError:
                continue
        return None

    def _read_new_eve_events(self, eve: Path) -> list[Dict[str, object]]:
        try:
            stat = eve.stat()
        except OSError:
            return []
        if not self._eve_initialized:
            self._eve_inode = stat.st_ino
            self._eve_offset = 0  # Start from beginning to catch alerts added during initialization
            self._eve_partial = ""
            self._eve_initialized = True
            # Read once on initialization to catch any events already present
            try:
                with eve.open("r", encoding="utf-8", errors="ignore") as handle:
                    data = handle.read()
                    self._eve_offset = handle.tell()
                    lines = data.splitlines()
                    if data and not data.endswith("\n"):
                        self._eve_partial = lines.pop() if lines else data
                    else:
                        self._eve_partial = ""
                    events = []
                    for line in lines:
                        try:
                            obj = json.loads(line)
                            if isinstance(obj, dict):
                                events.append(obj)
                        except (json.JSONDecodeError, Exception):
                            continue
                    return events
            except Exception:
                return []
        if self._eve_inode is None or stat.st_ino != self._eve_inode:
            self._eve_inode = stat.st_ino
            self._eve_offset = 0
            self._eve_partial = ""
        elif stat.st_size < self._eve_offset:
            self._eve_offset = 0
            self._eve_partial = ""
        try:
            with eve.open("r", encoding="utf-8", errors="ignore") as handle:
                handle.seek(self._eve_offset)
                data = handle.read()
                self._eve_offset = handle.tell()
        except Exception:
            return []
        if not data:
            return []
        data = self._eve_partial + data
        lines = data.splitlines()
        if data and not data.endswith("\n"):
            self._eve_partial = lines.pop() if lines else data
        else:
            self._eve_partial = ""
        events: list[Dict[str, object]] = []
        for line in lines:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                self._eve_parse_errors["json_decode_fail"] += 1
                continue
            except Exception:
                self._eve_parse_errors["skipped_lines"] += 1
                continue
            if isinstance(obj, dict):
                events.append(obj)
        return events

    def _coerce_port(self, value: object) -> Optional[int]:
        try:
            port = int(float(str(value)))
        except Exception:
            return None
        if 1 <= port <= 65535:
            return port
        return None

    def _normalize_ipv4(self, value: object) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            addr = ipaddress.ip_address(text)
        except ValueError:
            return None
        if addr.version != 4:
            return None
        return str(addr)

    def _resolve_opencanary_ports(self) -> set[int]:
        # Optional override to pin target ports without parsing OpenCanary config.
        override_ports: set[int] = set()
        override = self.cfg.deception.get("canary_ports_override", [])
        if isinstance(override, (list, tuple)):
            for raw in override:
                parsed = self._coerce_port(raw)
                if parsed is not None:
                    override_ports.add(parsed)
        if override_ports:
            self._opencanary_ports = set(override_ports)
            return set(override_ports)

        cfg_path = Path(self.cfg.deception.get("opencanary_cfg", "/etc/opencanaryd/opencanary.conf"))
        try:
            stat = cfg_path.stat()
            mtime_ns = stat.st_mtime_ns
        except OSError:
            mtime_ns = None

        if (
            mtime_ns is not None
            and self._opencanary_cfg_mtime_ns == mtime_ns
            and self._opencanary_ports
        ):
            return set(self._opencanary_ports)

        ports: set[int] = set()
        if mtime_ns is not None:
            try:
                payload = json.loads(cfg_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
            if isinstance(payload, dict):
                for key, enabled in payload.items():
                    if not isinstance(key, str) or not key.endswith(".enabled"):
                        continue
                    is_enabled = enabled
                    if isinstance(is_enabled, str):
                        is_enabled = is_enabled.strip().lower() in ("1", "true", "yes", "on")
                    if not bool(is_enabled):
                        continue
                    prefix = key[: -len(".enabled")]
                    port = self._coerce_port(payload.get(f"{prefix}.port"))
                    if port is not None:
                        ports.add(port)
            self._opencanary_cfg_mtime_ns = mtime_ns

        if not ports:
            ports = {22, 80}
        self._opencanary_ports = set(ports)
        return set(ports)

    def _extract_canary_target_from_event(self, event: Dict[str, object]) -> Optional[Tuple[str, int]]:
        if not self._canary_delay_enabled:
            return None
        canary_ports = self._resolve_opencanary_ports()
        if not canary_ports:
            return None

        local_ip = self._normalize_ipv4(self._get_interface_ip(self.cfg.interfaces.get("upstream", "")))
        if not local_ip:
            return None

        flow = event.get("flow")
        flow_dict = flow if isinstance(flow, dict) else {}

        src_ip = self._normalize_ipv4(event.get("src_ip") or flow_dict.get("src_ip"))
        dst_ip = self._normalize_ipv4(event.get("dest_ip") or flow_dict.get("dest_ip"))
        src_port = self._coerce_port(event.get("src_port") or flow_dict.get("src_port"))
        dst_port = self._coerce_port(event.get("dest_port") or flow_dict.get("dest_port"))

        if dst_ip == local_ip and dst_port in canary_ports and src_ip:
            return (src_ip, int(dst_port))
        if src_ip == local_ip and src_port in canary_ports and dst_ip:
            return (dst_ip, int(src_port))
        return None

    def _register_canary_delay_targets(self, targets: List[Tuple[str, int]], now: float) -> None:
        if not self._canary_delay_enabled:
            return
        expires_at = now + self._canary_delay_window_sec
        for ip_addr, port in targets:
            ip_norm = self._normalize_ipv4(ip_addr)
            if ip_norm is None:
                continue
            parsed_port = self._coerce_port(port)
            if parsed_port is None:
                continue
            self._canary_delay_targets[(ip_norm, parsed_port)] = expires_at

    def _active_canary_delay_targets(self, now: float) -> List[Tuple[str, int]]:
        expired = [key for key, ttl in self._canary_delay_targets.items() if ttl <= now]
        for key in expired:
            self._canary_delay_targets.pop(key, None)
        return sorted(self._canary_delay_targets.keys())

    def _sync_deception_delay_tc(self, stage: Stage, now: float) -> List[Tuple[str, int]]:
        active_targets = self._active_canary_delay_targets(now)
        if (
            stage == Stage.DECEPTION
            and self._canary_delay_enabled
            and active_targets
            and not self.dry_run
        ):
            self.tc.apply_deception_delay(
                active_targets,
                delay_ms=self._canary_delay_ms,
                jitter_ms=self._canary_delay_jitter_ms,
                loss_percent=self._canary_delay_loss_percent,
            )
        else:
            self.tc.clear_deception_delay()
        return active_targets

    def suricata_bumped(self) -> Dict[str, object]:
        summary: Dict[str, object] = {
            "alert": False,
            "severity": 0,
            "canary_targets": [],
        }
        eve = Path(self.cfg.suricata.get("eve_path", "/var/log/suricata/eve.json"))
        if not self.cfg.suricata.get("enabled", False) or not eve.exists():
            return summary
        events = self._read_new_eve_events(eve)
        if not events:
            return summary
        now = time.time()
        max_severity = 0
        canary_targets: set[Tuple[str, int]] = set()
        for event in events:
            if event.get("event_type") != "alert" and "alert" not in event:
                continue
            try:
                sev = int((event.get("alert") or {}).get("severity", 3))
            except Exception:
                sev = 3
            sev = max(1, min(3, sev))

            ts = self._parse_eve_timestamp(event.get("timestamp"))
            if ts is None:
                dt = 0.0
            else:
                dt = now - ts
                self.logger.debug(f"[suricata] alert: ts={ts:.0f} sev={sev} dt={dt:.1f}s")
            if dt >= 30:
                continue

            summary["alert"] = True
            max_severity = max(max_severity, sev)
            target = self._extract_canary_target_from_event(event)
            if target is not None:
                canary_targets.add(target)
                self.logger.debug(
                    "[suricata] canary target detected src=%s canary_port=%s",
                    target[0],
                    target[1],
                )

        if not summary["alert"] and events:
            self.logger.debug(f"[suricata] {len(events)} alerts found but all outside 30s window")
            return summary

        if max_severity <= 0:
            max_severity = max(1, min(3, int(self._last_suricata_severity or 3)))
        self._last_suricata_severity = max_severity
        summary["severity"] = max_severity
        summary["canary_targets"] = sorted(canary_targets)
        return summary

    def run_loop(self) -> None:
        self.handle_signals()
        probe_done = False
        while not self.stop_event.is_set():
            self._refresh_upstream_iface(reapply_rules=True)
            self.ensure_dnsmasq_running()
            # Check for force flags from Status API (with lock)
            with self.status_ctx_lock:
                force_reprobe = self.status_ctx.pop("force_reprobe", False)
                force_epd_update = self.status_ctx.pop("force_epd_update", False)
                force_contain = self.status_ctx.pop("force_contain", False)
                force_release = self.status_ctx.pop("force_release", False)
                force_stage_open = self.status_ctx.pop("force_stage_open", False)
                force_disconnect = self.status_ctx.pop("force_disconnect", False)
            
            # Track manual actions to force snapshot update
            manual_action_triggered = False
            
            if force_reprobe:
                self.logger.info("ACTION: Force re-probe requested via API")
                probe_done = False  # Force probe re-run
            
            if force_contain:
                self.logger.info("ACTION: Force CONTAIN stage requested via API")
                # Manual CONTAIN: optionally enforce minimum hold duration before release
                now = time.time()
                self.manual_contain_active = True
                self.manual_contain_min_until = now + self.manual_contain_min_duration_sec
                self.release_confirm_pending = False
                self.release_confirm_until = 0.0
                self.forced_contain_until = 0.0
                # Directly transition to CONTAIN stage
                prev_stage = self.current_stage
                self.current_stage = Stage.CONTAIN
                # CRITICAL: Also update state_machine internal state so write_snapshot() reflects CONTAIN
                self.state_machine.ctx.state = Stage.CONTAIN
                self.apply_stage(Stage.CONTAIN)
                # Log the manual transition
                add_history_event(
                    from_stage=prev_stage.value,
                    to_stage=Stage.CONTAIN.value,
                    suspicion=75,  # High suspicion for manual contain
                    reason="Manual containment activated via WebUI"
                )
                # Immediately write updated snapshot
                link_meta = {}  # Minimal metadata for snapshot
                contain_summary = {
                    "changed": True,
                    "suspicion": 75,
                    "reason": "Manual containment activated via WebUI",
                    "constraints": [],
                }
                self.write_snapshot(contain_summary, link_meta)
                self._maybe_update_epd(Stage.CONTAIN, contain_summary, link_meta, force=True)
                # Also update status_ctx immediately
                with self.status_ctx_lock:
                    self.status_ctx.update({
                        "stage": Stage.CONTAIN.value,
                        "suspicion": 75,
                        "reason": "Manual containment activated via WebUI",
                    })
                self.logger.info(
                    "CONTAIN stage activated via API (manual_min_hold=%.1fs, release_confirm_window=%.1fs)",
                    self.manual_contain_min_duration_sec,
                    self.manual_release_confirm_window_sec,
                )
                # Skip state machine this loop to preserve CONTAIN
                time.sleep(0.1)  # Brief delay before next iteration
                continue  # Skip the rest of the loop

            if force_release:
                self.logger.info("ACTION: Release CONTAIN requested via API")
                now = time.time()
                if not self.manual_contain_active:
                    self.logger.info("Release ignored: manual CONTAIN not active")
                    time.sleep(0.1)
                    continue
                if now < self.manual_contain_min_until:
                    remaining = int(self.manual_contain_min_until - now)
                    self.logger.info(f"Release blocked: minimum duration not reached ({remaining}s)")
                    with self.status_ctx_lock:
                        self.status_ctx.update(
                            {
                                "reason": f"Contain minimum duration not reached ({remaining}s)",
                            }
                        )
                    time.sleep(0.1)
                    continue
                if self.manual_release_confirm_window_sec > 0.0:
                    if not self.release_confirm_pending or now > self.release_confirm_until:
                        self.release_confirm_pending = True
                        self.release_confirm_until = now + self.manual_release_confirm_window_sec
                        self.logger.info(
                            "Release confirmation required (press again within %.1fs)",
                            self.manual_release_confirm_window_sec,
                        )
                        link_meta = {}
                        confirm_reason = (
                            f"Release confirmation required (press again within "
                            f"{self.manual_release_confirm_window_sec:.1f}s)"
                        )
                        confirm_summary = {
                            "changed": False,
                            "suspicion": 75,
                            "reason": confirm_reason,
                            "constraints": [],
                        }
                        self.write_snapshot(confirm_summary, link_meta)
                        with self.status_ctx_lock:
                            self.status_ctx.update(
                                {
                                    "reason": confirm_reason,
                                }
                            )
                        time.sleep(0.1)
                        continue
                # Confirmed release
                self.release_confirm_pending = False
                self.release_confirm_until = 0.0
                self.manual_contain_active = False
                # Clear forced CONTAIN override
                self.forced_contain_until = 0.0
                prev_stage = self.current_stage
                self.current_stage = Stage.NORMAL
                # Sync state machine to NORMAL and reset suspicion
                self.state_machine.ctx.state = Stage.NORMAL
                self.state_machine.ctx.suspicion = 0.0
                self.state_machine.ctx.last_reason = "manual_release"
                self.apply_stage(Stage.NORMAL)
                add_history_event(
                    from_stage=prev_stage.value,
                    to_stage=Stage.NORMAL.value,
                    suspicion=0,
                    reason="Manual containment released via WebUI",
                )
                link_meta = {}
                release_summary = {
                    "changed": True,
                    "suspicion": 0,
                    "reason": "Manual containment released via WebUI",
                    "constraints": [],
                }
                self.write_snapshot(release_summary, link_meta)
                self._maybe_update_epd(Stage.NORMAL, release_summary, link_meta, force=True)
                with self.status_ctx_lock:
                    self.status_ctx.update(
                        {
                            "stage": Stage.NORMAL.value,
                            "suspicion": 0,
                            "reason": "Manual containment released via WebUI",
                        }
                    )
                probe_done = False
                time.sleep(0.1)
                continue

            if force_stage_open:
                self.logger.info("ACTION: Stage Open requested via API")
                now = time.time()
                if self.manual_contain_active and now < self.manual_contain_min_until:
                    remaining = int(self.manual_contain_min_until - now)
                    self.logger.info(f"Stage Open blocked: manual CONTAIN minimum duration not reached ({remaining}s)")
                    # Reflect the blocked action in the UI snapshot
                    blocked_summary = {
                        "changed": False,
                        "suspicion": 75,
                        "reason": f"Contain minimum duration not reached ({remaining}s)",
                        "constraints": [],
                    }
                    self.write_snapshot(blocked_summary, {})
                    with self.status_ctx_lock:
                        self.status_ctx.update(
                            {
                                "reason": f"Contain minimum duration not reached ({remaining}s)",
                            }
                        )
                    time.sleep(0.1)
                    continue
                # Clear manual flags and open stage
                self.manual_contain_active = False
                self.release_confirm_pending = False
                self.release_confirm_until = 0.0
                self.forced_contain_until = 0.0
                prev_stage = self.current_stage
                self.current_stage = Stage.NORMAL
                self.state_machine.ctx.state = Stage.NORMAL
                self.state_machine.ctx.suspicion = 0.0
                self.state_machine.ctx.last_reason = "stage_open"
                self.apply_stage(Stage.NORMAL)
                add_history_event(
                    from_stage=prev_stage.value,
                    to_stage=Stage.NORMAL.value,
                    suspicion=0,
                    reason="Stage Open via WebUI",
                )
                link_meta = {}
                open_summary = {
                    "changed": True,
                    "suspicion": 0,
                    "reason": "Stage Open via WebUI",
                    "constraints": [],
                }
                self.write_snapshot(open_summary, link_meta)
                self._maybe_update_epd(Stage.NORMAL, open_summary, link_meta, force=True)
                with self.status_ctx_lock:
                    self.status_ctx.update(
                        {
                            "stage": Stage.NORMAL.value,
                            "suspicion": 0,
                            "reason": "Stage Open via WebUI",
                        }
                    )
                probe_done = False
                time.sleep(0.1)
                continue
            
            if force_disconnect:
                self.logger.info("ACTION: Disconnect requested via API")
                iface = self.cfg.interfaces.get("upstream", "")
                disconnect_ok = False
                reason = f"Wi-Fi disconnected ({iface})"
                errors = []

                try:
                    def attempt(cmd, label):
                        result = subprocess.run(cmd, capture_output=True, timeout=5)
                        if result.returncode == 0:
                            return True
                        stderr = result.stderr.decode("utf-8").strip() if result.stderr else "unknown error"
                        errors.append(f"{label}: {stderr}")
                        return False

                    if shutil.which("wpa_cli"):
                        disconnect_ok = attempt(["wpa_cli", "-i", iface, "disconnect"], "wpa_cli disconnect")
                    if not disconnect_ok and shutil.which("nmcli"):
                        disconnect_ok = attempt(["nmcli", "dev", "disconnect", iface], "nmcli disconnect")
                    if not disconnect_ok and shutil.which("iw"):
                        disconnect_ok = attempt(["iw", "dev", iface, "disconnect"], "iw disconnect")
                    if not disconnect_ok:
                        if attempt(["ip", "link", "set", iface, "down"], "ip link down"):
                            disconnect_ok = True
                            reason = f"Wi-Fi disconnected ({iface} down)"
                except Exception as e:
                    errors.append(str(e))

                if disconnect_ok:
                    self.state_machine.ctx.last_reason = "disconnect"
                    self.persistent_connection_state = self._normalize_connection_state(
                        {
                        "wifi_state": "DISCONNECTED",
                        "usb_nat": "OFF",
                        "internet_check": "N/A",
                        "captive_portal": "NA",
                        "captive_portal_reason": "MANUAL_DISCONNECT",
                        "captive_probe_iface": "",
                        }
                    )
                else:
                    reason = "Disconnect failed: " + ("; ".join(errors) if errors else "unknown error")
                with self.status_ctx_lock:
                    self.status_ctx.update({"reason": reason})

                disconnect_summary = {
                    "changed": disconnect_ok,
                    "suspicion": self.state_machine.ctx.suspicion,
                    "reason": reason,
                    "constraints": [],
                }
                self.write_snapshot(disconnect_summary, {}, skip_sync=True)
                self._maybe_update_epd(self.current_stage, disconnect_summary, {}, force=True)
                # Skip state machine this loop
                time.sleep(0.1)
                continue  # Skip the rest of the loop
            
            # Manual CONTAIN: keep stage until explicit release
            if self.manual_contain_active:
                if self.current_stage != Stage.CONTAIN:
                    self.current_stage = Stage.CONTAIN
                    self.state_machine.ctx.state = Stage.CONTAIN
                time.sleep(0.1)
                continue

            # Check if temporary forced CONTAIN is still active
            if time.time() < self.forced_contain_until:
                self.logger.debug(f"Maintaining forced CONTAIN (until {self.forced_contain_until - time.time():.1f}s)")
                # Skip state machine, keep current stage
                time.sleep(0.1)
                continue  # Skip the rest of the loop
            
            link_state, link_meta, new_link = self.poll_wifi()
            resolved_probe = self._refresh_captive_probe_iface()
            captive_probe_iface = str(resolved_probe.get("iface", "") or "")
            captive_skip_reason = str(resolved_probe.get("reason", "NOT_FOUND") or "NOT_FOUND")
            if not captive_probe_iface:
                checked_at = datetime.utcnow().isoformat() + "Z"
                self.last_probe = ProbeOutcome(
                    captive_portal=False,
                    captive_status="NA",
                    captive_reason=captive_skip_reason,
                    captive_checked_at=checked_at,
                    captive_iface="",
                    tls_mismatch=False,
                    dns_mismatch=0,
                    route_anomaly=False,
                    details={
                        "captive": {
                            "status": "NA",
                            "reason": captive_skip_reason,
                            "checked_at": checked_at,
                            "iface": "",
                        },
                        "tls": [],
                        "dns": {},
                        "route": {"upstream": self.cfg.interfaces.get("upstream", "")},
                    },
                )
                current_conn = self.persistent_connection_state.copy() if self.persistent_connection_state else {}
                current_conn.update(
                    {
                        "captive_probe_iface": "",
                        "captive_portal": "NA",
                        "captive_portal_reason": captive_skip_reason,
                        "captive_checked_at": checked_at,
                    }
                )
                self.persistent_connection_state = self._normalize_connection_state(current_conn)
            signals: Dict[str, object] = {"link_up": link_state}
            if link_meta.get("bssid"):
                signals["bssid"] = link_meta["bssid"]
            wifi_tags = link_meta.get("wifi_tags", [])
            if wifi_tags:
                signals["wifi_tags"] = True
            if new_link:
                probe_done = False

            if link_state and captive_probe_iface and not probe_done:
                self.last_probe = run_all(
                    self.cfg.probes,
                    self.cfg.interfaces["upstream"],
                    captive_iface=captive_probe_iface,
                )
                current_conn = self.persistent_connection_state.copy() if self.persistent_connection_state else {}
                current_conn.update(
                    {
                        "captive_probe_iface": self.last_probe.captive_iface or captive_probe_iface,
                        "captive_portal": self.last_probe.captive_status,
                        "captive_portal_reason": self.last_probe.captive_reason,
                        "captive_checked_at": self.last_probe.captive_checked_at,
                    }
                )
                self.persistent_connection_state = self._normalize_connection_state(current_conn)
                signals["probe_fail"] = self.last_probe.captive_portal or self.last_probe.tls_mismatch
                signals["probe_fail_count"] = 1 + self.last_probe.dns_mismatch
                signals["dns_mismatch"] = self.last_probe.dns_mismatch
                signals["cert_mismatch"] = self.last_probe.tls_mismatch
                signals["route_anomaly"] = self.last_probe.route_anomaly
                
                # ★ DNS 不一致を通知（閾値超過時）
                dns_mismatch_alert_threshold = int(
                    self.cfg.notify.get("thresholds", {}).get("dns_mismatch_alert", 3)
                )
                if self.last_probe.dns_mismatch >= dns_mismatch_alert_threshold:
                    self._notify_signal_alert(
                        signal_type="dns_mismatch",
                        reason=f"DNS mismatch detected: {self.last_probe.dns_mismatch} mismatches",
                        tags=["dns", "warning"],
                    )
                
                probe_done = True

            suricata_ctx = self.suricata_bumped()
            if bool(suricata_ctx.get("alert", False)):
                signals["suricata_alert"] = True
                signals["suricata_severity"] = max(1, min(3, int(suricata_ctx.get("severity", self._last_suricata_severity or 3))))
                canary_targets = suricata_ctx.get("canary_targets", [])
                if isinstance(canary_targets, list) and canary_targets:
                    self._register_canary_delay_targets(canary_targets, time.time())
                    signals["canary_target_alert"] = True
                
                # ★ Suricata アラートを通知
                severity_name = {1: "Low", 2: "Medium", 3: "High"}.get(
                    int(suricata_ctx.get("severity", self._last_suricata_severity or 3)), "Unknown"
                )
                self._notify_signal_alert(
                    signal_type="suricata_alert",
                    reason=f"Suricata detected suspicious activity (severity: {severity_name})",
                    tags=["suricata", "ids", severity_name.lower()],
                )

            state, summary = self.state_machine.step(signals)
            if (
                state == Stage.CONTAIN
                and self.cfg.deception.get("enable_if_opencanary_present", False)
                and Path(self.cfg.deception.get("opencanary_cfg", "/etc/opencanaryd/opencanary.conf")).exists()
                and self._get_current_mode_label() == "SCAPEGOAT"
                and self._is_opencanary_on()
            ):
                state = Stage.DECEPTION
            if state != self.current_stage:
                # ★ 状態遷移を通知
                self._notify_state_transition(self.current_stage, state)
                
                # Web UI 履歴に記録
                add_history_event(
                    from_stage=self.current_stage.value,
                    to_stage=state.value,
                    suspicion=summary.get("suspicion", 0),
                    reason=summary.get("reason", "")
                )
                self.current_stage = state
                probe_done = state != Stage.PROBE
                self.apply_stage(state)

            active_canary_targets = self._sync_deception_delay_tc(state, time.time())
            
            # Tactics Engine: DecisionRecord を作成・ログ（状態遷移時のみ）
            if state != self.current_stage or "suricata_alert" in signals:
                try:
                    # 簡略版：suricata_alert のみを記録
                    if "suricata_alert" in signals:
                        source = "suricata"
                        event_digest = "sha256:pending"  # 本実装ではeve.json全体のハッシュを想定
                        event_min = None
                    else:
                        source = "internal"
                        event_digest = "sha256:internal"
                        event_min = None

                    state_before = StateSnapshot(
                        state=self.current_stage.value,
                        user_state=str(summary.get("reason", "")),
                        suspicion=float(summary.get("suspicion", 0)),
                        risk_score=0,
                    )
                    state_after = StateSnapshot(
                        state=state.value,
                        user_state=str(summary.get("reason", "")),
                        suspicion=float(summary.get("suspicion", 0)),
                        risk_score=0,
                    )
                    score_delta = ScoreDelta(
                        suspicion_add=max(0.0, float(summary.get("suspicion", 0))),
                        suspicion_decay=0.0,
                    )
                    constraints_triggered = summary.get("constraints", []) if isinstance(summary.get("constraints", []), list) else []

                    chosen = [
                        ChosenAction(
                            action_type="transition",
                            detail={"from": self.current_stage.value, "to": state.value}
                        )
                    ]

                    record = DecisionLogger.create_record(
                        engine_version="0.1.0",
                        config_hash=self.config_hash,
                        inputs_source=source,
                        event_digest=event_digest,
                        event_min=event_min,
                        features={
                            "suricata_alert": "suricata_alert" in signals,
                            "suricata_severity": int(signals.get("suricata_severity", 0)),
                            "canary_target_alert": "canary_target_alert" in signals,
                        },
                        state_before=state_before,
                        score_delta=score_delta,
                        constraints_triggered=constraints_triggered,
                        chosen=chosen,
                        state_after=state_after,
                        parse_errors=self._eve_parse_errors.copy(),
                    )
                    self.decision_logger.log_decision(record)
                    self.last_decision_id = record.decision_id
                except Exception as e:
                    self.logger.warning(f"Failed to log decision: {e}")
            
            # Tactics Engine: status_ctx を更新（config_hash は常に含める）
            link = link_meta.get("link", {}) if link_meta else {}
            self.status_ctx.update(
                {
                    "stage": state.value,
                    "suspicion": summary.get("suspicion", 0),
                    "reason": summary.get("reason", ""),
                    "wifi": link_meta,
                    "last_probe": self.last_probe.details if self.last_probe else None,
                    "config_hash": self.config_hash,
                    "last_decision_id": self.last_decision_id,
                    # Web UI 用の追加データ
                    "start_time": getattr(self, "_start_time", time.time()),
                    "upstream_if": self.cfg.interfaces.get("upstream", ""),
                    "captive_probe_iface": self._resolved_captive_probe_iface,
                    "captive_portal_status": self.last_probe.captive_status if self.last_probe else "NA",
                    "captive_portal_reason": self.last_probe.captive_reason if self.last_probe else self._resolved_captive_probe_reason,
                    "downstream_if": self.cfg.interfaces.get("downstream", "usb0"),
                    "mgmt_ip": self.cfg.interfaces.get("mgmt_ip", "10.55.0.10"),
                    "ssid": (link or {}).get("ssid", "-"),
                    "bssid": (link or {}).get("bssid", "-"),
                    "signal_dbm": (link or {}).get("signal", "-"),
                    "rtt_ms": 180 if state in (Stage.PROBE, Stage.DEGRADED) else 0,
                    "rate_mbps": 2.0 if state == Stage.DEGRADED else 1.0 if state == Stage.PROBE else 0,
                    "last_signals": signals,
                    "canary_delay_enabled": self._canary_delay_enabled,
                    "canary_delay_active": bool(state == Stage.DECEPTION and active_canary_targets),
                    "canary_delay_target_count": len(active_canary_targets),
                    "canary_delay_targets": [f"{ip}:{port}" for ip, port in active_canary_targets],
                    "degrade_threshold": self.cfg.state_machine.get("degrade_threshold", 20),
                    "normal_threshold": self.cfg.state_machine.get("normal_threshold", 8),
                    "contain_threshold": self.cfg.state_machine.get("contain_threshold", 50),
                    "decay_per_sec": self.cfg.state_machine.get("decay_per_sec", 3),
                    "suricata_cooldown_sec": self.cfg.state_machine.get("suricata_cooldown_sec", 30),
                }
            )
            self.write_snapshot(summary, link_meta)
            self._maybe_update_epd(state, summary, link_meta, force=force_epd_update)
            self._maybe_write_wifi_health(link_meta)
            if self.pretty_console:
                self.render_console(state, summary, link_meta)
            
            # ★ NEW: ログデバウンス
            # 状態遷移時のみ INFO ログ出力；その他は DEBUG
            if summary.get("changed", False):
                # 状態遷移時は常にログ出力
                log_entry = {
                    **self.status_ctx,
                    "transitioned": True,
                }
                self.logger.info(json.dumps(log_entry))
            
            # DEBUG ログ：詳細（毎ループ）
            self.logger.debug(
                f"step: state={state.value} susp={summary.get('suspicion', 0):.1f} "
                f"reason={summary.get('reason', '')} changed={summary.get('changed', False)}"
            )
            
            time.sleep(2.0)
        self.stop()

    def poll_wifi(self) -> tuple[bool, Dict[str, object], bool]:
        upstream_iface = self.cfg.interfaces["upstream"]
        if self._is_wireless_iface(upstream_iface):
            tags, meta = evaluate_wifi_safety(
                upstream_iface,
                self.cfg.paths.get("known_db", ""),
                self.cfg.interfaces.get("gateway_ip"),
            )
        else:
            has_ip = self._get_interface_ip(upstream_iface) != "-"
            has_default_route = any(
                str(route.get("dev") or "") == upstream_iface for route in self._default_routes()
            )
            connected = has_ip and has_default_route
            gw = self._default_gateway_for_iface(upstream_iface)
            link_meta = {
                "connected": "1" if connected else "0",
                "ssid": f"Wired:{upstream_iface}" if connected else "",
                "bssid": upstream_iface if connected else "",
            }
            if gw:
                link_meta["gateway"] = gw
            tags = []
            meta = {"link": link_meta, "capture_len": 0}
        link = meta.get("link", {})
        connected = link.get("connected") == "1"
        bssid = link.get("bssid", "")
        new_link = False
        if connected and bssid and bssid != self.state_machine.ctx.last_link_bssid:
            self.state_machine.reset_for_new_link(bssid)
            self.current_stage = Stage.NORMAL
            new_link = True
        meta["wifi_tags"] = tags
        return connected, meta, new_link

    def render_console(self, state: Stage, summary: Dict[str, object], link_meta: Dict[str, object]) -> None:
        # Simple text dashboard; keeps JSON logs intact.
        now = time.time()
        if now - self.last_console < 1:
            return
        self.last_console = now
        link = link_meta.get("link", {}) if link_meta else {}
        ssid = link.get("ssid", "")
        bssid = link.get("bssid", "")
        bar_len = min(20, int(float(summary.get("suspicion", 0)) / 5))
        bar = "#" * bar_len + "." * (20 - bar_len)
        probe = self.last_probe
        probe_lines = []
        if probe:
            probe_lines.append(f"Captive: {probe.captive_status}")
            probe_lines.append(f"TLS mismatch: {'YES' if probe.tls_mismatch else 'no'}")
            probe_lines.append(f"DNS mismatch: {probe.dns_mismatch}")
        tags = link_meta.get("wifi_tags", []) if link_meta else []
        out = []
        out.append("\033[2J\033[H")  # clear screen
        out.append("Azazel-Gadget First-Minute Control")
        out.append(f"State: {state.value:8}  Suspicion: {summary.get('suspicion', 0):5} [{bar}]")
        out.append(f"Reason: {summary.get('reason','')}")
        out.append(f"Wi-Fi: ssid={ssid} bssid={bssid}")
        if tags:
            out.append(f"Wi-Fi tags: {','.join(tags)}")
        if probe_lines:
            out.append("Probe: " + " | ".join(probe_lines))
        out.append("Ctrl+Cで停止 / JSONログ: first_minute.log")
        sys.stdout.write("\n".join(out) + "\n")
        sys.stdout.flush()
    def _init_notifier(self) -> None:
        """★ ntfy 通知クライアントを初期化（トークンファイルから読込）"""
        try:
            ntfy_cfg = self.cfg.notify.get("ntfy", {})
            token_file = Path(ntfy_cfg.get("token_file", "/etc/azazel/ntfy.token"))
            
            if not token_file.exists():
                self.logger.warning(f"ntfy token file not found: {token_file}, notifications disabled")
                return
            
            token = token_file.read_text().strip()
            if not token:
                self.logger.warning(f"ntfy token file is empty: {token_file}, notifications disabled")
                return
            
            self.notifier = NtfyNotifier(
                base_url=ntfy_cfg.get("base_url", "http://10.55.0.10:8081"),
                token=token,
                topic_alert=ntfy_cfg.get("topic_alert", "azg-alert-critical"),
                topic_info=ntfy_cfg.get("topic_info", "azg-info-status"),
                cooldown_sec=int(ntfy_cfg.get("cooldown_sec", 30)),
            )
            self.logger.info("ntfy notifier initialized successfully")
        except Exception as e:
            self.logger.error(f"Failed to initialize ntfy notifier: {e}")
            self.notifier = None
    
    def _notify_state_transition(self, from_stage: Stage, to_stage: Stage) -> None:
        """★ 状態遷移を通知"""
        if not self.notifier:
            return
        
        # 危険側（DEGRADED/CONTAIN）のみアラート通知、それ以外は情報通知
        is_danger = to_stage in (Stage.DEGRADED, Stage.CONTAIN)
        title = f"State: {from_stage.value} → {to_stage.value}"
        event_key = f"state_change:{from_stage.value}->{to_stage.value}"
        
        if is_danger:
            self.notifier.notify_alert(
                title=title,
                body=f"Azazel-Gadget transitioned to {to_stage.value}",
                tags=["state-change", to_stage.value.lower()],
                priority=4,
                event_key=event_key,
            )
        else:
            self.notifier.notify_info(
                title=title,
                body=f"Azazel-Gadget transitioned to {to_stage.value}",
                tags=["state-change", to_stage.value.lower()],
                priority=2,
                event_key=event_key,
            )
    
    def _notify_signal_alert(self, signal_type: str, reason: str, tags: Optional[list] = None) -> None:
        """★ シグナルアラートを通知（suricata, dns_mismatch など）"""
        if not self.notifier:
            return
        
        event_key = f"signal:{signal_type}"
        tag_list = tags or [signal_type.lower()]
        
        self.notifier.notify_alert(
            title=f"Signal Alert: {signal_type}",
            body=reason,
            tags=tag_list,
            priority=5,
            event_key=event_key,
        )
