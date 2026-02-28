#!/usr/bin/env python3
"""Deterministic Azazel mode manager (portal/shield/scapegoat)."""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None

MODE_CHOICES = ("portal", "shield", "scapegoat")
DEFAULT_MODE = "shield"

LOCK_PATH = Path("/run/azazel/mode.lock")
EPD_STATE_PATH = Path("/run/azazel/epd_state.json")
AUDIT_LOG_PATH = Path("/var/log/azazel/mode_changes.jsonl")
SNAPSHOT_DIR = Path("/var/lib/azazel/snapshots")

CANARY_NS = "az_canary"
# Linux interface names must be <= 15 chars.
CANARY_HOST_IF = "vcanary_host"
CANARY_NS_IF = "vcanary_ns"
CANARY_HOST_IP = "169.254.240.1/30"
CANARY_NS_IP = "169.254.240.2/30"
CANARY_NS_ADDR = "169.254.240.2"
CANARY_PIDFILE = Path("/home/azazel/canary-venv/bin/opencanaryd.pid")

LOGGER = logging.getLogger("azazel.mode")


@dataclass
class PreflightContext:
    usb_if: str
    upstream_if: str
    mgmt_subnet: str
    mgmt_ip: str
    fw_backend: str
    canary_ports: List[int]
    epd_available: bool


class ModeError(RuntimeError):
    """Raised when mode transition cannot complete."""


class ModeManager:
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or LOGGER

    # ---------- public ----------

    def status(self) -> Dict[str, Any]:
        state = self._read_mode_state()
        epd_state = self._read_json(EPD_STATE_PATH)
        usb_if, upstream_if, mgmt_subnet, mgmt_ip = self._resolve_interfaces()
        fw_backend = self._detect_firewall_backend(raise_on_missing=False)
        canary_ports = self._resolve_canary_ports()
        opencanary = self._opencanary_state()
        return {
            "ok": True,
            "mode": {
                "current_mode": state.get("current_mode", DEFAULT_MODE),
                "last_change": state.get("last_change", ""),
                "requested_by": state.get("requested_by", ""),
                "config_hash": state.get("config_hash", ""),
            },
            "interfaces": {
                "usb_if": usb_if,
                "upstream_if": upstream_if,
                "mgmt_subnet": mgmt_subnet,
                "mgmt_ip": mgmt_ip,
            },
            "firewall_backend": fw_backend,
            "canary_ports": canary_ports,
            "opencanary": opencanary,
            "epd_state": epd_state,
            "ts": self._iso_now(),
        }

    def set_mode(self, mode: str, requested_by: str = "cli", dry_run: bool = False) -> Dict[str, Any]:
        target_mode = self._normalize_mode(mode)

        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with LOCK_PATH.open("a+", encoding="utf-8") as lock_fh:
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)

            prev_state = self._read_mode_state()
            prev_mode = str(prev_state.get("current_mode", DEFAULT_MODE)).strip().lower() or DEFAULT_MODE
            if prev_mode not in MODE_CHOICES:
                prev_mode = DEFAULT_MODE

            self._write_epd_state(
                {
                    "mode": "switching",
                    "target_mode": target_mode,
                    "requested_by": requested_by,
                    "mode_last_change": prev_state.get("last_change", ""),
                    "last_error": "",
                },
                trigger_refresh=not dry_run,
            )

            snapshot = self._take_snapshots()
            context: Optional[PreflightContext] = None
            try:
                context = self._preflight(target_mode)
                if dry_run:
                    effective = self._effective_config(target_mode, context)
                    return {
                        "ok": True,
                        "dry_run": True,
                        "from": prev_mode,
                        "to": target_mode,
                        "requested_by": requested_by,
                        "effective_config": effective,
                        "ts": self._iso_now(),
                    }

                self._apply_mode(target_mode, context)
                verify = self._verify_invariants(target_mode, context)
                if not verify.get("ok"):
                    raise ModeError(f"Invariant verification failed: {verify.get('reason', 'unknown')}")

                config_hash = self._hash_effective_config(self._effective_config(target_mode, context))
                new_state = {
                    "current_mode": target_mode,
                    "last_change": self._iso_now(),
                    "requested_by": requested_by,
                    "config_hash": config_hash,
                }
                self._write_mode_state(new_state)

                epd_state = self._collect_epd_state(
                    mode=target_mode,
                    mode_last_change=new_state["last_change"],
                    context=context,
                    last_error="",
                )
                self._write_epd_state(epd_state, trigger_refresh=True)
                self._log_mode_change(prev_mode, target_mode, requested_by, "ok", "")
                return {
                    "ok": True,
                    "from": prev_mode,
                    "to": target_mode,
                    "requested_by": requested_by,
                    "config_hash": config_hash,
                    "verify": verify,
                    "epd_state": epd_state,
                    "ts": self._iso_now(),
                }
            except Exception as exc:
                err = str(exc)
                self.logger.error("mode switch failed: %s", err)
                try:
                    self._restore_from_snapshot(snapshot)
                except Exception as restore_exc:  # pragma: no cover
                    err = f"{err}; rollback failed: {restore_exc}"
                    self.logger.error("rollback failed: %s", restore_exc)

                restored_state = prev_state
                if restored_state.get("current_mode") not in MODE_CHOICES:
                    restored_state = {
                        "current_mode": prev_mode,
                        "last_change": self._iso_now(),
                        "requested_by": "rollback",
                        "config_hash": "",
                    }
                self._write_mode_state(restored_state)

                fail_epd = {
                    "mode": "failed",
                    "target_mode": target_mode,
                    "requested_by": requested_by,
                    "restored_mode": restored_state.get("current_mode", prev_mode),
                    "mode_last_change": restored_state.get("last_change", ""),
                    "last_error": err,
                }
                self._write_epd_state(fail_epd, trigger_refresh=True)

                # Re-render restored steady-state immediately after failed banner.
                if context is None:
                    try:
                        context = self._preflight(str(restored_state.get("current_mode", prev_mode)))
                    except Exception:
                        context = None
                if context is not None:
                    steady_epd = self._collect_epd_state(
                        mode=str(restored_state.get("current_mode", prev_mode)),
                        mode_last_change=str(restored_state.get("last_change", "")),
                        context=context,
                        last_error=err,
                    )
                    self._write_epd_state(steady_epd, trigger_refresh=True)

                self._log_mode_change(prev_mode, target_mode, requested_by, "fail", err)
                return {
                    "ok": False,
                    "from": prev_mode,
                    "to": target_mode,
                    "requested_by": requested_by,
                    "error": err,
                    "restored_mode": restored_state.get("current_mode", prev_mode),
                    "ts": self._iso_now(),
                }

    def apply_default(self, requested_by: str = "boot") -> Dict[str, Any]:
        # Boot default is always shield to prioritize safe startup posture.
        return self.set_mode(DEFAULT_MODE, requested_by=requested_by)

    # ---------- preflight ----------

    def _preflight(self, target_mode: str) -> PreflightContext:
        for ifname in (CANARY_HOST_IF, CANARY_NS_IF):
            if len(ifname) > 15:
                raise ModeError(f"invalid canary interface name (too long): {ifname}")

        usb_if, upstream_if, mgmt_subnet, mgmt_ip = self._resolve_interfaces()
        if not self._iface_exists(usb_if):
            raise ModeError(f"usb interface not found: {usb_if}")
        if not self._iface_exists(upstream_if):
            raise ModeError(f"upstream interface not found: {upstream_if}")

        fw_backend = self._detect_firewall_backend(raise_on_missing=True)

        if not (shutil.which("dnsmasq") or self._service_active("azazel-first-minute.service")):
            raise ModeError("DHCP/DNS readiness check failed: dnsmasq binary or azazel-first-minute.service missing")

        canary_ports = self._resolve_canary_ports()
        if target_mode == "scapegoat":
            if not canary_ports:
                raise ModeError("OpenCanary allowlist is empty")
            if not (Path("/home/azazel/canary-venv/bin/opencanaryd").exists() or shutil.which("opencanaryd")):
                raise ModeError("scapegoat requested but opencanaryd not found")

        epd_available = Path("/usr/local/bin/azazel-epd-refresh").exists() or Path(
            "/etc/systemd/system/azazel-epd-refresh.service"
        ).exists()

        return PreflightContext(
            usb_if=usb_if,
            upstream_if=upstream_if,
            mgmt_subnet=mgmt_subnet,
            mgmt_ip=mgmt_ip,
            fw_backend=fw_backend,
            canary_ports=canary_ports,
            epd_available=epd_available,
        )

    # ---------- apply ----------

    def _apply_mode(self, mode: str, context: PreflightContext) -> None:
        self._apply_sysctl(context)

        if mode == "scapegoat":
            self._ensure_canary_namespace()
            self._stop_service_if_present("opencanary.service")
            self._disable_service_if_present("opencanary.service")
            self._start_canary_isolated()
        else:
            self._stop_service_if_present("opencanary@az_canary.service")
            self._stop_service_if_present("opencanary.service")
            self._disable_service_if_present("opencanary.service")
            self._teardown_canary_namespace()

        if context.fw_backend == "nft":
            rules = render_nft_rules(
                mode=mode,
                usb_if=context.usb_if,
                upstream_if=context.upstream_if,
                mgmt_subnet=context.mgmt_subnet,
                canary_ports=context.canary_ports,
            )
            self._run(["nft", "delete", "table", "inet", "azazel"], check=False)
            self._run(["nft", "-f", "-"], input_text=rules, check=True)
        else:
            self._apply_iptables(mode, context)

    # ---------- verify ----------

    def _verify_invariants(self, mode: str, context: PreflightContext) -> Dict[str, Any]:
        inv: Dict[str, Any] = {
            "A_no_wlan_to_usb_new": False,
            "B_usb_internet": "unknown",
            "C_shield_no_wlan_open": True,
            "D_scapegoat_allowlist_only": True,
            "E_scapegoat_canary_running": True,
            "reason": "",
            "details": {},
            "ok": False,
        }

        if context.fw_backend == "nft":
            forward_text = self._run(["nft", "list", "chain", "inet", "azazel", "forward"], check=False).stdout
            pat_est = rf'iifname "{re.escape(context.upstream_if)}" oifname "{re.escape(context.usb_if)}" ct state established,related accept'
            has_est = re.search(pat_est, forward_text or "") is not None
            has_bad = re.search(
                rf'iifname "{re.escape(context.upstream_if)}" oifname "{re.escape(context.usb_if)}".*ct state new',
                forward_text or "",
            ) is not None
            inv["A_no_wlan_to_usb_new"] = bool(has_est and not has_bad)

            if mode == "shield":
                input_text = self._run(["nft", "list", "chain", "inet", "azazel", "input"], check=False).stdout
                allow_wlan = re.search(
                    rf'iifname "{re.escape(context.upstream_if)}" tcp dport', input_text or ""
                ) is not None
                inv["C_shield_no_wlan_open"] = not allow_wlan

            if mode == "scapegoat":
                nat_text = self._run(["nft", "list", "chain", "inet", "azazel", "nat_prerouting"], check=False).stdout
                allow_str = "{ " + ", ".join(str(p) for p in context.canary_ports) + " }"
                inv["D_scapegoat_allowlist_only"] = allow_str in (nat_text or "")
                opencanary_state = self._opencanary_state()
                inv["details"]["opencanary"] = opencanary_state
                inv["E_scapegoat_canary_running"] = opencanary_state == "ON"
                inv["details"]["canary_ns_pids"] = self._run(
                    ["ip", "netns", "pids", CANARY_NS], check=False
                ).stdout.strip().split()

        else:
            # iptables backend: invariant A is encoded by forward policy in our dedicated chain.
            inv["A_no_wlan_to_usb_new"] = True
            if mode == "shield":
                inv["C_shield_no_wlan_open"] = True
            if mode == "scapegoat":
                inv["D_scapegoat_allowlist_only"] = bool(context.canary_ports)
                opencanary_state = self._opencanary_state()
                inv["details"]["opencanary"] = opencanary_state
                inv["E_scapegoat_canary_running"] = opencanary_state == "ON"

        if mode in ("portal", "shield"):
            internet_ok = self._quick_internet_check(context.upstream_if)
            dns_ok = self._quick_dns_check()
            inv["B_usb_internet"] = "ok" if (internet_ok and dns_ok) else "fail"
            inv["details"]["internet_check"] = {"ping": internet_ok, "dns": dns_ok}

        if mode == "scapegoat":
            if not inv["D_scapegoat_allowlist_only"]:
                inv["reason"] = "scapegoat allowlist forwarding mismatch"
            elif not inv["E_scapegoat_canary_running"]:
                inv["reason"] = "OpenCanary is not running in scapegoat mode"

        inv["ok"] = bool(
            inv["A_no_wlan_to_usb_new"]
            and (inv["C_shield_no_wlan_open"] if mode == "shield" else True)
            and (inv["D_scapegoat_allowlist_only"] if mode == "scapegoat" else True)
            and (inv["E_scapegoat_canary_running"] if mode == "scapegoat" else True)
        )
        return inv

    # ---------- firewall backends ----------

    def _apply_iptables(self, mode: str, context: PreflightContext) -> None:
        canary_ports = ",".join(str(p) for p in context.canary_ports)

        cmds: List[List[str]] = [
            ["iptables", "-N", "AZAZEL_INPUT"],
            ["iptables", "-N", "AZAZEL_FORWARD"],
            ["iptables", "-t", "nat", "-N", "AZAZEL_POSTROUTING"],
            ["iptables", "-t", "nat", "-N", "AZAZEL_PREROUTING"],
        ]
        for cmd in cmds:
            self._run(cmd, check=False)

        flush_cmds = [
            ["iptables", "-F", "AZAZEL_INPUT"],
            ["iptables", "-F", "AZAZEL_FORWARD"],
            ["iptables", "-t", "nat", "-F", "AZAZEL_POSTROUTING"],
            ["iptables", "-t", "nat", "-F", "AZAZEL_PREROUTING"],
        ]
        for cmd in flush_cmds:
            self._run(cmd, check=False)

        self._ensure_iptables_jump("INPUT", "AZAZEL_INPUT")
        self._ensure_iptables_jump("FORWARD", "AZAZEL_FORWARD")
        self._ensure_iptables_jump("POSTROUTING", "AZAZEL_POSTROUTING", table="nat")
        self._ensure_iptables_jump("PREROUTING", "AZAZEL_PREROUTING", table="nat")

        rules = [
            ["iptables", "-A", "AZAZEL_INPUT", "-i", "lo", "-j", "ACCEPT"],
            [
                "iptables",
                "-A",
                "AZAZEL_INPUT",
                "-m",
                "conntrack",
                "--ctstate",
                "ESTABLISHED,RELATED",
                "-j",
                "ACCEPT",
            ],
            [
                "iptables",
                "-A",
                "AZAZEL_INPUT",
                "-i",
                context.usb_if,
                "-p",
                "udp",
                "-m",
                "multiport",
                "--dports",
                "53,67,68",
                "-j",
                "ACCEPT",
            ],
            [
                "iptables",
                "-A",
                "AZAZEL_INPUT",
                "-i",
                context.usb_if,
                "-p",
                "tcp",
                "-m",
                "multiport",
                "--dports",
                "22,53,8082,8083,8084,6080",
                "-j",
                "ACCEPT",
            ],
            [
                "iptables",
                "-A",
                "AZAZEL_FORWARD",
                "-m",
                "conntrack",
                "--ctstate",
                "ESTABLISHED,RELATED",
                "-j",
                "ACCEPT",
            ],
            ["iptables", "-A", "AZAZEL_FORWARD", "-i", context.usb_if, "-o", context.upstream_if, "-j", "ACCEPT"],
            [
                "iptables",
                "-A",
                "AZAZEL_FORWARD",
                "-i",
                context.upstream_if,
                "-o",
                context.usb_if,
                "-m",
                "conntrack",
                "--ctstate",
                "ESTABLISHED,RELATED",
                "-j",
                "ACCEPT",
            ],
            [
                "iptables",
                "-t",
                "nat",
                "-A",
                "AZAZEL_POSTROUTING",
                "-s",
                context.mgmt_subnet,
                "-o",
                context.upstream_if,
                "-j",
                "MASQUERADE",
            ],
        ]

        if mode == "scapegoat" and canary_ports:
            rules.extend(
                [
                    [
                        "iptables",
                        "-t",
                        "nat",
                        "-A",
                        "AZAZEL_PREROUTING",
                        "-i",
                        context.upstream_if,
                        "-p",
                        "tcp",
                        "-m",
                        "multiport",
                        "--dports",
                        canary_ports,
                        "-j",
                        "DNAT",
                        "--to-destination",
                        CANARY_NS_ADDR,
                    ],
                    [
                        "iptables",
                        "-A",
                        "AZAZEL_FORWARD",
                        "-i",
                        context.upstream_if,
                        "-o",
                        CANARY_HOST_IF,
                        "-p",
                        "tcp",
                        "-m",
                        "multiport",
                        "--dports",
                        canary_ports,
                        "-j",
                        "ACCEPT",
                    ],
                    [
                        "iptables",
                        "-A",
                        "AZAZEL_FORWARD",
                        "-i",
                        CANARY_HOST_IF,
                        "-o",
                        context.upstream_if,
                        "-m",
                        "conntrack",
                        "--ctstate",
                        "ESTABLISHED,RELATED",
                        "-j",
                        "ACCEPT",
                    ],
                ]
            )

        rules.append(["iptables", "-A", "AZAZEL_INPUT", "-j", "DROP"])
        rules.append(["iptables", "-A", "AZAZEL_FORWARD", "-j", "DROP"])

        for rule in rules:
            self._run(rule, check=True)

    def _ensure_iptables_jump(self, chain: str, jump_chain: str, table: str = "filter") -> None:
        base = ["iptables"]
        if table != "filter":
            base.extend(["-t", table])
        check_cmd = base + ["-C", chain, "-j", jump_chain]
        if self._run(check_cmd, check=False).returncode == 0:
            return
        insert_cmd = base + ["-I", chain, "1", "-j", jump_chain]
        self._run(insert_cmd, check=True)

    # ---------- netns / canary ----------

    def _ensure_canary_namespace(self) -> None:
        if self._run(["ip", "netns", "list"], check=False).stdout.find(CANARY_NS) < 0:
            self._run(["ip", "netns", "add", CANARY_NS], check=True)

        if self._run(["ip", "link", "show", CANARY_HOST_IF], check=False).returncode != 0:
            self._run(["ip", "link", "add", CANARY_HOST_IF, "type", "veth", "peer", "name", CANARY_NS_IF], check=True)
        self._run(["ip", "link", "set", CANARY_NS_IF, "netns", CANARY_NS], check=False)

        self._run(["ip", "addr", "flush", "dev", CANARY_HOST_IF], check=False)
        self._run(["ip", "addr", "add", CANARY_HOST_IP, "dev", CANARY_HOST_IF], check=False)
        self._run(["ip", "link", "set", CANARY_HOST_IF, "up"], check=True)

        self._run(["ip", "netns", "exec", CANARY_NS, "ip", "link", "set", "lo", "up"], check=False)
        self._run(["ip", "netns", "exec", CANARY_NS, "ip", "addr", "flush", "dev", CANARY_NS_IF], check=False)
        self._run(["ip", "netns", "exec", CANARY_NS, "ip", "addr", "add", CANARY_NS_IP, "dev", CANARY_NS_IF], check=False)
        self._run(["ip", "netns", "exec", CANARY_NS, "ip", "link", "set", CANARY_NS_IF, "up"], check=False)
        self._run(
            ["ip", "netns", "exec", CANARY_NS, "ip", "route", "replace", "default", "via", CANARY_HOST_IP.split("/")[0], "dev", CANARY_NS_IF],
            check=False,
        )

    def _teardown_canary_namespace(self) -> None:
        self._run(["ip", "link", "del", CANARY_HOST_IF], check=False)
        pids = self._run(["ip", "netns", "pids", CANARY_NS], check=False).stdout.strip().split()
        for pid in pids:
            if pid.isdigit():
                self._run(["kill", "-TERM", pid], check=False)
        self._run(["ip", "netns", "del", CANARY_NS], check=False)

    def _start_canary_isolated(self) -> None:
        self._cleanup_stale_canary_processes()

        if self._unit_exists("opencanary@.service"):
            self._run(["systemctl", "start", f"opencanary@{CANARY_NS}.service"], check=True)
            return

        start_script = Path("/usr/local/bin/opencanary-start")
        if start_script.exists():
            self._run(["ip", "netns", "exec", CANARY_NS, str(start_script)], check=True)
            return

        binary = Path("/home/azazel/canary-venv/bin/opencanaryd")
        if not binary.exists():
            raise ModeError("isolated canary start failed: opencanaryd binary missing")
        self._run(
            ["ip", "netns", "exec", CANARY_NS, str(binary), "--start", "--uid=nobody", "--gid=nogroup"],
            check=True,
        )

    def _cleanup_stale_canary_processes(self) -> None:
        # Stop any legacy/global unit first.
        self._stop_service_if_present("opencanary.service")

        # Kill orphaned twistd/opencanaryd processes that may still hold the global pidfile.
        for pattern in (
            "opencanary.tac",
            "/home/azazel/canary-venv/bin/opencanaryd",
            "opencanaryd.pid",
        ):
            self._run(["pkill", "-f", pattern], check=False)
        self._run(["pkill", "-9", "-f", "opencanary.tac"], check=False)

        try:
            if CANARY_PIDFILE.exists():
                CANARY_PIDFILE.unlink()
        except Exception:
            pass

    # ---------- snapshots / rollback ----------

    def _take_snapshots(self) -> Dict[str, Any]:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

        backend = self._detect_firewall_backend(raise_on_missing=False)
        fw_path = SNAPSHOT_DIR / f"{backend}-{ts}.txt"
        if backend == "nft":
            fw_dump = self._run(["nft", "list", "ruleset"], check=False).stdout
        elif backend == "iptables":
            fw_dump = self._run(["iptables-save"], check=False).stdout
        else:
            fw_dump = ""
        fw_path.write_text(fw_dump or "", encoding="utf-8")

        services: Dict[str, str] = {}
        for unit in self._managed_units():
            if not self._unit_exists(unit):
                continue
            active = self._run(["systemctl", "is-active", unit], check=False).stdout.strip()
            services[unit] = active

        return {"backend": backend, "fw_snapshot": str(fw_path), "services": services}

    def _restore_from_snapshot(self, snapshot: Dict[str, Any]) -> None:
        backend = str(snapshot.get("backend", ""))
        fw_snapshot = Path(str(snapshot.get("fw_snapshot", "")))
        if fw_snapshot.exists():
            if backend == "nft":
                self._run(["nft", "-f", str(fw_snapshot)], check=False)
            elif backend == "iptables":
                self._run(["iptables-restore", str(fw_snapshot)], check=False)

        services = snapshot.get("services", {})
        if isinstance(services, dict):
            for unit, state in services.items():
                if not self._unit_exists(unit):
                    continue
                state_text = str(state).strip().lower()
                if state_text == "active":
                    self._run(["systemctl", "start", unit], check=False)
                else:
                    self._run(["systemctl", "stop", unit], check=False)

    # ---------- state / config ----------

    def _mode_path_candidates(self) -> List[Path]:
        return [
            Path("/etc/azazel/mode.json"),
            Path("/etc/azazel-gadget/mode.json"),
            Path("/etc/azazel-zero/mode.json"),
        ]

    def _mode_path(self) -> Path:
        for p in self._mode_path_candidates():
            if p.exists():
                return p
        return self._mode_path_candidates()[0]

    def _read_mode_state(self) -> Dict[str, Any]:
        path = self._mode_path()
        data = self._read_json(path)
        if not isinstance(data, dict):
            return {
                "current_mode": DEFAULT_MODE,
                "last_change": "",
                "requested_by": "",
                "config_hash": "",
            }
        if str(data.get("current_mode", "")).strip().lower() not in MODE_CHOICES:
            data["current_mode"] = DEFAULT_MODE
        return data

    def _write_mode_state(self, state: Dict[str, Any]) -> None:
        path = self._mode_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(path, state)

    def _effective_config(self, mode: str, context: PreflightContext) -> Dict[str, Any]:
        return {
            "mode": mode,
            "usb_if": context.usb_if,
            "upstream_if": context.upstream_if,
            "mgmt_subnet": context.mgmt_subnet,
            "mgmt_ip": context.mgmt_ip,
            "fw_backend": context.fw_backend,
            "canary_ports": context.canary_ports,
        }

    def _hash_effective_config(self, data: Dict[str, Any]) -> str:
        encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    # ---------- epd ----------

    def _collect_epd_state(
        self,
        mode: str,
        mode_last_change: str,
        context: PreflightContext,
        last_error: str,
    ) -> Dict[str, Any]:
        internet = "ok" if self._quick_internet_check(context.upstream_if) else "fail"
        dns_ok = self._quick_dns_check()
        dhcp_ok = self._dnsmasq_running()
        opencanary_state = self._opencanary_state()

        base: Dict[str, Any] = {
            "mode": mode,
            "mode_last_change": mode_last_change,
            "upstream_if": context.upstream_if,
            "usb_if": context.usb_if,
            "upstream_ip": self._iface_ip(context.upstream_if),
            "usb_ip": self._iface_ip(context.usb_if),
            "internet": internet,
            "dhcp": "ok" if dhcp_ok else "fail",
            "dns": "ok" if dns_ok else "fail",
            "opencanary": "on" if opencanary_state == "ON" else "off",
            "exposed_ports": context.canary_ports if mode == "scapegoat" else [],
            "fw_backend": context.fw_backend,
            "last_error": last_error,
        }
        if mode == "scapegoat":
            base["ns"] = CANARY_NS
        return base

    def _write_epd_state(self, payload: Dict[str, Any], trigger_refresh: bool = True) -> None:
        EPD_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write_json(EPD_STATE_PATH, payload)
        if trigger_refresh:
            self._trigger_epd_refresh()

    def _trigger_epd_refresh(self) -> None:
        # If refresh is currently running (e.g. "switching" banner), wait briefly and then queue a clean start
        # so the final steady-state screen is rendered without killing the in-flight refresh job.
        for _ in range(30):
            active = self._run(
                ["systemctl", "is-active", "azazel-epd-refresh.service"],
                check=False,
                timeout=2,
            ).stdout.strip().lower()
            if active not in {"active", "activating"}:
                break
            time.sleep(0.2)

        result = self._run(
            ["systemctl", "start", "--no-block", "azazel-epd-refresh.service"],
            check=False,
            timeout=2,
        )
        if result.returncode == 0:
            return

        # Fallback for environments where the systemd refresh unit is not installed yet.
        refresh_bin = Path("/usr/local/bin/azazel-epd-refresh")
        if refresh_bin.exists():
            self._run([str(refresh_bin)], check=False, timeout=45)

    # ---------- helpers ----------

    def _resolve_interfaces(self) -> Tuple[str, str, str, str]:
        defaults = self._load_defaults()
        cfg = self._load_first_minute_config()

        usb_if = str(defaults.get("USB_IF") or cfg.get("interfaces", {}).get("downstream") or "usb0").strip()
        mgmt_subnet = str(defaults.get("MGMT_SUBNET") or cfg.get("interfaces", {}).get("mgmt_subnet") or "10.55.0.0/24").strip()
        mgmt_ip = str(defaults.get("MGMT_IP") or cfg.get("interfaces", {}).get("mgmt_ip") or "10.55.0.10").strip()

        configured_up = str(
            defaults.get("AZAZEL_UP_IF")
            or defaults.get("WAN_IF")
            or cfg.get("interfaces", {}).get("upstream")
            or ""
        ).strip()

        upstream_if = ""
        if configured_up and configured_up.lower() not in ("auto", "") and self._iface_exists(configured_up):
            upstream_if = configured_up
        elif self._iface_exists("wlan0"):
            upstream_if = "wlan0"
        else:
            route_dev = self._default_route_iface()
            if route_dev and route_dev != usb_if:
                upstream_if = route_dev

        if not upstream_if:
            for cand in self._list_ifaces():
                if cand not in ("lo", usb_if):
                    upstream_if = cand
                    break

        if not upstream_if:
            upstream_if = "wlan0"

        return usb_if, upstream_if, mgmt_subnet, mgmt_ip

    def _resolve_canary_ports(self) -> List[int]:
        env_ports = str(os.environ.get("AZAZEL_CANARY_PORTS", "")).strip()
        if env_ports:
            parsed: List[int] = []
            for token in env_ports.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    val = int(token)
                except Exception:
                    continue
                if 1 <= val <= 65535 and val not in parsed:
                    parsed.append(val)
            return sorted(parsed)

        cfg_path = self._resolve_canary_config_path()
        return extract_opencanary_ports(cfg_path)

    def _resolve_canary_config_path(self) -> Path:
        candidates = [
            Path("/etc/opencanaryd/opencanary.conf"),
            Path("/etc/azazel-gadget/opencanary.conf"),
            Path("/etc/azazel-zero/opencanary.conf"),
            Path(__file__).resolve().parents[2] / "configs" / "opencanary.conf",
        ]
        for path in candidates:
            if path.exists():
                return path
        return candidates[0]

    def _detect_firewall_backend(self, raise_on_missing: bool = True) -> str:
        if shutil.which("nft"):
            return "nft"
        if shutil.which("iptables") and shutil.which("iptables-save"):
            return "iptables"
        if raise_on_missing:
            raise ModeError("No firewall backend available (nft/iptables)")
        return "none"

    def _apply_sysctl(self, context: PreflightContext) -> None:
        for key, value in (
            ("net.ipv4.ip_forward", "1"),
            ("net.ipv4.conf.all.rp_filter", "2"),
            ("net.ipv4.conf.default.rp_filter", "2"),
            (f"net.ipv4.conf.{context.usb_if}.rp_filter", "2"),
            (f"net.ipv4.conf.{context.upstream_if}.rp_filter", "2"),
        ):
            self._run(["sysctl", "-w", f"{key}={value}"], check=False)

    def _opencanary_state(self) -> str:
        if self._service_active("opencanary@az_canary.service") or self._service_active("opencanary.service"):
            return "ON"
        pidfile = Path("/home/azazel/canary-venv/bin/opencanaryd.pid")
        if pidfile.exists():
            pid: Optional[int] = None
            try:
                pid = int(pidfile.read_text(encoding="utf-8").strip())
            except Exception:
                pid = None
            if pid:
                try:
                    os.kill(pid, 0)
                    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", errors="ignore")
                    if "opencanary" in cmdline:
                        return "ON"
                except PermissionError:
                    if Path(f"/proc/{pid}").exists():
                        try:
                            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode("utf-8", errors="ignore")
                            if "opencanary" in cmdline:
                                return "ON"
                        except Exception:
                            return "ON"
                except Exception:
                    pass
        if self._run(["pgrep", "-f", "[o]pencanary.tac"], check=False).returncode == 0:
            return "ON"
        return "OFF"

    def _dnsmasq_running(self) -> bool:
        if self._service_active("dnsmasq.service"):
            return True
        return self._run(["pgrep", "-f", "dnsmasq"], check=False).returncode == 0

    def _quick_internet_check(self, upstream_if: str) -> bool:
        cmd = ["ping", "-I", upstream_if, "-c", "1", "-W", "1", "1.1.1.1"]
        return self._run(cmd, check=False, timeout=2).returncode == 0

    def _quick_dns_check(self) -> bool:
        cmd = ["getent", "hosts", "example.com"]
        return self._run(cmd, check=False, timeout=2).returncode == 0

    def _iface_exists(self, iface: str) -> bool:
        return bool(iface) and Path(f"/sys/class/net/{iface}").exists()

    def _iface_ip(self, iface: str) -> str:
        out = self._run(["ip", "-4", "-o", "addr", "show", "dev", iface], check=False).stdout.strip()
        if not out:
            return ""
        parts = out.split()
        for i, token in enumerate(parts):
            if token == "inet" and i + 1 < len(parts):
                return parts[i + 1].split("/")[0]
        return ""

    def _default_route_iface(self) -> str:
        out = self._run(["ip", "route", "show", "default"], check=False).stdout
        for line in out.splitlines():
            parts = line.strip().split()
            if "dev" in parts:
                idx = parts.index("dev")
                if idx + 1 < len(parts):
                    return parts[idx + 1]
        return ""

    def _list_ifaces(self) -> List[str]:
        out = self._run(["ip", "-o", "link", "show"], check=False).stdout
        names: List[str] = []
        for line in out.splitlines():
            if ": " not in line:
                continue
            name = line.split(": ", 1)[1].split(":", 1)[0].strip()
            if "@" in name:
                name = name.split("@", 1)[0]
            if name and name not in names:
                names.append(name)
        return names

    def _normalize_mode(self, mode: str) -> str:
        value = str(mode or "").strip().lower()
        if value not in MODE_CHOICES:
            raise ModeError(f"Unknown mode: {mode}")
        return value

    def _managed_units(self) -> List[str]:
        return [
            "opencanary.service",
            "opencanary@az_canary.service",
            "azazel-first-minute.service",
            "azazel-control-daemon.service",
            "azazel-web.service",
        ]

    def _unit_exists(self, unit: str) -> bool:
        if not shutil.which("systemctl"):
            return False
        cmd = ["systemctl", "list-unit-files", unit]
        return self._run(cmd, check=False, timeout=4).returncode == 0

    def _service_active(self, unit: str) -> bool:
        if not shutil.which("systemctl"):
            return False
        res = self._run(["systemctl", "is-active", unit], check=False, timeout=3)
        return res.returncode == 0 and res.stdout.strip() == "active"

    def _stop_service_if_present(self, unit: str) -> None:
        if self._unit_exists(unit):
            self._run(["systemctl", "stop", unit], check=False)

    def _disable_service_if_present(self, unit: str) -> None:
        if self._unit_exists(unit):
            self._run(["systemctl", "disable", unit], check=False)

    def _load_defaults(self) -> Dict[str, str]:
        defaults: Dict[str, str] = {}
        for path in (Path("/etc/default/azazel-gadget"), Path("/etc/default/azazel-zero")):
            if not path.exists():
                continue
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                key, val = line.split("=", 1)
                defaults[key.strip()] = val.strip().strip('"').strip("'")
            break
        return defaults

    def _load_first_minute_config(self) -> Dict[str, Any]:
        if yaml is None:
            return {}
        candidates = [
            Path("/etc/azazel-gadget/first_minute.yaml"),
            Path("/etc/azazel-zero/first_minute.yaml"),
            Path(__file__).resolve().parents[2] / "configs" / "first_minute.yaml",
        ]
        for path in candidates:
            if not path.exists():
                continue
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    return raw
            except Exception:
                continue
        return {}

    def _read_json(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _atomic_write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)

    def _log_mode_change(self, old: str, new: str, requested_by: str, result: str, reason: str) -> None:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": self._iso_now(),
            "from": old,
            "to": new,
            "by": requested_by,
            "result": result,
            "reason": reason,
        }
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _run(
        self,
        cmd: List[str],
        check: bool = False,
        timeout: float = 8,
        input_text: Optional[str] = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            cp = subprocess.run(
                cmd,
                input=input_text,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=check,
            )
            return cp
        except subprocess.CalledProcessError as exc:
            if check:
                raise ModeError(f"command failed: {' '.join(cmd)}: {(exc.stderr or exc.stdout).strip()}") from exc
            return exc
        except Exception as exc:
            if check:
                raise ModeError(f"command failed: {' '.join(cmd)}: {exc}") from exc
            return subprocess.CompletedProcess(cmd, 1, "", str(exc))

    def _iso_now(self) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# ---------- standalone helpers for tests ----------


def extract_opencanary_ports(config_path: Path) -> List[int]:
    """Extract enabled OpenCanary TCP ports from config JSON."""
    if not config_path.exists():
        return [22, 80]
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return [22, 80]

    if not isinstance(data, dict):
        return [22, 80]

    ports: List[int] = []
    for key, enabled in data.items():
        if not key.endswith(".enabled"):
            continue
        if not bool(enabled):
            continue
        base = key[: -len(".enabled")]
        port_key = f"{base}.port"
        port_val = data.get(port_key)
        try:
            port = int(port_val)
        except Exception:
            continue
        if 1 <= port <= 65535 and port not in ports:
            ports.append(port)

    # Fallback defaults if config has no enabled entries.
    if not ports:
        return [22, 80]
    return sorted(ports)


def render_nft_rules(
    mode: str,
    usb_if: str,
    upstream_if: str,
    mgmt_subnet: str,
    canary_ports: Iterable[int],
) -> str:
    mode_norm = str(mode).strip().lower()
    if mode_norm not in MODE_CHOICES:
        raise ValueError(f"invalid mode: {mode}")

    allow_ports = [int(p) for p in canary_ports if 1 <= int(p) <= 65535]
    allow_ports = sorted(dict.fromkeys(allow_ports))
    allow_port_text = "{ " + ", ".join(str(p) for p in allow_ports) + " }"

    lines = [
        "add table inet azazel",
        "add chain inet azazel input { type filter hook input priority 0; policy drop; }",
        "add chain inet azazel forward { type filter hook forward priority 0; policy drop; }",
        "add chain inet azazel output { type filter hook output priority 0; policy accept; }",
        "add chain inet azazel nat_prerouting { type nat hook prerouting priority -100; policy accept; }",
        "add chain inet azazel nat_postrouting { type nat hook postrouting priority 100; policy accept; }",
        "",
        "# Common input rules",
        "add rule inet azazel input iifname \"lo\" accept",
        "add rule inet azazel input ct state established,related accept",
        "add rule inet azazel input ip protocol icmp accept",
        "add rule inet azazel input ip6 nexthdr icmpv6 accept",
        f"add rule inet azazel input iifname \"{usb_if}\" udp dport {{ 53, 67, 68 }} accept",
        f"add rule inet azazel input iifname \"{usb_if}\" tcp dport {{ 22, 53, 8082, 8083, 8084, 6080 }} accept",
        f"add rule inet azazel input iifname \"{usb_if}\" accept",
        "",
        "# Common forwarding rules",
        "add rule inet azazel forward ct state established,related accept",
        f"add rule inet azazel forward iifname \"{usb_if}\" oifname \"{upstream_if}\" ct state new,established,related accept",
        f"add rule inet azazel forward iifname \"{upstream_if}\" oifname \"{usb_if}\" ct state established,related accept",
    ]

    if mode_norm == "scapegoat" and allow_ports:
        lines.extend(
            [
                "",
                "# Scapegoat decoy forwarding into isolated canary namespace",
                f"add rule inet azazel forward iifname \"{upstream_if}\" oifname \"{CANARY_HOST_IF}\" tcp dport {allow_port_text} ct state new,established,related accept",
                f"add rule inet azazel forward iifname \"{CANARY_HOST_IF}\" oifname \"{upstream_if}\" ct state established,related accept",
                f"add rule inet azazel forward iifname \"{CANARY_HOST_IF}\" oifname \"{usb_if}\" drop",
                f"add rule inet azazel forward iifname \"{usb_if}\" oifname \"{CANARY_HOST_IF}\" drop",
                f"add rule inet azazel nat_prerouting iifname \"{upstream_if}\" tcp dport {allow_port_text} dnat ip to {CANARY_NS_ADDR}",
            ]
        )

    lines.extend(
        [
            "",
            "# NAT",
            f"add rule inet azazel nat_postrouting oifname \"{upstream_if}\" ip saddr {mgmt_subnet} masquerade",
        ]
    )

    if mode_norm == "scapegoat" and allow_ports:
        lines.append(f"add rule inet azazel nat_postrouting oifname \"{upstream_if}\" ip saddr {CANARY_NS_ADDR} masquerade")

    return "\n".join(lines) + "\n"
