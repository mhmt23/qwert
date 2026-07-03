"""Kural tabanlı davranışsal anomali tespiti.

Mutabakat motoru tek bir işlem zincirinin iç tutarlılığına bakar. Buradaki
kurallar ise olay AKIŞI üzerinde desen arar: yetim (orphan) iptaller, imkânsız
hız, tekrar eden idempotency anahtarları vb. `PLAN.md` kataloğunda B ve C sınıfı.
"""

from __future__ import annotations

from typing import Dict, List

from simulator.events import TransactionEvent, EventType
from .reconciliation import Finding, Severity


def detect_orphan_cancels(events: List[TransactionEvent]) -> List[Finding]:
    """C1 — Yetim iptal/reversal: karşılık gelen fiziksel geri hareket olmadan
    üretilen CANCEL veya REVERSAL. İptal edildiği söylenen ama fiziksel ayağı
    olmayan işlemler dolandırıcılık göstergesidir."""
    findings: List[Finding] = []
    chains: Dict[str, List[TransactionEvent]] = {}
    for ev in events:
        chains.setdefault(ev.correlation_id, []).append(ev)

    for corr, evs in chains.items():
        types = {e.type for e in evs}
        has_cancel = EventType.CANCEL in types or EventType.REVERSAL in types
        has_phys_reverse = (
            EventType.CASH_RETURNED in types  # yatırma iadesi
        )
        # İptal var ama ne fiziksel iade ne de nakit dağıtımı yoksa şüpheli
        if has_cancel and not has_phys_reverse and EventType.CASH_DISPENSED not in types:
            findings.append(Finding(
                code="C1", severity=Severity.MEDIUM, correlation_id=corr,
                account=evs[0].account,
                title="Yetim iptal — fiziksel karşılığı yok",
                detail="CANCEL/REVERSAL üretildi fakat karşılık gelen fiziksel "
                       "nakit hareketi bulunamadı.",
                physical_net=0.0, logical_net=0.0,
            ))
    return findings


def detect_velocity_abuse(events: List[TransactionEvent],
                          window: float = 5.0,
                          max_ops: int = 3) -> List[Finding]:
    """Hız istismarı: aynı hesaptan `window` saniye içinde `max_ops`'tan fazla
    işlem. Sizin olayınızdaki gibi 'sınırsız tekrar' desenini yakalar."""
    findings: List[Finding] = []
    by_account: Dict[str, List[TransactionEvent]] = {}
    for ev in events:
        if ev.type in (EventType.CASH_ACCEPTED, EventType.CASH_DISPENSED):
            by_account.setdefault(ev.account, []).append(ev)

    for account, evs in by_account.items():
        evs.sort(key=lambda e: e.timestamp)
        for i in range(len(evs)):
            j = i
            while j < len(evs) and evs[j].timestamp - evs[i].timestamp <= window:
                j += 1
            count = j - i
            if count > max_ops:
                findings.append(Finding(
                    code="B4", severity=Severity.HIGH, correlation_id="-",
                    account=account,
                    title=f"Hız istismarı — {window:.0f}s içinde {count} nakit işlemi",
                    detail="Aynı hesaptan kısa sürede çok sayıda nakit işlemi. "
                           "Otomatik/tekrarlı istismar göstergesi.",
                    physical_net=0.0, logical_net=0.0,
                ))
                break
    return findings


def detect_missing_idempotency(events: List[TransactionEvent]) -> List[Finding]:
    """A5 — Idempotency anahtarı olmayan reversal'lar tekrar uygulanabilir."""
    findings: List[Finding] = []
    for ev in events:
        if ev.type == EventType.REVERSAL and ev.idempotency_key is None:
            findings.append(Finding(
                code="A5", severity=Severity.MEDIUM, correlation_id=ev.correlation_id,
                account=ev.account,
                title="Idempotency anahtarı olmayan REVERSAL",
                detail="Reversal bir idempotency anahtarı taşımıyor; ağ tekrarında "
                       "(replay) iki kez uygulanabilir.",
                physical_net=0.0, logical_net=0.0,
            ))
    return findings


ALL_RULES = [
    detect_orphan_cancels,
    detect_velocity_abuse,
    detect_missing_idempotency,
]


def run_all_rules(events: List[TransactionEvent]) -> List[Finding]:
    out: List[Finding] = []
    for rule in ALL_RULES:
        out.extend(rule(events))
    return out
