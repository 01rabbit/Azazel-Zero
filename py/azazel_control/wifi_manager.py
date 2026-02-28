#!/usr/bin/env python3
"""State-machine Wi-Fi connection manager with fallback and recovery."""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from azazel_control.connectivity_checker import run_connectivity_checks
from azazel_control.recovery_engine import RecoveryEngine
from azazel_control.wifi_diagnostics import WifiDiagnosticsSession


STATE_IDLE = "IDLE"
STATE_SCANNING = "SCANNING"
STATE_ASSOCIATING = "ASSOCIATING"
STATE_AUTHENTICATING = "AUTHENTICATING"
STATE_DHCP = "DHCP"
STATE_ROUTING = "ROUTING"
STATE_CONNECTIVITY = "CONNECTIVITY"
STATE_CONNECTED = "CONNECTED"


def parse_security(security: str) -> Dict[str, bool]:
    sec = (security or "").upper().strip()
    return {
        "open": sec == "OPEN",
        "wpa3": "WPA3" in sec,
        "wpa2": "WPA2" in sec,
        "wpa": ("WPA" in sec) and ("WPA2" not in sec) and ("WPA3" not in sec),
    }


def _split_nmcli_terse_line(line: str, expected_fields: int) -> List[str]:
    fields: List[str] = []
    cur: List[str] = []
    escaped = False
    for ch in line:
        if escaped:
            cur.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == ":" and len(fields) < expected_fields - 1:
            fields.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    fields.append("".join(cur))
    if len(fields) < expected_fields:
        fields.extend([""] * (expected_fields - len(fields)))
    return fields[:expected_fields]


@dataclass
class ConnectStrategy:
    name: str
    force_wpa2: bool = False
    pmf_optional: bool = False
    recovery_level: int = 0


class WifiManager:
    def __init__(self, iface: str):
        self.iface = iface
        self.total_timeout_sec = int(os.environ.get("AZAZEL_WIFI_TOTAL_TIMEOUT_SEC", "90"))
        self.state_timeout = {
            STATE_SCANNING: int(os.environ.get("AZAZEL_WIFI_SCAN_TIMEOUT_SEC", "15")),
            STATE_ASSOCIATING: int(os.environ.get("AZAZEL_WIFI_ASSOC_TIMEOUT_SEC", "20")),
            STATE_AUTHENTICATING: int(os.environ.get("AZAZEL_WIFI_AUTH_TIMEOUT_SEC", "20")),
            STATE_DHCP: int(os.environ.get("AZAZEL_WIFI_DHCP_TIMEOUT_SEC", "25")),
            STATE_ROUTING: int(os.environ.get("AZAZEL_WIFI_ROUTE_TIMEOUT_SEC", "10")),
            STATE_CONNECTIVITY: int(os.environ.get("AZAZEL_WIFI_CONNECTIVITY_TIMEOUT_SEC", "12")),
        }
        self.require_connectivity = os.environ.get("AZAZEL_WIFI_REQUIRE_CONNECTIVITY", "0").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self.recovery = RecoveryEngine()
        self.state_trace: List[Dict[str, Any]] = []

    def _run(self, cmd: List[str], timeout: int = 15) -> Dict[str, Any]:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return {
                "ok": proc.returncode == 0,
                "cmd": cmd,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "").strip(),
                "stderr": (proc.stderr or "").strip(),
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "cmd": cmd,
                "returncode": None,
                "stdout": "",
                "stderr": "command timeout",
            }
        except Exception as exc:  # pragma: no cover
            return {
                "ok": False,
                "cmd": cmd,
                "returncode": None,
                "stdout": "",
                "stderr": str(exc),
            }

    def _mark_state(self, state: str, status: str, detail: Optional[Dict[str, Any]] = None, diag: Optional[WifiDiagnosticsSession] = None) -> None:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "state": state,
            "status": status,
        }
        if detail:
            payload["detail"] = detail
        self.state_trace.append(payload)
        if diag:
            diag.add_event(state=state, status=status, detail=detail or {})

    def _scan_networks(self) -> Dict[str, Any]:
        self._run(["nmcli", "dev", "wifi", "rescan", "ifname", self.iface], timeout=self.state_timeout[STATE_SCANNING])
        result = self._run(
            [
                "nmcli",
                "-t",
                "--escape",
                "yes",
                "-f",
                "SSID,SECURITY,BSSID,SIGNAL",
                "dev",
                "wifi",
                "list",
                "ifname",
                self.iface,
            ],
            timeout=self.state_timeout[STATE_SCANNING],
        )
        entries: List[Dict[str, str]] = []
        if result["ok"]:
            for line in result["stdout"].splitlines():
                if not line.strip():
                    continue
                ssid, security, bssid, signal = _split_nmcli_terse_line(line, 4)
                entries.append(
                    {
                        "ssid": ssid,
                        "security": security,
                        "bssid": bssid,
                        "signal": signal,
                    }
                )
        return {
            "ok": result["ok"],
            "error": result["stderr"],
            "entries": entries,
        }

    def _security_transition_mode(self, entries: List[Dict[str, str]], ssid: str) -> bool:
        for row in entries:
            if row.get("ssid") != ssid:
                continue
            sec = (row.get("security") or "").upper()
            if ("SAE" in sec or "WPA3" in sec) and ("WPA2" in sec or "PSK" in sec):
                return True
            if "WPA2 WPA3" in sec:
                return True
        return False

    def _ssid_seen(self, entries: List[Dict[str, str]], ssid: str) -> bool:
        return any(row.get("ssid") == ssid for row in entries)

    def _list_profiles_for_ssid(self, ssid: str) -> List[str]:
        names: List[str] = []
        cons = self._run(["nmcli", "-t", "-f", "NAME,TYPE", "con", "show"], timeout=8)
        if not cons["ok"]:
            return names
        for line in cons["stdout"].splitlines():
            if ":" not in line:
                continue
            name, ctype = line.split(":", 1)
            if ctype != "802-11-wireless":
                continue
            out = self._run(["nmcli", "-g", "802-11-wireless.ssid", "con", "show", name], timeout=8)
            if out["ok"] and out["stdout"].splitlines()[:1] == [ssid]:
                names.append(name)
        return names

    def _delete_profiles_for_ssid(self, ssid: str) -> None:
        for name in self._list_profiles_for_ssid(ssid):
            self._run(["nmcli", "con", "delete", name], timeout=8)

    def _active_connection(self) -> Optional[str]:
        out = self._run(["nmcli", "-t", "-f", "GENERAL.CONNECTION", "dev", "show", self.iface], timeout=6)
        if not out["ok"]:
            return None
        for line in out["stdout"].splitlines():
            if line.startswith("GENERAL.CONNECTION:"):
                value = line.split(":", 1)[1].strip()
                if value and value != "--":
                    return value
        return None

    def _set_autoconnect_off(self, con_name: str) -> None:
        if not con_name:
            return
        self._run(["nmcli", "con", "mod", con_name, "connection.autoconnect", "no"], timeout=6)

    def _sanitize_con_name(self, ssid: str, suffix: str) -> str:
        clean = re.sub(r"[^A-Za-z0-9_.-]", "_", ssid)[:24] or "wifi"
        return f"azazel-{clean}-{suffix}"

    def _activate_with_strategy(
        self,
        ssid: str,
        security: str,
        passphrase: Optional[str],
        strategy: ConnectStrategy,
    ) -> Dict[str, Any]:
        sec_flags = parse_security(security)
        existing = self._list_profiles_for_ssid(ssid)

        if strategy.force_wpa2:
            if not passphrase and not existing:
                return {"ok": False, "error": "WPA2 fallback requires passphrase or existing profile"}

            self._delete_profiles_for_ssid(ssid)
            con_name = self._sanitize_con_name(ssid, strategy.name)
            add = self._run(["nmcli", "con", "add", "type", "wifi", "ifname", self.iface, "con-name", con_name, "ssid", ssid], timeout=10)
            if not add["ok"]:
                return {"ok": False, "error": add["stderr"] or "profile create failed", "raw": add}

            mod_key = self._run(["nmcli", "con", "mod", con_name, "802-11-wireless-security.key-mgmt", "wpa-psk"], timeout=8)
            if not mod_key["ok"]:
                return {"ok": False, "error": mod_key["stderr"] or "failed to set key-mgmt", "raw": mod_key}

            if strategy.pmf_optional:
                self._run(["nmcli", "con", "mod", con_name, "802-11-wireless-security.pmf", "1"], timeout=8)

            if passphrase:
                mod_psk = self._run(["nmcli", "con", "mod", con_name, "802-11-wireless-security.psk", passphrase], timeout=8)
                if not mod_psk["ok"]:
                    return {"ok": False, "error": mod_psk["stderr"] or "failed to set psk", "raw": mod_psk}

            self._run(["nmcli", "con", "mod", con_name, "connection.autoconnect", "no"], timeout=6)
            up = self._run(["nmcli", "con", "up", con_name], timeout=self.state_timeout[STATE_ASSOCIATING])
            return {"ok": up["ok"], "error": up.get("stderr", ""), "raw": up, "connection": con_name}

        # strategy auto
        if sec_flags["open"] or passphrase:
            self._delete_profiles_for_ssid(ssid)

        if not sec_flags["open"] and not passphrase and existing:
            up = self._run(["nmcli", "con", "up", existing[0]], timeout=self.state_timeout[STATE_ASSOCIATING])
            return {"ok": up["ok"], "error": up.get("stderr", ""), "raw": up, "connection": existing[0]}

        cmd = ["nmcli", "dev", "wifi", "connect", ssid, "ifname", self.iface]
        if not sec_flags["open"] and passphrase:
            cmd.extend(["password", passphrase])

        up = self._run(cmd, timeout=self.state_timeout[STATE_ASSOCIATING])
        con_name = self._active_connection() or (existing[0] if existing else "")
        return {"ok": up["ok"], "error": up.get("stderr", ""), "raw": up, "connection": con_name}

    def _is_associated(self) -> bool:
        out = self._run(["iw", "dev", self.iface, "link"], timeout=5)
        if not out["ok"]:
            return False
        return "Connected to" in out["stdout"]

    def _get_ipv4(self) -> str:
        out = self._run(["ip", "-o", "-4", "addr", "show", self.iface], timeout=5)
        if not out["ok"]:
            return ""
        m = re.search(r"inet\s+(\S+)", out["stdout"])
        if not m:
            return ""
        return m.group(1).split("/")[0]

    def _get_default_route(self) -> str:
        out = self._run(["ip", "route", "show", "dev", self.iface], timeout=5)
        if not out["ok"]:
            return ""
        m = re.search(r"default via\s+(\S+)", out["stdout"])
        return m.group(1) if m else ""

    def _wait_for_ip(self, timeout_sec: int) -> str:
        deadline = time.time() + max(1, timeout_sec)
        while time.time() < deadline:
            ip = self._get_ipv4()
            if ip:
                return ip
            time.sleep(1)
        return ""

    def _wait_for_route(self, timeout_sec: int) -> str:
        deadline = time.time() + max(1, timeout_sec)
        while time.time() < deadline:
            gw = self._get_default_route()
            if gw:
                return gw
            time.sleep(1)
        return ""

    @staticmethod
    def _classify_failure(
        nm_error: str,
        ssid_seen: bool,
        associated: bool,
        ip_addr: str,
        gateway: str,
        connectivity_status: str,
    ) -> str:
        err = (nm_error or "").lower()
        if not ssid_seen:
            return "SCAN_FAILURE"
        if "sae" in err or "pmf" in err:
            return "AUTH_NEGOTIATION_FAILURE"
        if "secret" in err or "password" in err or "authentication" in err:
            return "AUTH_FAILURE"
        if "timed out" in err or "timeout" in err:
            return "ASSOC_TIMEOUT"
        if not associated:
            return "ASSOC_FAILURE"
        if not ip_addr:
            return "DHCP_FAILURE"
        if not gateway:
            return "ROUTE_FAILURE"
        if connectivity_status not in ("NO", "SUSPECTED", "YES"):
            return "CONNECTIVITY_FAILURE"
        return "UNKNOWN_FAILURE"

    @staticmethod
    def _recommended_action(category: str) -> str:
        actions = {
            "SCAN_FAILURE": "APに近づくかSSID/BSSID設定を確認して再試行",
            "AUTH_NEGOTIATION_FAILURE": "WPA2強制フォールバックを維持しPMF設定を確認",
            "AUTH_FAILURE": "パスフレーズ・認証方式を確認",
            "ASSOC_TIMEOUT": "無線干渉を避けて再試行、必要ならIF復旧を実行",
            "ASSOC_FAILURE": "ドライバ状態を確認し再スキャン後に再試行",
            "DHCP_FAILURE": "DHCPサーバ状態を確認、再接続を試行",
            "ROUTE_FAILURE": "デフォルトルートとNetworkManagerプロファイルを確認",
            "CONNECTIVITY_FAILURE": "キャプティブポータル/外部到達性を確認",
            "UNKNOWN_FAILURE": "診断バンドルを取得してログ解析",
        }
        return actions.get(category, actions["UNKNOWN_FAILURE"])

    def connect(
        self,
        ssid: str,
        security: str,
        passphrase: Optional[str],
        persist: bool,
        diagnostics: Optional[WifiDiagnosticsSession] = None,
    ) -> Dict[str, Any]:
        _ = persist  # Persist policy is handled by disabling autoconnect for operator-driven behavior.
        started = time.time()
        last_success_state = STATE_IDLE
        last_error = ""
        failure_category = "UNKNOWN_FAILURE"
        attempts: List[Dict[str, Any]] = []

        self._mark_state(STATE_IDLE, "enter", {"ssid": ssid, "iface": self.iface}, diag=diagnostics)

        self._mark_state(STATE_SCANNING, "enter", {}, diag=diagnostics)
        scan = self._scan_networks()
        ssid_seen = self._ssid_seen(scan.get("entries", []), ssid)
        transition_mode = self._security_transition_mode(scan.get("entries", []), ssid)
        self._mark_state(
            STATE_SCANNING,
            "done",
            {
                "ssid_seen": ssid_seen,
                "transition_mode": transition_mode,
                "scan_ok": scan.get("ok", False),
            },
            diag=diagnostics,
        )
        if scan.get("ok"):
            last_success_state = STATE_SCANNING

        sec = parse_security(security)
        strategies: List[ConnectStrategy] = [ConnectStrategy(name="auto")]
        if transition_mode and not sec["open"]:
            strategies.append(ConnectStrategy(name="wpa2_fallback", force_wpa2=True, pmf_optional=True))
        strategies.append(ConnectStrategy(name="recovery_l1", force_wpa2=transition_mode, pmf_optional=True, recovery_level=1))
        strategies.append(ConnectStrategy(name="recovery_l2", force_wpa2=transition_mode, pmf_optional=True, recovery_level=2))
        strategies.append(ConnectStrategy(name="recovery_l3", force_wpa2=transition_mode, pmf_optional=True, recovery_level=3))

        for idx, strategy in enumerate(strategies, start=1):
            if time.time() - started > self.total_timeout_sec:
                last_error = f"overall timeout {self.total_timeout_sec}s"
                failure_category = "ASSOC_TIMEOUT"
                break

            if strategy.recovery_level > 0:
                rec = self.recovery.run(strategy.recovery_level, self.iface)
                self._mark_state(
                    STATE_ASSOCIATING,
                    "recovery",
                    {"attempt": idx, "strategy": strategy.name, "recovery": rec},
                    diag=diagnostics,
                )

            self._mark_state(STATE_ASSOCIATING, "enter", {"attempt": idx, "strategy": strategy.name}, diag=diagnostics)
            activation = self._activate_with_strategy(ssid, security, passphrase, strategy)
            attempts.append({
                "attempt": idx,
                "strategy": strategy.name,
                "recovery_level": strategy.recovery_level,
                "result": activation,
            })

            if not activation.get("ok"):
                last_error = str(activation.get("error") or "activation failed")
                failure_category = self._classify_failure(last_error, ssid_seen, False, "", "", "NA")
                self._mark_state(
                    STATE_ASSOCIATING,
                    "fail",
                    {"attempt": idx, "strategy": strategy.name, "error": last_error},
                    diag=diagnostics,
                )
                continue

            last_success_state = STATE_ASSOCIATING
            self._mark_state(STATE_ASSOCIATING, "done", {"attempt": idx, "strategy": strategy.name}, diag=diagnostics)

            self._mark_state(STATE_AUTHENTICATING, "enter", {"attempt": idx}, diag=diagnostics)
            associated = self._is_associated()
            if not associated:
                failure_category = "ASSOC_FAILURE"
                last_error = "association not established"
                self._mark_state(STATE_AUTHENTICATING, "fail", {"error": last_error}, diag=diagnostics)
                continue
            last_success_state = STATE_AUTHENTICATING
            self._mark_state(STATE_AUTHENTICATING, "done", {"associated": True}, diag=diagnostics)

            self._mark_state(STATE_DHCP, "enter", {}, diag=diagnostics)
            ip_addr = self._wait_for_ip(self.state_timeout[STATE_DHCP])
            if not ip_addr:
                failure_category = "DHCP_FAILURE"
                last_error = "no IPv4 lease"
                self._mark_state(STATE_DHCP, "fail", {"error": last_error}, diag=diagnostics)
                continue
            last_success_state = STATE_DHCP
            self._mark_state(STATE_DHCP, "done", {"ip": ip_addr}, diag=diagnostics)

            self._mark_state(STATE_ROUTING, "enter", {}, diag=diagnostics)
            gateway = self._wait_for_route(self.state_timeout[STATE_ROUTING])
            if not gateway:
                failure_category = "ROUTE_FAILURE"
                last_error = "default route missing"
                self._mark_state(STATE_ROUTING, "fail", {"error": last_error}, diag=diagnostics)
                continue
            last_success_state = STATE_ROUTING
            self._mark_state(STATE_ROUTING, "done", {"gateway": gateway}, diag=diagnostics)

            self._mark_state(STATE_CONNECTIVITY, "enter", {}, diag=diagnostics)
            conn = run_connectivity_checks(self.iface, timeout_sec=self.state_timeout[STATE_CONNECTIVITY])
            captive_status = str(conn.get("captive_status", "NA") or "NA")
            internet_ok = bool(conn.get("internet_ok", False))
            last_success_state = STATE_CONNECTIVITY
            self._mark_state(
                STATE_CONNECTIVITY,
                "done",
                {
                    "captive_status": captive_status,
                    "captive_reason": conn.get("captive_reason", ""),
                    "internet_ok": internet_ok,
                },
                diag=diagnostics,
            )

            if self.require_connectivity and not internet_ok:
                failure_category = "CONNECTIVITY_FAILURE"
                last_error = f"internet check failed ({conn.get('captive_reason', 'unknown')})"
                continue

            active_con = self._active_connection() or str(activation.get("connection") or "")
            self._set_autoconnect_off(active_con)
            self._mark_state(STATE_CONNECTED, "done", {"connection": active_con}, diag=diagnostics)
            return {
                "ok": True,
                "last_success_state": STATE_CONNECTED,
                "failure_category": "",
                "recommended_action": "",
                "error": "",
                "ip_wlan": ip_addr,
                "gateway_ip": gateway,
                "connection": active_con,
                "captive": conn,
                "attempts": attempts,
                "transition_mode": transition_mode,
                "ssid_seen": ssid_seen,
                "state_trace": self.state_trace,
            }

        # final failure classification with best-known runtime info
        ip_addr = self._get_ipv4()
        gateway = self._get_default_route()
        associated = self._is_associated()
        connectivity = run_connectivity_checks(self.iface, timeout_sec=4) if ip_addr else {
            "captive_status": "NA",
            "captive_reason": "NO_IP",
            "internet_ok": False,
            "checks": [],
        }

        if failure_category == "UNKNOWN_FAILURE":
            failure_category = self._classify_failure(
                last_error,
                ssid_seen,
                associated,
                ip_addr,
                gateway,
                str(connectivity.get("captive_status", "NA")),
            )

        self._mark_state(
            STATE_CONNECTED,
            "fail",
            {
                "failure_category": failure_category,
                "error": last_error,
                "last_success_state": last_success_state,
            },
            diag=diagnostics,
        )

        return {
            "ok": False,
            "last_success_state": last_success_state,
            "failure_category": failure_category,
            "recommended_action": self._recommended_action(failure_category),
            "error": last_error or "connection failed",
            "ip_wlan": ip_addr,
            "gateway_ip": gateway,
            "connection": self._active_connection() or "",
            "captive": connectivity,
            "attempts": attempts,
            "transition_mode": transition_mode,
            "ssid_seen": ssid_seen,
            "state_trace": self.state_trace,
        }


__all__ = [
    "WifiManager",
    "STATE_IDLE",
    "STATE_SCANNING",
    "STATE_ASSOCIATING",
    "STATE_AUTHENTICATING",
    "STATE_DHCP",
    "STATE_ROUTING",
    "STATE_CONNECTIVITY",
    "STATE_CONNECTED",
]
