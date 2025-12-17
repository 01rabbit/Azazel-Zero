# azazel_zero/core/mock_llm_core.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import hashlib, json

def _stable_int(key: str) -> int:
    h = hashlib.blake2s(key.encode("utf-8", errors="ignore"), digest_size=8).digest()
    return int.from_bytes(h, "big", signed=False)

def _stable_choice(items: List[str], key: str) -> str:
    return items[_stable_int(key) % len(items)] if items else ""

@dataclass(slots=True)
class ThreatVerdict:
    risk: int
    category: str
    reason: str
    tags: Tuple[str, ...] = field(default_factory=tuple)

class MockLLMCore:
    __slots__ = ("profile", "history_size", "history", "_tpl")

    def __init__(self, profile: str = "zero", history_size: int = 10) -> None:
        self.profile = profile if profile in ("zero", "pi") else "zero"
        self.history_size = max(0, int(history_size))
        self.history: List[Dict[str, Any]] = []
        self._tpl = {
            "bruteforce": ["認証攻撃パターン。自動化された突破試行の可能性。", "辞書/総当たりの兆候。認証保護が必要。"],
            "scan": ["偵察活動（スキャン）の兆候。攻撃準備段階の可能性。", "ポート/サービス列挙の兆候。監視強化が必要。"],
            "exploit": ["エクスプロイト兆候。即時対応が必要な深刻度。", "脆弱性悪用の可能性。封じ込めを推奨。"],
            "malware": ["C2/マルウェア兆候。感染の可能性。", "悪性ペイロードの可能性。隔離が必要。"],
            "sqli": ["SQLi兆候。DB不正操作リスク。", "Webアプリ脆弱性悪用の可能性。"],
            "dos": ["DoS兆候。可用性低下リスク。", "大量トラフィック/過負荷の疑い。"],
            "unknown": ["不審兆候。追加ログと相関して判断。", "継続監視が推奨される事象。"],
            "safety": ["接続環境に危険兆候。通信路の安全性が低下。", "MITM等を否定できない。重要操作を控えるべき。"],
        }

    def evaluate(self, prompt: str, features: Optional[Dict[str, Any]] = None) -> ThreatVerdict:
        f = features or {}
        tags = tuple(t for t in (f.get("tags") or []) if isinstance(t, str) and t)

        text = (prompt or "").lower()
        sig = str(f.get("signature") or f.get("alert_signature") or "").lower()
        classtype = str(f.get("classtype") or f.get("classification") or "").lower()
        blob = " ".join(x for x in (text, sig, classtype) if x)

        if ("sql" in blob) or ("injection" in blob) or ("union select" in blob) or ("drop table" in blob):
            cat = "sqli"
        elif ("malware" in blob) or ("trojan" in blob) or ("backdoor" in blob) or ("c2" in blob) or ("beacon" in blob):
            cat = "malware"
        elif ("exploit" in blob) or ("shellcode" in blob) or ("overflow" in blob) or ("vulnerability" in blob) or ("cve-" in blob):
            cat = "exploit"
        elif ("ddos" in blob) or ("dos" in blob) or ("syn flood" in blob) or ("flood" in blob):
            cat = "dos"
        elif ("nmap" in blob) or ("port scan" in blob) or ("scan" in blob) or ("recon" in blob):
            cat = "scan"
        elif ("brute" in blob) or ("login" in blob) or ("password" in blob) or ("auth" in blob):
            cat = "bruteforce"
        else:
            cat = "unknown"

        base = {"sqli":4,"malware":5,"exploit":4,"dos":3,"scan":2,"bruteforce":3,"unknown":2}[cat]

        safety_bump = 0
        for t in tags:
            tl = t.lower()
            if tl in ("evil_ap","mitm","dns_spoof","tls_downgrade","captive_portal","phish"):
                safety_bump = max(safety_bump, 2)
            elif tl in ("suspicious_ap","arp_spoof","dhcp_spoof","sslstrip"):
                safety_bump = max(safety_bump, 1)

        if self.profile == "zero":
            risk = base + safety_bump
            if safety_bump >= 2:
                risk = max(risk, 4)
        else:
            risk = base + (1 if safety_bump >= 2 else 0)

        jitter = _stable_int((prompt or "") + "|" + cat + "|" + ",".join(tags)) % 2
        risk = max(1, min(5, risk + jitter))

        if tags and cat == "unknown":
            reason = _stable_choice(self._tpl["safety"], (prompt or "") + "|safety|" + ",".join(tags))
        else:
            reason = _stable_choice(self._tpl.get(cat, self._tpl["unknown"]), (prompt or "") + "|" + cat)

        if self.profile == "zero" and any(t.lower() in ("evil_ap","mitm","dns_spoof","tls_downgrade") for t in tags):
            reason += " 重要操作を停止し、回線の切替または切断が望ましい。"

        return ThreatVerdict(risk=int(risk), category=cat, reason=reason, tags=tags)

    def generate_response(self, prompt: str, features: Optional[Dict[str, Any]] = None) -> str:
        v = self.evaluate(prompt, features)
        out = {"risk": v.risk, "reason": v.reason, "category": v.category}
        self._push(prompt, out)
        return json.dumps(out, ensure_ascii=False)

    def _push(self, prompt: str, out: Dict[str, Any]) -> None:
        if self.history_size <= 0:
            return
        item = {"prompt": prompt[:200], "response": out}
        self.history.append(item)
        if len(self.history) > self.history_size:
            del self.history[:len(self.history)-self.history_size]