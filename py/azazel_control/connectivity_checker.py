#!/usr/bin/env python3
"""Connectivity and captive-portal detection helpers."""

from __future__ import annotations

import socket
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

DEFAULT_CHECK_URLS = [
    "http://connectivitycheck.gstatic.com/generate_204",
    "http://captive.apple.com/hotspot-detect.html",
    "http://www.msftconnecttest.com/connecttest.txt",
]


def _parse_location(headers: str) -> str:
    for line in headers.splitlines():
        if line.lower().startswith("location:"):
            return line.split(":", 1)[1].strip()
    return ""


def _curl_probe(url: str, iface: Optional[str], timeout_sec: int) -> Dict[str, Any]:
    body_path = ""
    hdr_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="azazel_conn_body_", delete=False, dir="/tmp") as bf:
            body_path = bf.name
        with tempfile.NamedTemporaryFile(prefix="azazel_conn_hdr_", delete=False, dir="/tmp") as hf:
            hdr_path = hf.name

        cmd = [
            "curl",
            "-sS",
            "--max-time",
            str(max(2, timeout_sec)),
            "-o",
            body_path,
            "-D",
            hdr_path,
            "-w",
            "%{http_code} %{url_effective}",
            url,
        ]
        if iface:
            cmd[1:1] = ["--interface", iface]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max(4, timeout_sec + 2))
        out = {
            "url": url,
            "http_code": "000",
            "http_check": "FAIL",
            "location": "",
            "body_len": 0,
            "effective_url": "",
            "curl_error": "",
            "returncode": proc.returncode,
        }

        if proc.returncode != 0:
            if proc.returncode == 28:
                out["curl_error"] = "TIMEOUT"
            elif proc.returncode == 6:
                out["curl_error"] = "DNS_FAIL"
            else:
                out["curl_error"] = f"CURL_ERR_{proc.returncode}"
            return out

        payload = (proc.stdout or "").strip().split(maxsplit=1)
        out["http_code"] = payload[0] if payload else "000"
        out["effective_url"] = payload[1] if len(payload) > 1 else ""
        out["http_check"] = "OK"
        try:
            out["body_len"] = Path(body_path).stat().st_size
        except Exception:
            out["body_len"] = 0
        try:
            headers = Path(hdr_path).read_text(encoding="utf-8", errors="ignore")
            out["location"] = _parse_location(headers)
        except Exception:
            out["location"] = ""
        return out
    except subprocess.TimeoutExpired:
        return {
            "url": url,
            "http_code": "000",
            "http_check": "FAIL",
            "location": "",
            "body_len": 0,
            "effective_url": "",
            "curl_error": "TIMEOUT",
            "returncode": None,
        }
    except Exception as exc:  # pragma: no cover - defensive
        return {
            "url": url,
            "http_code": "000",
            "http_check": "FAIL",
            "location": "",
            "body_len": 0,
            "effective_url": "",
            "curl_error": f"CURL_ERR:{exc}",
            "returncode": None,
        }
    finally:
        for p in (body_path, hdr_path):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass


def _resolve_hosts(urls: Iterable[str]) -> Dict[str, List[str]]:
    resolved: Dict[str, List[str]] = {}
    for url in urls:
        host = urlparse(url).hostname or ""
        if not host or host in resolved:
            continue
        addrs: List[str] = []
        try:
            for item in socket.getaddrinfo(host, None):
                addr = item[4][0]
                if addr not in addrs:
                    addrs.append(addr)
        except Exception:
            addrs = []
        resolved[host] = addrs
    return resolved


def _gateway_ping() -> str:
    try:
        proc = subprocess.run(["ping", "-c", "1", "-W", "2", "8.8.8.8"], capture_output=True, timeout=3)
        return "OK" if proc.returncode == 0 else "FAIL"
    except Exception:
        return "FAIL"


def classify_captive(checks: List[Dict[str, Any]]) -> Dict[str, str]:
    for check in checks:
        code = str(check.get("http_code", "000") or "000")
        if code.startswith("30"):
            portal_url = str(check.get("location") or check.get("effective_url") or check.get("url") or "")
            return {
                "status": "YES",
                "reason": "HTTP_30X",
                "likelihood": "high",
                "portal_url": portal_url,
            }

    for check in checks:
        code = str(check.get("http_code", "000") or "000")
        body_len = int(check.get("body_len", 0) or 0)
        if code == "200" and body_len > 0:
            portal_url = str(check.get("effective_url") or check.get("url") or "")
            return {
                "status": "SUSPECTED",
                "reason": "HTTP_200_BODY",
                "likelihood": "medium",
                "portal_url": portal_url,
            }

    for check in checks:
        if str(check.get("http_code", "000") or "000") == "204":
            return {
                "status": "NO",
                "reason": "HTTP_204",
                "likelihood": "none",
                "portal_url": "",
            }

    first_error = ""
    for check in checks:
        err = str(check.get("curl_error") or "")
        if err:
            first_error = err
            break

    if first_error:
        return {
            "status": "NA",
            "reason": first_error,
            "likelihood": "low",
            "portal_url": "",
        }

    for check in checks:
        code = str(check.get("http_code", "000") or "000")
        if code and code != "000":
            return {
                "status": "NA",
                "reason": f"HTTP_{code}",
                "likelihood": "low",
                "portal_url": "",
            }

    return {
        "status": "NA",
        "reason": "HTTP_000",
        "likelihood": "low",
        "portal_url": "",
    }


def run_connectivity_checks(
    iface: Optional[str],
    urls: Optional[List[str]] = None,
    timeout_sec: int = 4,
) -> Dict[str, Any]:
    check_urls = urls[:] if urls else DEFAULT_CHECK_URLS[:]
    probes = [_curl_probe(url, iface=iface, timeout_sec=timeout_sec) for url in check_urls]
    captive = classify_captive(probes)

    resolved = _resolve_hosts(check_urls)
    all_addrs = [addr for addrs in resolved.values() for addr in addrs]
    dns_suspicious = len(set(all_addrs)) <= 1 and len(resolved) >= 2 and len(all_addrs) >= 2

    dns_resolution = "OK"
    if any(not addrs for addrs in resolved.values()):
        dns_resolution = "FAIL"

    primary = probes[0] if probes else {
        "url": check_urls[0] if check_urls else "",
        "http_code": "000",
        "http_check": "FAIL",
        "location": "",
        "body_len": 0,
        "effective_url": "",
        "curl_error": "",
    }

    internet_ok = captive["status"] == "NO"
    return {
        "gateway_reachable": _gateway_ping(),
        "dns_resolution": dns_resolution,
        "dns_suspicious": dns_suspicious,
        "http_check": primary.get("http_check", "FAIL"),
        "http_code": primary.get("http_code", "000"),
        "location": primary.get("location", ""),
        "body_len": int(primary.get("body_len", 0) or 0),
        "effective_url": primary.get("effective_url", ""),
        "probe_url": primary.get("url", ""),
        "curl_error": primary.get("curl_error", ""),
        "checks": probes,
        "captive_status": captive["status"],
        "captive_reason": captive["reason"],
        "captive_likelihood": captive["likelihood"],
        "captive_portal_url": captive["portal_url"],
        "internet_ok": internet_ok,
    }


__all__ = [
    "DEFAULT_CHECK_URLS",
    "classify_captive",
    "run_connectivity_checks",
]
