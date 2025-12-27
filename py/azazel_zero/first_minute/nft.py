from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from .state_machine import Stage


class NftManager:
    def __init__(
        self,
        template_path: Path,
        upstream: str,
        downstream: str,
        mgmt_ip: str,
        mgmt_subnet: str,
        probe_ttl: int = 120,
        dynamic_ttl: int = 300,
    ):
        self.template_path = template_path
        self.upstream = upstream
        self.downstream = downstream
        self.mgmt_ip = mgmt_ip
        self.mgmt_subnet = mgmt_subnet
        self.probe_ttl = probe_ttl
        self.dynamic_ttl = dynamic_ttl

    def _render(self) -> str:
        path = Path(self.template_path)
        if not path.exists():
            repo_fallback = Path(__file__).resolve().parents[3] / "nftables" / "first_minute.nft"
            if repo_fallback.exists():
                path = repo_fallback
        text = path.read_text()
        replacements = {
            "@UPSTREAM@": self.upstream,
            "@DOWNSTREAM@": self.downstream,
            "@MGMT_IP@": self.mgmt_ip,
            "@MGMT_SUBNET@": self.mgmt_subnet,
            "@PROBE_TTL@": f"{self.probe_ttl}s",
            "@DYNAMIC_TTL@": f"{self.dynamic_ttl}s",
        }
        for key, val in replacements.items():
            text = text.replace(key, str(val))
        return text

    def render_preview(self) -> str:
        return self._render()

    def apply_base(self) -> None:
        rendered = self._render()
        subprocess.run(["nft", "-f", "-"], input=rendered, text=True, check=True)

    def set_stage(self, stage: Stage) -> None:
        mark_map = {
            Stage.PROBE: 1,
            Stage.DEGRADED: 2,
            Stage.NORMAL: 3,
            Stage.CONTAIN: 4,
            Stage.DECEPTION: 5,
        }
        mark = mark_map.get(stage, 1)
        subprocess.run(["nft", "flush", "chain", "inet", "azazel_fmc", "stage_switch"], check=False)
        subprocess.run(
            ["nft", "add", "rule", "inet", "azazel_fmc", "stage_switch", "ct", "mark", "set", str(mark)],
            check=True,
        )

    def add_ip(self, ip: str, set_name: str = "allow_dyn_v4", timeout: Optional[int] = None) -> None:
        if ":" in ip:
            return  # ignore IPv6 for this v4 set
        elements = ip
        cmd = ["nft", "add", "element", "inet", "azazel_fmc", set_name, f"{{ {elements} }}"]
        if timeout:
            cmd = [
                "nft",
                "add",
                "element",
                "inet",
                "azazel_fmc",
                set_name,
                f"{{ {elements} timeout {timeout}s }}",
            ]
        subprocess.run(cmd, check=False)

    def clear(self) -> None:
        subprocess.run(["nft", "flush", "table", "inet", "azazel_fmc"], check=False)
        subprocess.run(["nft", "flush", "table", "ip", "nat_azazel_fmc"], check=False)
