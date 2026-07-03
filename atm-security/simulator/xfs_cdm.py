"""XFS/CDM cihaz katmanı simülasyonu.

CEN/XFS, ATM donanımını (nakit kabul/dağıtım birimi = CDM/CIM) yöneten
standart arayüzdür. Burada yalnızca mutabakat açısından önemli olan fiziksel
olayları (nakit kabul edildi / iade edildi / dağıtıldı) ve iptal sinyallerini
modelliyoruz. Amaç: fiziksel nakit hareketini mantıksal ledger'dan bağımsız
üretebilmek — çünkü zafiyetin özü ikisinin ayrışmasıdır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .events import TransactionEvent, Source, EventType


@dataclass
class XfsDevice:
    """Tek bir ATM/CDM cihazı. Ürettiği her olay fiziksel gerçeği yansıtır."""
    device_id: str = "ATM-001"
    cassette_total: float = 0.0        # kasetteki nakit (dağıtım için)
    accepted_total: float = 0.0        # kabul edilip kasete alınan nakit
    events: List[TransactionEvent] = field(default_factory=list)

    def _emit(self, corr, type_, amount, account, t) -> TransactionEvent:
        ev = TransactionEvent(
            correlation_id=corr, source=Source.XFS_DEVICE, type=type_,
            amount=amount, account=account, timestamp=t,
        )
        self.events.append(ev)
        return ev

    def cash_accepted(self, corr, account, amount, t):
        """Banknotlar sayıldı ve kasete alındı (geri dönüşü olmayan an)."""
        self.accepted_total += amount
        return self._emit(corr, EventType.CASH_ACCEPTED, amount, account, t)

    def cash_returned(self, corr, account, amount, t):
        """Banknotlar kullanıcıya iade edildi. Eğer daha önce kasete
        alınmışsa (accepted), bu fiziksel bir geri çıkıştır."""
        self.accepted_total -= amount
        return self._emit(corr, EventType.CASH_RETURNED, amount, account, t)

    def cash_dispensed(self, corr, account, amount, t):
        """Nakit fiziksel olarak dağıtıldı (kullanıcı aldı)."""
        self.cassette_total -= amount
        return self._emit(corr, EventType.CASH_DISPENSED, amount, account, t)

    def cancel(self, corr, account, amount, t):
        """Cihaz/XFS katmanında iptal sinyali üretildi (mantıksal)."""
        return self._emit(corr, EventType.CANCEL, amount, account, t)
