from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Iterable, Optional

from .nft import NftManager


class DNSObserver(threading.Thread):
    def __init__(
        self,
        log_path: Path,
        nft: NftManager,
        stop_event: threading.Event,
        set_name: str = "allow_dyn_v4",
    ):
        super().__init__(daemon=True)
        self.log_path = log_path
        self.nft = nft
        self.stop_event = stop_event
        self.set_name = set_name
        self.ip_re = re.compile(r"(?<![0-9])((?:\d{1,3}\.){3}\d{1,3})(?![0-9])")

    def _follow(self, fh) -> Iterable[str]:
        fh.seek(0, 2)
        while not self.stop_event.is_set():
            line = fh.readline()
            if not line:
                time.sleep(0.2)
                continue
            yield line

    def run(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_path.touch(exist_ok=True)
        with self.log_path.open("r") as fh:
            for line in self._follow(fh):
                for ip in self.ip_re.findall(line):
                    self.nft.add_ip(ip, set_name=self.set_name)


def seed_probe_ips(nft: NftManager, hosts: Iterable[str]) -> None:
    for ip in hosts:
        if ":" in ip:
            continue  # ignore IPv6
        nft.add_ip(ip, set_name="allow_probe_v4")
