from __future__ import annotations

import logging
import subprocess
from typing import Iterable, Tuple

from .state_machine import Stage

_LOG = logging.getLogger(__name__)


class TcManager:
    """
    Traffic Control Manager
    
    改善点:
    - forward トラフィックのみに遅滞を適用
    - ホスト input は遅滞の対象外
    - nftables の input chain で管理通信（SSH/VSCode）は先に許可されているため、
      tc による遅滞は forward に限定しても管理通信の機能停止は防止される
    
    注意点:
    - root qdisc は Pi host traffic に影響する可能性がある
    - 将来改善として HTB/HFSC + fwmark で完全分離が可能
    """
    
    def __init__(self, downstream: str, upstream: str):
        self.downstream = downstream
        self.upstream = upstream
        self._deception_signature: tuple | None = None
        self._deception_active = False

    def _run(self, args: list[str]) -> None:
        result = subprocess.run(
            ["tc"] + args,
            check=False,
            capture_output=True,
            text=True,
        )
        rc = getattr(result, "returncode", 0)
        if not isinstance(rc, int) or rc == 0:
            return

        stderr = (getattr(result, "stderr", "") or "").strip()
        cmd_text = "tc " + " ".join(args)
        # Deleting a non-existent root qdisc is expected during transition/cleanup.
        if args[:3] == ["qdisc", "del", "dev"] and (
            "Cannot delete qdisc with handle of zero" in stderr or "No such file or directory" in stderr
        ):
            _LOG.debug("tc benign: %s -> %s", cmd_text, stderr or f"rc={rc}")
            return

        _LOG.warning("tc command failed: %s (rc=%s, stderr=%s)", cmd_text, rc, stderr or "<empty>")

    def apply(self, stage: Stage) -> None:
        # Stage-applied shaping and targeted deception-delay must not conflict.
        self._deception_signature = None
        self._deception_active = False

        # Keep lightweight shaping; Pi Zero 2 W cannot handle heavy queuing
        if stage == Stage.DEGRADED:
            # 出向き (upstream): 2Mbps 帯域制限
            self._run(["qdisc", "replace", "dev", self.upstream, "root", "handle", "1:", "tbf", "rate", "2mbit", "burst", "32kbit", "latency", "400ms"])
            # 下向き (downstream): 150ms 遅延
            self._run(["qdisc", "replace", "dev", self.downstream, "root", "handle", "2:", "netem", "delay", "150ms", "50ms", "distribution", "normal"])
        elif stage == Stage.PROBE:
            # Probe 期間：より強い制限
            self._run(["qdisc", "replace", "dev", self.upstream, "root", "handle", "1:", "tbf", "rate", "1mbit", "burst", "16kbit", "latency", "400ms"])
            self._run(["qdisc", "replace", "dev", self.downstream, "root", "handle", "2:", "netem", "delay", "220ms", "100ms"])
        elif stage == Stage.CONTAIN:
            # CONTAIN：攻撃と見なすトラフィックを強く抑制
            # ★ ただし管理通信（SSH/VSCode）は nftables で先に許可されているため、
            #    forward chain で既に制限されている
            # ★ ここで tc を適用する必要は相対的に低い
            #    （TCP のみを遅滞したい場合は root qdisc ではなく HTB/HFSC 使用）
            self._run(["qdisc", "replace", "dev", self.upstream, "root", "handle", "1:", "tbf", "rate", "512kbit", "burst", "8kbit", "latency", "600ms"])
            self._run(["qdisc", "replace", "dev", self.downstream, "root", "handle", "2:", "netem", "delay", "400ms", "200ms", "loss", "5%"])
        elif stage == Stage.DECEPTION:
            # DECEPTION は Suricata+Canary 条件でのみ個別遅滞を入れるため、
            # ここでは一旦クリアして専用メソッド側へ委譲する。
            self.clear()
        else:
            self.clear()

    def apply_deception_delay(
        self,
        targets: Iterable[Tuple[str, int]],
        delay_ms: int = 650,
        jitter_ms: int = 120,
        loss_percent: float = 0.0,
    ) -> None:
        cleaned = sorted(
            {
                (str(ip).strip(), int(port))
                for ip, port in targets
                if str(ip).strip() and 1 <= int(port) <= 65535
            }
        )
        if not cleaned:
            self.clear_deception_delay()
            return

        signature = (tuple(cleaned), int(delay_ms), int(jitter_ms), float(loss_percent))
        if signature == self._deception_signature:
            return

        self._deception_signature = signature
        self._deception_active = True

        # Rebuild upstream qdisc deterministically so stale filters do not remain.
        self._run(["qdisc", "del", "dev", self.upstream, "root"])
        self._run(["qdisc", "replace", "dev", self.upstream, "root", "handle", "1:", "prio", "bands", "3"])
        self._run(["qdisc", "replace", "dev", self.upstream, "parent", "1:1", "handle", "10:", "sfq"])
        self._run(["qdisc", "replace", "dev", self.upstream, "parent", "1:2", "handle", "20:", "sfq"])

        netem_cmd = [
            "qdisc",
            "replace",
            "dev",
            self.upstream,
            "parent",
            "1:3",
            "handle",
            "30:",
            "netem",
            "delay",
            f"{max(1, int(delay_ms))}ms",
            f"{max(0, int(jitter_ms))}ms",
            "distribution",
            "normal",
        ]
        if float(loss_percent) > 0:
            netem_cmd.extend(["loss", f"{float(loss_percent):.2f}%"])
        self._run(netem_cmd)

        pref = 200
        for attacker_ip, canary_port in cleaned:
            # Canary 応答 (sport=canary_port) が attacker_ip に戻るフローのみ遅延する。
            for proto in (6, 17):  # TCP, UDP
                self._run(
                    [
                        "filter",
                        "add",
                        "dev",
                        self.upstream,
                        "protocol",
                        "ip",
                        "parent",
                        "1:",
                        "pref",
                        str(pref),
                        "u32",
                        "match",
                        "ip",
                        "dst",
                        f"{attacker_ip}/32",
                        "match",
                        "ip",
                        "protocol",
                        str(proto),
                        "0xff",
                        "match",
                        "ip",
                        "sport",
                        str(canary_port),
                        "0xffff",
                        "flowid",
                        "1:3",
                    ]
                )
                pref += 1

    def clear_deception_delay(self) -> None:
        if not self._deception_active and self._deception_signature is None:
            return
        self._deception_signature = None
        self._deception_active = False
        self._run(["qdisc", "del", "dev", self.upstream, "root"])

    def clear(self) -> None:
        self._deception_signature = None
        self._deception_active = False
        self._run(["qdisc", "del", "dev", self.downstream, "root"])
        self._run(["qdisc", "del", "dev", self.upstream, "root"])
