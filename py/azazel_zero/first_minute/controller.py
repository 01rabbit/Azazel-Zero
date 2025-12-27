from __future__ import annotations

import json
import logging
import os
import signal
import socket
from urllib.parse import urlparse
import subprocess
import threading
import time
import shutil
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional

from azazel_zero.sensors.wifi_safety import evaluate_wifi_safety

from .config import FirstMinuteConfig
from .dns_observer import DNSObserver, seed_probe_ips
from .nft import NftManager
from .probes import ProbeOutcome, run_all
from .state_machine import FirstMinuteStateMachine, Stage
from .tc import TcManager


class StatusHandler(BaseHTTPRequestHandler):
    def __init__(self, ctx: Dict[str, object], *args, **kwargs):
        self.ctx = ctx
        super().__init__(*args, **kwargs)

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(self.ctx, default=str).encode("utf-8"))

    def log_message(self, fmt, *args):  # pragma: no cover - avoid noisy logs
        return


def make_status_server(host: str, port: int, ctx: Dict[str, object]) -> ThreadingHTTPServer:
    def handler(*args, **kwargs):
        return StatusHandler(ctx, *args, **kwargs)

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
        self.nft = NftManager(
            cfg.nft_template_path,
            cfg.interfaces["upstream"],
            cfg.interfaces["downstream"],
            cfg.interfaces["mgmt_ip"],
            cfg.interfaces["mgmt_subnet"],
            int(cfg.policy.get("probe_allow_ttl", 120)),
            int(cfg.policy.get("dynamic_allow_ttl", 300)),
        )
        self.tc = TcManager(cfg.interfaces["downstream"], cfg.interfaces["upstream"])
        self.dns_thread: Optional[DNSObserver] = None
        self.status_ctx: Dict[str, object] = {"state": "INIT", "suspicion": 0, "last_probe": None}
        self.status_server: Optional[ThreadingHTTPServer] = None
        self.processes: Dict[str, subprocess.Popen] = {}
        self.last_console = 0.0

    def preflight(self) -> None:
        if os.geteuid() != 0:
            raise SystemExit("First-Minute Control requires root.")
        for bin_name in ("nft", "tc", "ip"):
            if not shutil.which(bin_name):  # type: ignore[name-defined]
                raise SystemExit(f"{bin_name} not found in PATH")

    def apply_sysctl(self) -> None:
        cmds = [
            ["sysctl", "-w", "net.ipv4.ip_forward=1"],
            ["sysctl", "-w", "net.ipv4.conf.all.rp_filter=1"],
            ["sysctl", "-w", "net.ipv4.conf.default.rp_filter=1"],
        ]
        for cmd in cmds:
            subprocess.run(cmd, check=False)

    def start_dnsmasq(self) -> None:
        if self.no_dns_start or not self.cfg.dnsmasq.get("enable", True):
            return
        cmd = ["dnsmasq", f"--conf-file={self.cfg.dnsmasq_conf_path}"]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.processes["dnsmasq"] = proc

    def stop_dnsmasq(self) -> None:
        proc = self.processes.get("dnsmasq")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    def start_dns_observer(self) -> None:
        self.dns_thread = DNSObserver(self.cfg.dns_log_path, self.nft, self.stop_event)
        self.dns_thread.start()

    def start_status_api(self) -> None:
        host = self.cfg.status_api.get("host", "127.0.0.1")
        port = int(self.cfg.status_api.get("port", 8081))
        self.status_server = make_status_server(host, port, self.status_ctx)
        thread = threading.Thread(target=self.status_server.serve_forever, daemon=True)
        thread.start()

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
            self.nft.apply_base()
            self.apply_stage(Stage.PROBE)
            self.start_dnsmasq()
            self.start_dns_observer()
            self.start_status_api()
            self.seed_probe_destinations()
        self.run_loop()

    def stop(self) -> None:
        self.stop_event.set()
        self.stop_dnsmasq()
        if self.status_server:
            self.status_server.shutdown()
        if not self.dry_run:
            self.tc.clear()
            self.nft.clear()

    def handle_signals(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop_event.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop_event.set())

    def suricata_bumped(self) -> bool:
        eve = Path(self.cfg.suricata.get("eve_path", "/var/log/suricata/eve.json"))
        if not self.cfg.suricata.get("enabled", False) or not eve.exists():
            return False
        try:
            return time.time() - eve.stat().st_mtime < 30
        except Exception:
            return False

    def run_loop(self) -> None:
        self.handle_signals()
        probe_done = False
        while not self.stop_event.is_set():
            link_state, link_meta, new_link = self.poll_wifi()
            signals: Dict[str, object] = {"link_up": link_state}
            if link_meta.get("bssid"):
                signals["bssid"] = link_meta["bssid"]
            wifi_tags = link_meta.get("wifi_tags", [])
            if wifi_tags:
                signals["wifi_tags"] = True
            if new_link:
                probe_done = False

            if self.current_stage == Stage.PROBE and link_state and not probe_done:
                self.last_probe = run_all(self.cfg.probes, self.cfg.interfaces["upstream"])
                signals["probe_fail"] = self.last_probe.captive_portal or self.last_probe.tls_mismatch
                signals["probe_fail_count"] = 1 + self.last_probe.dns_mismatch
                signals["dns_mismatch"] = self.last_probe.dns_mismatch
                signals["cert_mismatch"] = self.last_probe.tls_mismatch
                signals["route_anomaly"] = self.last_probe.route_anomaly
                probe_done = True

            if self.suricata_bumped():
                signals["suricata_alert"] = True

            state, summary = self.state_machine.step(signals)
            if (
                state == Stage.CONTAIN
                and self.cfg.deception.get("enable_if_opencanary_present", False)
                and Path(self.cfg.deception.get("opencanary_cfg", "/etc/opencanaryd/opencanary.conf")).exists()
            ):
                state = Stage.DECEPTION
            if state != self.current_stage:
                self.current_stage = state
                probe_done = state != Stage.PROBE
                self.apply_stage(state)
            self.status_ctx.update(
                {
                    "state": state.value,
                    "suspicion": summary.get("suspicion", 0),
                    "reason": summary.get("reason", ""),
                    "wifi": link_meta,
                    "last_probe": self.last_probe.details if self.last_probe else None,
                }
            )
            if self.pretty_console:
                self.render_console(state, summary, link_meta)
            self.logger.info(json.dumps(self.status_ctx))
            time.sleep(2.0)
        self.stop()

    def poll_wifi(self) -> tuple[bool, Dict[str, object], bool]:
        tags, meta = evaluate_wifi_safety(
            self.cfg.interfaces["upstream"],
            self.cfg.paths.get("known_db", ""),
            self.cfg.interfaces.get("gateway_ip"),
        )
        link = meta.get("link", {})
        connected = link.get("connected") == "1"
        bssid = link.get("bssid", "")
        new_link = False
        if connected and bssid and bssid != self.state_machine.ctx.last_link_bssid:
            self.state_machine.reset_for_new_link(bssid)
            self.current_stage = Stage.PROBE
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
            probe_lines.append(f"Captive: {'YES' if probe.captive_portal else 'no'}")
            probe_lines.append(f"TLS mismatch: {'YES' if probe.tls_mismatch else 'no'}")
            probe_lines.append(f"DNS mismatch: {probe.dns_mismatch}")
        tags = link_meta.get("wifi_tags", []) if link_meta else []
        out = []
        out.append("\033[2J\033[H")  # clear screen
        out.append("Azazel-Zero First-Minute Control")
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
