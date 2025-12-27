from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple


class Stage(str, Enum):
    INIT = "INIT"
    PROBE = "PROBE"
    DEGRADED = "DEGRADED"
    NORMAL = "NORMAL"
    CONTAIN = "CONTAIN"
    DECEPTION = "DECEPTION"


@dataclass
class StageContext:
    state: Stage = Stage.INIT
    suspicion: float = 0.0
    last_transition: float = field(default_factory=time.time)
    last_link_bssid: str = ""
    probe_started: float = field(default_factory=time.time)
    stable_since: float = field(default_factory=time.time)
    last_reason: str = "init"


class FirstMinuteStateMachine:
    def __init__(self, cfg: Dict[str, float]):
        self.ctx = StageContext()
        self.cfg = cfg

    def reset_for_new_link(self, bssid: str) -> None:
        self.ctx.state = Stage.PROBE
        self.ctx.suspicion = 0.0
        self.ctx.last_transition = time.time()
        self.ctx.probe_started = time.time()
        self.ctx.stable_since = time.time()
        self.ctx.last_link_bssid = bssid
        self.ctx.last_reason = "new_link"

    def force_state(self, stage: Stage, reason: str = "manual") -> Stage:
        self.ctx.state = stage
        self.ctx.last_transition = time.time()
        self.ctx.last_reason = reason
        self.ctx.stable_since = time.time()
        return stage

    def _decay(self, now: float) -> None:
        dt = now - self.ctx.last_transition
        decay = self.cfg.get("decay_per_sec", 2)
        self.ctx.suspicion = max(0.0, self.ctx.suspicion - decay * dt)
        self.ctx.last_transition = now

    def _apply_signals(self, signals: Dict[str, float | int | bool], reasons: List[str]) -> None:
        add = 0.0
        if signals.get("probe_fail"):
            add += 15 * float(signals.get("probe_fail_count", 1))
            reasons.append("probe_fail")
        if signals.get("dns_mismatch"):
            add += 10 * float(signals.get("dns_mismatch", 1))
            reasons.append("dns_mismatch")
        if signals.get("cert_mismatch"):
            add += 25
            reasons.append("cert_mismatch")
        if signals.get("wifi_tags"):
            add += 20
            reasons.append("wifi_tags")
        if signals.get("route_anomaly"):
            add += 10
            reasons.append("route_anomaly")
        if signals.get("suricata_alert"):
            add += 15
            reasons.append("suricata_alert")
        self.ctx.suspicion = min(100.0, self.ctx.suspicion + add)

    def step(self, signals: Dict[str, float | int | bool]) -> Tuple[Stage, Dict[str, float | str]]:
        now = time.time()
        reasons: List[str] = []
        # Passive decay
        self._decay(now)
        self._apply_signals(signals, reasons)

        elapsed_probe = now - self.ctx.probe_started
        state = self.ctx.state
        changed = False

        if not signals.get("link_up") and state != Stage.INIT:
            self.ctx.state = Stage.INIT
            self.ctx.suspicion = 0.0
            self.ctx.last_reason = "link_down"
            self.ctx.last_transition = now
            return self.ctx.state, {"state": self.ctx.state.value, "suspicion": 0.0, "reason": "link_down"}

        degrade_threshold = self.cfg.get("degrade_threshold", 30)
        normal_threshold = self.cfg.get("normal_threshold", 8)
        contain_threshold = self.cfg.get("contain_threshold", 65)
        stable_normal_sec = self.cfg.get("stable_normal_sec", 20)
        stable_probe_sec = self.cfg.get("stable_probe_sec", 10)
        probe_window = self.cfg.get("probe_window_sec", 20)

        if state == Stage.INIT and signals.get("link_up"):
            self.reset_for_new_link(signals.get("bssid", ""))
            state = self.ctx.state
            changed = True
        elif state == Stage.PROBE:
            if self.ctx.suspicion >= contain_threshold:
                state = Stage.CONTAIN
                changed = True
                self.ctx.last_reason = "probe->contain"
            elif (self.ctx.suspicion >= degrade_threshold) and (elapsed_probe >= stable_probe_sec):
                state = Stage.DEGRADED
                changed = True
                self.ctx.last_reason = "probe->degraded"
                self.ctx.stable_since = now
            elif (elapsed_probe >= probe_window) and (self.ctx.suspicion <= normal_threshold):
                state = Stage.NORMAL
                changed = True
                self.ctx.last_reason = "probe->normal"
                self.ctx.stable_since = now
        elif state == Stage.DEGRADED:
            if self.ctx.suspicion >= contain_threshold:
                state = Stage.CONTAIN
                changed = True
                self.ctx.last_reason = "degraded->contain"
            elif self.ctx.suspicion <= normal_threshold:
                if now - self.ctx.stable_since >= stable_normal_sec:
                    state = Stage.NORMAL
                    changed = True
                    self.ctx.last_reason = "degraded->normal"
            else:
                self.ctx.stable_since = now
        elif state == Stage.NORMAL:
            if self.ctx.suspicion >= contain_threshold:
                state = Stage.CONTAIN
                changed = True
                self.ctx.last_reason = "normal->contain"
            elif self.ctx.suspicion >= degrade_threshold:
                state = Stage.DEGRADED
                changed = True
                self.ctx.last_reason = "normal->degraded"
                self.ctx.stable_since = now
        elif state == Stage.CONTAIN and signals.get("allow_recover"):
            if self.ctx.suspicion <= degrade_threshold:
                state = Stage.DEGRADED
                changed = True
                self.ctx.last_reason = "contain->degraded"

        if changed:
            self.ctx.state = state
            self.ctx.last_transition = now
        summary = {
            "state": self.ctx.state.value,
            "suspicion": round(self.ctx.suspicion, 2),
            "reason": self.ctx.last_reason if reasons == [] else ",".join(reasons),
        }
        return self.ctx.state, summary
