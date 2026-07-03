"""Ortak olay modeli.

Tüm framework'ün merkezinde `TransactionEvent` durur. Simülatör bu olayları
üretir, detektör bunları tüketir. Fiziksel nakit hareketi (`physical=True`) ile
mantıksal ledger hareketini (`physical=False`) ayırt etmek, mutabakat motorunun
temelidir: her fiziksel hareketin tam bir mantıksal karşılığı olmalıdır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import itertools

_seq_counter = itertools.count(1)


class Source(str, Enum):
    XFS_DEVICE = "XFS_DEVICE"      # ATM/CDM cihaz katmanı (nakit sensörleri)
    SWITCH = "SWITCH"              # yönlendirme/host katmanı (ISO 8583)
    CORE_BANKING = "CORE_BANKING"  # hesap defteri / ledger


class EventType(str, Enum):
    # Fiziksel (nakit gerçekten hareket etti)
    CASH_ACCEPTED = "CASH_ACCEPTED"    # banknotlar kasete alındı
    CASH_RETURNED = "CASH_RETURNED"    # banknotlar kullanıcıya iade edildi
    CASH_DISPENSED = "CASH_DISPENSED"  # nakit dağıtıldı
    # Mantıksal (ledger / mesaj)
    CREDIT = "CREDIT"                  # hesap alacaklandırıldı
    DEBIT = "DEBIT"                    # hesap borçlandırıldı
    REVERSAL = "REVERSAL"              # işlem geri alındı (ledger)
    CANCEL = "CANCEL"                  # iptal sinyali (cihaz/switch)
    TIMEOUT = "TIMEOUT"                # host yanıt vermedi


# Hangi olay tipleri fiziksel nakit hareketidir
PHYSICAL_TYPES = {
    EventType.CASH_ACCEPTED,
    EventType.CASH_RETURNED,
    EventType.CASH_DISPENSED,
}


@dataclass
class TransactionEvent:
    correlation_id: str          # aynı işlem zincirini bağlar
    source: Source
    type: EventType
    amount: float
    account: str
    timestamp: float             # simülasyon saati (saniye)
    idempotency_key: Optional[str] = None
    sequence: int = field(default_factory=lambda: next(_seq_counter))

    @property
    def physical(self) -> bool:
        return self.type in PHYSICAL_TYPES

    def __str__(self) -> str:
        tag = "PHYS" if self.physical else "LOG "
        return (
            f"[t={self.timestamp:6.2f}] {self.source.value:<12} "
            f"{self.type.value:<14} {tag} {self.amount:>8.2f}€ "
            f"acct={self.account} corr={self.correlation_id}"
        )
