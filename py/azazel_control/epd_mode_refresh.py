#!/usr/bin/env python3
"""Render mode-centric EPD state from /run/azazel/epd_state.json."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict

EPD_STATE = Path("/run/azazel/epd_state.json")
EPD_LAST_RENDER = Path("/run/azazel/epd_last_render.json")
SURI_EPD_STATE = Path("/run/azazel/suri_epd_state.json")
RUNTIME_SNAPSHOT_CANDIDATES = (
    Path("/run/azazel-gadget/ui_snapshot.json"),
    Path("/run/azazel-zero/ui_snapshot.json"),
)
MODE_CHOICES = {"portal", "shield", "scapegoat"}
USER_STATES = {"SAFE", "CHECKING", "LIMITED", "CONTAINED", "DECEPTION"}


def _safe_load(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        return {}


def _read_live_ssid(upstream_if: str) -> str:
    iface = str(upstream_if or "").strip()
    candidates = []
    if iface:
        # iwgetid syntax varies by distro; try both forms.
        candidates.append(["iwgetid", iface, "-r"])
        candidates.append(["iwgetid", "-r", iface])
    candidates.append(["iwgetid", "-r"])

    for cmd in candidates:
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False).stdout.strip()
            if out:
                return out
        except Exception:
            continue
    return "No SSID"


def _first_snapshot() -> Dict[str, Any]:
    for path in RUNTIME_SNAPSHOT_CANDIDATES:
        data = _safe_load(path)
        if isinstance(data, dict) and data:
            return data
    return {}


def _clean_mode_label(value: object, default: str = "SHIELD") -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        raw = default
    return raw[:12]


def _normal_render_spec(
    payload: Dict[str, Any],
    mode_label: str,
    risk_status: str,
    snapshot: Dict[str, Any],
    suspicion: int = 0,
) -> Dict[str, Any]:
    conn = snapshot.get("connection")
    wifi_state = ""
    if isinstance(conn, dict):
        wifi_state = str(conn.get("wifi_state", "")).strip().upper()

    raw_ssid = str(snapshot.get("ssid", "")).strip()
    ssid = raw_ssid if raw_ssid and raw_ssid != "-" else ""
    if not ssid:
        ssid = str(payload.get("ssid", "")).strip()
    if not ssid:
        ssid = _read_live_ssid(str(payload.get("upstream_if", "")).strip())

    signal: int | None = None
    if wifi_state == "CONNECTED":
        try:
            signal = int(float(str(snapshot.get("signal_dbm", "")).strip()))
        except Exception:
            signal = None

    return {
        "state": "normal",
        "mode_label": _clean_mode_label(mode_label),
        "ssid": ssid,
        "risk_status": str(risk_status or "UNKNOWN").strip().upper(),
        "suspicion": int(suspicion),
        "signal": signal,
    }


def _user_state_from_stage_name(stage_name: object) -> str:
    name = str(stage_name or "").strip().upper()
    mapping = {
        "INIT": "CHECKING",
        "PROBE": "CHECKING",
        "NORMAL": "SAFE",
        "DEGRADED": "LIMITED",
        "CONTAIN": "CONTAINED",
        "DECEPTION": "DECEPTION",
    }
    return mapping.get(name, "")


def _normalized_user_state(data: Dict[str, Any]) -> str:
    user_state = str(data.get("user_state", "")).strip().upper()
    if user_state in USER_STATES:
        return user_state
    internal = data.get("internal")
    if isinstance(internal, dict):
        from_stage = _user_state_from_stage_name(internal.get("state_name", ""))
        if from_stage:
            return from_stage
    return ""


def _coerce_suspicion(data: Dict[str, Any]) -> int:
    internal = data.get("internal")
    if not isinstance(internal, dict):
        return 0
    try:
        return int(float(str(internal.get("suspicion", 0)).strip()))
    except Exception:
        return 0


def _risk_from_connection(data: Dict[str, Any]) -> str:
    conn = data.get("connection")
    if not isinstance(conn, dict):
        return "UNKNOWN"
    internet = str(conn.get("internet_check", "")).strip().upper()
    if internet == "OK":
        return "SAFE"
    if internet == "FAIL":
        return "LIMITED"
    if internet in ("N/A", "UNKNOWN"):
        return "CHECKING"
    return "UNKNOWN"


def _risk_and_suspicion_from_snapshot(data: Dict[str, Any], use_user_state: bool = True) -> tuple[str, int]:
    suspicion = _coerce_suspicion(data)
    if use_user_state:
        user_state = _normalized_user_state(data)
        if user_state:
            return user_state, suspicion
    return _risk_from_connection(data), suspicion


def _alerts_in_scapegoat_enabled() -> bool:
    return os.environ.get("AZAZEL_EPD_ALERTS_IN_SCAPEGOAT", "0").strip().lower() in ("1", "true", "yes", "on")


def _alerts_allowed(mode: str) -> bool:
    return str(mode).strip().lower() != "scapegoat" or _alerts_in_scapegoat_enabled()


def _safe_msg(value: object, default: str) -> str:
    raw = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    if not raw:
        raw = default
    return raw[:20]


def _active_suri_alert(now: float) -> Dict[str, Any]:
    data = _safe_load(SURI_EPD_STATE)
    if not isinstance(data, dict) or not data:
        return {}
    state = str(data.get("state", "")).strip().lower()
    if state not in {"warning", "danger"}:
        return {}
    msg = _safe_msg(data.get("msg", ""), "ATTACK DETECTED")

    try:
        expires_at = int(float(str(data.get("expires_at", "")).strip()))
    except Exception:
        expires_at = 0
    if expires_at > 0 and now > float(expires_at):
        return {}

    try:
        ts = int(float(str(data.get("ts", "0")).strip()))
    except Exception:
        ts = 0
    if expires_at <= 0:
        ttl_default = int(float(os.environ.get("AZAZEL_SURI_ALERT_TTL_SEC", "120")))
        ttl = max(1, ttl_default)
        if ts > 0 and (now - float(ts)) > ttl:
            return {}
    return {"state": state, "msg": msg}


def _mode_label_from_payload(mode: str, payload: Dict[str, Any]) -> str:
    if mode in MODE_CHOICES:
        return mode
    fallback = str(payload.get("target_mode", "")).strip().lower()
    if fallback in MODE_CHOICES:
        return fallback
    return "shield"


def _limited_msg_from_snapshot(snapshot: Dict[str, Any]) -> str:
    recommendation = str(snapshot.get("recommendation", "")).strip()
    if recommendation:
        return _safe_msg(recommendation, "LIMITED")
    reasons = snapshot.get("reasons")
    if isinstance(reasons, list) and reasons:
        return _safe_msg(reasons[0], "LIMITED")
    return "LIMITED"


def _desired_render_spec(payload: Dict[str, Any]) -> Dict[str, Any]:
    mode = str(payload.get("mode", "")).strip().lower()
    snapshot = _first_snapshot()
    mode_label = _mode_label_from_payload(mode, payload)

    # Keep base screen during mode switch (no WARNING banner).
    if mode == "switching":
        target = str(payload.get("target_mode", "shield")).strip().lower()
        if target not in ("portal", "shield", "scapegoat"):
            target = "shield"
        return _normal_render_spec(payload, target, "CHECKING", snapshot, 0)

    if mode == "failed":
        return {"state": "danger", "msg": "MODE FAIL"}

    if mode_label in MODE_CHOICES:
        alerts_allowed = _alerts_allowed(mode_label)
        suri_alert = _active_suri_alert(time.time())
        if suri_alert and alerts_allowed:
            return suri_alert

        user_state = _normalized_user_state(snapshot)
        risk, suspicion = _risk_and_suspicion_from_snapshot(snapshot, use_user_state=alerts_allowed)
        attack = snapshot.get("attack")
        if not isinstance(attack, dict):
            attack = {}

        if alerts_allowed:
            if user_state == "CONTAINED":
                return {"state": "danger", "msg": "ATTACK DETECTED"}
            if user_state == "DECEPTION":
                delay_active = bool(attack.get("canary_delay_active", False))
                return {"state": "danger", "msg": "DELAY ACTIVE" if delay_active else "DECEPTION MODE"}
            if user_state == "LIMITED":
                return {"state": "warning", "msg": _limited_msg_from_snapshot(snapshot)}

        if risk == "UNKNOWN":
            net = str(payload.get("internet", "unknown")).strip().upper()
            if net == "OK":
                risk = "SAFE"
            elif net == "FAIL":
                risk = "LIMITED"
            else:
                risk = "CHECKING"
        return _normal_render_spec(payload, mode_label, risk, snapshot, suspicion)

    return {"state": "warning", "msg": "MODE N/A"}


def _same_render(desired: Dict[str, Any], last_payload: Dict[str, Any]) -> bool:
    last_render = {}
    if isinstance(last_payload, dict):
        if isinstance(last_payload.get("render"), dict):
            last_render = last_payload.get("render") or {}
        elif isinstance(last_payload, dict):
            # backward compatibility: raw render dict
            last_render = last_payload

    return _visual_fingerprint(desired) == _visual_fingerprint(last_render)


def _to_int_or_none(value: Any) -> int | None:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return None


def _signal_bucket(signal_value: Any) -> str:
    # Keep in sync with py/azazel_epd.py:render_normal icon thresholds.
    signal_dbm = _to_int_or_none(signal_value)
    if signal_dbm is None:
        return "none"
    if signal_dbm >= -60:
        return "strong"
    if signal_dbm >= -70:
        return "medium"
    return "weak"


def _visual_fingerprint(render: Dict[str, Any]) -> Dict[str, Any]:
    """Return a fingerprint that matches what the panel actually displays."""
    state = str(render.get("state", "")).strip().lower()
    if state == "normal":
        return {
            "state": "normal",
            "mode_label": str(render.get("mode_label", "")).strip().upper(),
            "ssid": str(render.get("ssid", "")).strip(),
            "risk_status": str(render.get("risk_status", "")).strip().upper(),
            "suspicion": int(_to_int_or_none(render.get("suspicion")) or 0),
            "signal_bucket": _signal_bucket(render.get("signal")),
        }
    return {
        "state": state,
        "msg": str(render.get("msg", "")).strip(),
    }


def main() -> int:
    payload = _safe_load(EPD_STATE)
    if not payload:
        return 0

    root = Path(os.environ.get("AZAZEL_ROOT", str(Path(__file__).resolve().parents[2])))
    epd_script = root / "py" / "azazel_epd.py"
    if not epd_script.exists():
        return 0

    desired = _desired_render_spec(payload)
    last = _safe_load(EPD_LAST_RENDER)
    if _same_render(desired, last):
        return 0

    cmd = [sys.executable, str(epd_script), "--state", desired.get("state", "warning")]
    if desired.get("state") == "normal":
        cmd.extend(
            [
                "--ssid", str(desired.get("ssid", "")),
                "--mode-label", str(desired.get("mode_label", "SHIELD")),
                "--risk-status", str(desired.get("risk_status", "UNKNOWN")),
                "--suspicion", str(desired.get("suspicion", 0)),
            ]
        )
        if desired.get("signal") is not None:
            cmd.extend(["--signal", str(desired.get("signal"))])
    else:
        cmd.extend(["--msg", str(desired.get("msg", "MODE"))])

    try:
        subprocess.run(cmd, timeout=45, check=False)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
