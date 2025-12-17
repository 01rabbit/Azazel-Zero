# azazel_zero/app/threat_judge.py
from __future__ import annotations
from typing import Dict, Any, Optional
from azazel_zero.core.mock_llm_core import MockLLMCore
from azazel_zero.sensors.wifi_safety import evaluate_wifi_safety

def judge_zero(prompt: str, iface: str, known_db_path: str, gateway_ip: Optional[str]) -> Dict[str, Any]:
    tags, meta = evaluate_wifi_safety(iface, known_db_path, gateway_ip)
    core = MockLLMCore(profile="zero")
    verdict = core.evaluate(prompt, features={"tags": tags, "service": "wifi"})
    return {
        "risk": verdict.risk,
        "category": verdict.category,
        "reason": verdict.reason,
        "tags": list(verdict.tags),
        "meta": meta,
    }