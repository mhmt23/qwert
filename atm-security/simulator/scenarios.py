"""Zafiyet senaryoları: race condition ve mutabakat boşluklarını GÜVENLİ üretir.

Her senaryo bir olay listesi (`List[TransactionEvent]`) döndürür. Bu olaylar
detektöre verilir; detektör zafiyeti yakalayabilmeli. Böylece "tespit yeteneğini"
test edebiliriz.

Senaryolar `PLAN.md` içindeki zafiyet kataloğuyla (A1..A5) eşleşir.
"""

from __future__ import annotations

from typing import List

from .events import TransactionEvent, EventType
from .core_banking import CoreBanking
from .xfs_cdm import XfsDevice


def scenario_A1_deposit_cancel_race(account="TR-001", amount=1000.0) -> List[TransactionEvent]:
    """A1 — Para yatırma iptal race condition (SİZİN OLAYINIZ).

    Akış:
      t=0.0  Kullanıcı nakit koyar, CDM sayar -> CASH_ACCEPTED (fiziksel)
      t=0.5  Switch Core'a iletir -> CREDIT (hesap alacaklandı)
      t=0.8  Bir CANCEL sinyali üretilir (timeout/kullanıcı iptali)
      t=1.0  CDM banknotları İADE eder -> CASH_RETURNED (fiziksel)
      ***    İptal Core'a ULAŞMAZ -> CREDIT kalır, nakit de kullanıcıda.

    Sonuç: Hesap +1000€, kullanıcının elinde +1000€ nakit = ÇİFT PARA.
    Detektör bunu 'fiziksel iade var ama ledger reversal yok' olarak yakalamalı.
    """
    core, dev = CoreBanking(), XfsDevice()
    ev = []
    ev.append(dev.cash_accepted("A1", account, amount, t=0.0))
    ev.append(core.credit("A1", account, amount, t=0.5))
    ev.append(dev.cancel("A1", account, amount, t=0.8))
    ev.append(dev.cash_returned("A1", account, amount, t=1.0))
    # DİKKAT: core.reversal(...) çağrılmıyor -> desync. Zafiyet burada.
    return ev


def scenario_A2_withdrawal_reversal_race(account="TR-002", amount=800.0) -> List[TransactionEvent]:
    """A2 — Para çekme reversal race.

    Core borçlandırır, nakit dağıtılır, ama bir reversal ledger'ı geri alır;
    fiziksel dağıtımın karşılığı silinir.
    """
    core, dev = CoreBanking(), XfsDevice(cassette_total=10000.0)
    ev = []
    ev.append(core.debit("A2", account, amount, t=0.0))
    ev.append(dev.cash_dispensed("A2", account, amount, t=0.4))
    ev.append(dev.cancel("A2", account, amount, t=0.6))
    # Reversal ledger'ı geri alır ama nakit fiziksel olarak gitti:
    ev.append(core.reversal("A2", account, amount, t=0.9,
                            original_type=EventType.DEBIT))
    return ev


def scenario_A4_partial_dispense(account="TR-004", requested=500.0, dispensed=300.0):
    """A4 — Kısmi dağıtım uyuşmazlığı: 500€ borçlandırılır, 300€ dağıtılır."""
    core, dev = CoreBanking(), XfsDevice(cassette_total=10000.0)
    ev = []
    ev.append(core.debit("A4", account, requested, t=0.0))
    ev.append(dev.cash_dispensed("A4", account, dispensed, t=0.4))
    return ev


def scenario_clean_deposit(account="TR-OK", amount=1000.0) -> List[TransactionEvent]:
    """Kontrol senaryosu: HER ŞEY DOĞRU. İptal olur, hem fiziksel iade hem
    ledger reversal yapılır. Detektör bunu bulgu ÜRETMEDEN geçmeli
    (yanlış-pozitif kontrolü)."""
    core, dev = CoreBanking(), XfsDevice()
    ev = []
    ev.append(dev.cash_accepted("OK", account, amount, t=0.0))
    ev.append(core.credit("OK", account, amount, t=0.5))
    ev.append(dev.cancel("OK", account, amount, t=0.8))
    ev.append(dev.cash_returned("OK", account, amount, t=1.0))
    # Doğru desen: reversal fiziksel iadeyle eşleşir VE idempotency anahtarı taşır.
    ev.append(core.reversal("OK", account, amount, t=1.2,
                            original_type=EventType.CREDIT, idem="OK-rev-1"))
    return ev


ALL_SCENARIOS = {
    "A1": scenario_A1_deposit_cancel_race,
    "A2": scenario_A2_withdrawal_reversal_race,
    "A4": scenario_A4_partial_dispense,
    "clean": scenario_clean_deposit,
}
