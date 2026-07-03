"""Mutabakat (reconciliation) motoru — framework'ün kalbi.

Temel değişmez (invariant):
  Her işlem zinciri (correlation_id) için, cihazın FİZİKSEL net nakit hareketi
  ile Core Banking'in MANTIKSAL net ledger hareketi TUTARLI olmalıdır.

  - Para yatırma: kalıcı olarak kabul edilen nakit (accepted - returned) kadar
    hesap net alacaklanmalı (credit - reversal).
  - Para çekme: dağıtılan nakit kadar hesap net borçlanmalı.

Bu tutmazsa 'desync' vardır — tam da sizin tespit ettiğiniz zafiyet sınıfı.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List

from simulator.events import TransactionEvent, EventType, Source


class Severity(str, Enum):
    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class Finding:
    code: str                 # zafiyet katalog kodu (A1, B1, ...)
    severity: Severity
    correlation_id: str
    account: str
    title: str
    detail: str
    physical_net: float
    logical_net: float

    def __str__(self) -> str:
        return (
            f"[{self.severity.value}] {self.code} corr={self.correlation_id} "
            f"acct={self.account}: {self.title}\n"
            f"    fiziksel_net={self.physical_net:+.2f}€  "
            f"ledger_net={self.logical_net:+.2f}€  fark="
            f"{self.physical_net - self.logical_net:+.2f}€\n"
            f"    {self.detail}"
        )


# Fiziksel nakit hareketinin işareti (hesap sahibi açısından):
#   CASH_ACCEPTED  -> banka nakdi aldı  -> hesap +olmalı  (+amount)
#   CASH_RETURNED  -> nakit geri verildi -> yatırma iptali (-amount)
#   CASH_DISPENSED -> kullanıcı nakit aldı -> hesap -olmalı (-amount)
_PHYSICAL_SIGN = {
    EventType.CASH_ACCEPTED: +1.0,
    EventType.CASH_RETURNED: -1.0,
    EventType.CASH_DISPENSED: -1.0,
}

# Mantıksal ledger hareketinin işareti:
_LOGICAL_SIGN = {
    EventType.CREDIT: +1.0,
    EventType.DEBIT: -1.0,
}


class ReconciliationEngine:
    """Olayları correlation_id'ye göre gruplayıp net akışları karşılaştırır."""

    def __init__(self, tolerance: float = 0.01):
        self.tolerance = tolerance

    def analyze(self, events: List[TransactionEvent]) -> List[Finding]:
        chains: Dict[str, List[TransactionEvent]] = {}
        for ev in events:
            chains.setdefault(ev.correlation_id, []).append(ev)

        findings: List[Finding] = []
        for corr, evs in chains.items():
            findings.extend(self._analyze_chain(corr, evs))
        return findings

    def _analyze_chain(self, corr, evs) -> List[Finding]:
        account = evs[0].account
        physical_net = 0.0
        logical_net = 0.0
        has_cancel = False
        has_reversal = False

        for ev in evs:
            if ev.type in _PHYSICAL_SIGN:
                physical_net += _PHYSICAL_SIGN[ev.type] * ev.amount
            elif ev.type in _LOGICAL_SIGN:
                logical_net += _LOGICAL_SIGN[ev.type] * ev.amount
            elif ev.type == EventType.REVERSAL:
                has_reversal = True
                # Reversal, önceki net ledger hareketini geri alır. Zincirdeki
                # ilk mantıksal hareketin tersi işaretiyle uygulanır.
                logical_net += self._reversal_sign(evs) * ev.amount
            elif ev.type == EventType.CANCEL:
                has_cancel = True

        diff = physical_net - logical_net
        if abs(diff) <= self.tolerance:
            return []  # mutabık — sorun yok

        # Desync var. Sınıflandır.
        return [self._classify(corr, account, evs, physical_net,
                               logical_net, diff, has_cancel, has_reversal)]

    @staticmethod
    def _reversal_sign(evs) -> float:
        for ev in evs:
            if ev.type == EventType.CREDIT:
                return -1.0  # credit'i geri al -> negatif
            if ev.type == EventType.DEBIT:
                return +1.0  # debit'i geri al -> pozitif
        return 0.0

    def _classify(self, corr, account, evs, phys, log, diff,
                  has_cancel, has_reversal) -> Finding:
        types = {ev.type for ev in evs}

        # A1: fiziksel iade var, ama ledger reversal yok -> çift alacak
        if EventType.CASH_RETURNED in types and not has_reversal:
            return Finding(
                code="A1", severity=Severity.CRITICAL, correlation_id=corr,
                account=account,
                title="Para yatırma iptal race — ledger reversal EKSİK",
                detail=("Nakit fiziksel olarak iade edildi (CASH_RETURNED) fakat "
                        "Core Banking'de karşılık gelen REVERSAL yok. Hesap "
                        "alacaklandırılmış durumda kaldı: kullanıcı hem nakdi "
                        "aldı hem hesabı alacaklı. XFS iptal sinyali Core'a "
                        "asenkron ulaşmadı."),
                physical_net=phys, logical_net=log,
            )

        # A2: fiziksel dağıtım var ama ledger reversal ile debit geri alındı
        if EventType.CASH_DISPENSED in types and has_reversal:
            return Finding(
                code="A2", severity=Severity.CRITICAL, correlation_id=corr,
                account=account,
                title="Para çekme reversal race — nakit dağıtıldı ama borç geri alındı",
                detail=("Nakit fiziksel olarak dağıtıldı (CASH_DISPENSED) fakat "
                        "ledger'da REVERSAL ile borç geri alındı. Kullanıcı "
                        "parayı aldı, hesap borçlanmadı."),
                physical_net=phys, logical_net=log,
            )

        # A4: kısmi dağıtım — dağıtılan nakit borçlanandan az/çok
        if EventType.CASH_DISPENSED in types and EventType.DEBIT in types:
            return Finding(
                code="A4", severity=Severity.HIGH, correlation_id=corr,
                account=account,
                title="Kısmi dağıtım uyuşmazlığı",
                detail=("Dağıtılan nakit ile borçlandırılan tutar eşleşmiyor "
                        "(partial dispense). Fark hesap sahibi lehine/aleyhine."),
                physical_net=phys, logical_net=log,
            )

        # Genel B1: sınıflandırılamayan mutabakatsızlık
        return Finding(
            code="B1", severity=Severity.HIGH, correlation_id=corr,
            account=account,
            title="Mutabakat boşluğu — fiziksel ve mantıksal akış uyuşmuyor",
            detail=("Cihaz fiziksel net hareketi ile ledger net hareketi arasında "
                    "fark var. İncelenmeli."),
            physical_net=phys, logical_net=log,
        )
