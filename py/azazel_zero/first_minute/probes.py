from __future__ import annotations

import hashlib
import json
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from shutil import which
from typing import Dict, List, Tuple


@dataclass
class ProbeOutcome:
    captive_portal: bool
    tls_mismatch: bool
    dns_mismatch: int
    route_anomaly: bool
    details: Dict[str, object]


def probe_captive_portal(url: str, timeout: int, retries: int) -> Tuple[bool, Dict[str, object]]:
    detail: Dict[str, object] = {"url": url, "status": None}
    for _ in range(max(1, retries + 1)):
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                detail["status"] = resp.status
                body = resp.read(256)  # small only
                if resp.status in (200, 204) and len(body) < 50:
                    return False, detail  # looks normal
                # Redirect or large body indicates captive portal
                return True, detail
        except urllib.error.HTTPError as exc:
            detail["status"] = exc.code
            return True, detail
        except Exception as exc:  # pragma: no cover - network dependent
            detail["error"] = str(exc)
            time.sleep(0.5)
    return True, detail


def probe_tls_endpoint(host: str, port: int, fingerprint: str, timeout: int) -> Tuple[bool, Dict[str, object]]:
    mismatch = False
    detail: Dict[str, object] = {"host": host, "port": port}
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls_sock:
                der = tls_sock.getpeercert(binary_form=True)
                fp = hashlib.sha256(der).hexdigest()
                detail["fingerprint"] = fp
                if fingerprint:
                    mismatch = fp.lower() != fingerprint.lower()
                detail["sni"] = tls_sock.server_hostname
                detail["subject"] = tls_sock.getpeercert().get("subject")
    except Exception as exc:  # pragma: no cover - network dependent
        detail["error"] = str(exc)
        mismatch = True
    return mismatch, detail


def probe_dns_compare(sample_names: List[str], reference: str, timeout: int, max_mismatch: int) -> Tuple[int, Dict[str, object]]:
    mismatches = 0
    detail: Dict[str, object] = {"reference": reference, "results": []}
    has_dig = which("dig") is not None

    for name in sample_names:
        default_ips = set()
        ref_ips = set()
        try:
            info = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
            default_ips = {item[4][0] for item in info if item[4]}
        except Exception as exc:  # pragma: no cover - network dependent
            detail["results"].append({"name": name, "error": str(exc)})
            mismatches += 1
            continue

        if has_dig:
            try:
                cmd = ["dig", f"@{reference}", name, "+short", "+time=" + str(timeout), "+tries=1"]
                out = subprocess.check_output(cmd, timeout=timeout, text=True, stderr=subprocess.DEVNULL)
                ref_ips = {line.strip() for line in out.splitlines() if line.strip() and line[0].isdigit()}
            except subprocess.SubprocessError as exc:
                detail["results"].append({"name": name, "error": str(exc)})
        else:
            # Without dig, re-use default as reference to avoid false positives
            ref_ips = default_ips

        if default_ips != ref_ips:
            mismatches += 1
        detail["results"].append({"name": name, "default": sorted(default_ips), "ref": sorted(ref_ips)})
    detail["mismatches"] = mismatches
    return mismatches, detail


def probe_route(upstream: str) -> Tuple[bool, Dict[str, object]]:
    detail: Dict[str, object] = {"upstream": upstream}
    try:
        out = subprocess.check_output(["ip", "route", "show", "default"], text=True, timeout=2)
    except subprocess.SubprocessError as exc:
        detail["error"] = str(exc)
        return True, detail
    lines = [ln for ln in out.splitlines() if ln.strip()]
    detail["routes"] = lines
    anomaly = True
    for ln in lines:
        if f"dev {upstream}" in ln:
            anomaly = False
    return anomaly, detail


def run_all(cfg: Dict[str, object], upstream: str) -> ProbeOutcome:
    captive_cfg = cfg.get("captive_portal", {}) or {}
    tls_cfg = cfg.get("tls", []) or []
    dns_cfg = cfg.get("dns_compare", {}) or {}

    captive, captive_detail = probe_captive_portal(
        captive_cfg.get("url", "http://connectivitycheck.gstatic.com/generate_204"),
        int(captive_cfg.get("timeout", 4)),
        int(captive_cfg.get("retries", 1)),
    )

    tls_mismatch = False
    tls_details: List[object] = []
    for entry in tls_cfg:
        mismatch, detail = probe_tls_endpoint(
            entry.get("host", "example.com"),
            int(entry.get("port", 443)),
            entry.get("fingerprint_sha256", ""),
            int(entry.get("timeout", 4)),
        )
        tls_mismatch = tls_mismatch or mismatch
        tls_details.append(detail)

    dns_mismatch_count = 0
    dns_detail: Dict[str, object] = {}
    if dns_cfg.get("enabled", False):
        dns_mismatch_count, dns_detail = probe_dns_compare(
            dns_cfg.get("sample_names", ["example.com"]),
            dns_cfg.get("reference_resolver", "9.9.9.9"),
            int(dns_cfg.get("timeout", 3)),
            int(dns_cfg.get("max_mismatch", 2)),
        )

    route_anomaly, route_detail = probe_route(upstream)

    details = {
        "captive": captive_detail,
        "tls": tls_details,
        "dns": dns_detail,
        "route": route_detail,
    }
    return ProbeOutcome(
        captive_portal=captive,
        tls_mismatch=tls_mismatch,
        dns_mismatch=dns_mismatch_count,
        route_anomaly=route_anomaly,
        details=details,
    )
