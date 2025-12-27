from __future__ import annotations

import subprocess

from .state_machine import Stage


class TcManager:
    def __init__(self, downstream: str, upstream: str):
        self.downstream = downstream
        self.upstream = upstream

    def _run(self, args: list[str]) -> None:
        subprocess.run(["tc"] + args, check=False)

    def apply(self, stage: Stage) -> None:
        # Keep lightweight shaping; Pi Zero 2 W cannot handle heavy queuing
        if stage == Stage.DEGRADED:
            self._run(["qdisc", "replace", "dev", self.downstream, "root", "handle", "1:", "netem", "delay", "150ms", "50ms", "distribution", "normal"])
            self._run(["qdisc", "replace", "dev", self.upstream, "root", "handle", "2:", "tbf", "rate", "2mbit", "burst", "32kbit", "latency", "400ms"])
        elif stage == Stage.PROBE:
            self._run(["qdisc", "replace", "dev", self.downstream, "root", "handle", "1:", "netem", "delay", "220ms", "100ms"])
            self._run(["qdisc", "replace", "dev", self.upstream, "root", "handle", "2:", "tbf", "rate", "1mbit", "burst", "16kbit", "latency", "400ms"])
        elif stage == Stage.CONTAIN:
            self._run(["qdisc", "replace", "dev", self.downstream, "root", "handle", "1:", "netem", "delay", "400ms", "200ms", "loss", "5%"])
            self._run(["qdisc", "replace", "dev", self.upstream, "root", "handle", "2:", "tbf", "rate", "512kbit", "burst", "8kbit", "latency", "600ms"])
        else:
            self.clear()

    def clear(self) -> None:
        self._run(["qdisc", "del", "dev", self.downstream, "root"])
        self._run(["qdisc", "del", "dev", self.upstream, "root"])
