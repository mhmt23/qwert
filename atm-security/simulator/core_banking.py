"""Basitleştirilmiş Çekirdek Bankacılık (Core Banking) ledger'ı.

Gerçek bir çekirdek sistemi değildir; yalnızca mutabakat/race condition
davranışını modellemeye yetecek kadar hesap defteri mantığı içerir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .events import TransactionEvent, Source, EventType


@dataclass
class CoreBanking:
    balances: Dict[str, float] = field(default_factory=dict)
    ledger: List[TransactionEvent] = field(default_factory=list)
    # correlation_id -> uygulanmış idempotency anahtarları (tekrar-güvenliği)
    _applied_keys: set = field(default_factory=set)

    def balance(self, account: str) -> float:
        return self.balances.get(account, 0.0)

    def _emit(self, corr, type_, amount, account, t, idem=None) -> TransactionEvent:
        ev = TransactionEvent(
            correlation_id=corr, source=Source.CORE_BANKING, type=type_,
            amount=amount, account=account, timestamp=t, idempotency_key=idem,
        )
        self.ledger.append(ev)
        return ev

    def credit(self, corr, account, amount, t, idem=None) -> TransactionEvent:
        self.balances[account] = self.balance(account) + amount
        return self._emit(corr, EventType.CREDIT, amount, account, t, idem)

    def debit(self, corr, account, amount, t, idem=None) -> TransactionEvent:
        self.balances[account] = self.balance(account) - amount
        return self._emit(corr, EventType.DEBIT, amount, account, t, idem)

    def reversal(self, corr, account, amount, t, original_type, idem=None):
        """İşlemi geri al. `idempotency_key` verilmişse ve daha önce
        uygulanmışsa tekrar UYGULANMAZ (güvenli davranış). Anahtar yoksa
        körlemesine uygulanır (zafiyetli davranış — A5'i modellemek için)."""
        if idem is not None:
            if idem in self._applied_keys:
                return None  # idempotent: zaten uygulandı, tekrar etme
            self._applied_keys.add(idem)
        # CREDIT'i geri almak = DEBIT; DEBIT'i geri almak = CREDIT
        if original_type == EventType.CREDIT:
            self.balances[account] = self.balance(account) - amount
        else:
            self.balances[account] = self.balance(account) + amount
        return self._emit(corr, EventType.REVERSAL, amount, account, t, idem)
