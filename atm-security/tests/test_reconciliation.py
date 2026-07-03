"""Detektörün zafiyetleri yakaladığını ve temiz senaryoda yanlış-pozitif
üretmediğini doğrulayan testler."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.scenarios import (
    scenario_A1_deposit_cancel_race,
    scenario_A2_withdrawal_reversal_race,
    scenario_A4_partial_dispense,
    scenario_clean_deposit,
)
from detector.reconciliation import ReconciliationEngine, Severity
from detector.rules import run_all_rules


def _findings(events):
    return ReconciliationEngine().analyze(events)


def test_A1_deposit_cancel_race_detected():
    findings = _findings(scenario_A1_deposit_cancel_race())
    assert any(f.code == "A1" for f in findings), "A1 race yakalanmalı"
    a1 = next(f for f in findings if f.code == "A1")
    assert a1.severity == Severity.CRITICAL
    # Fiziksel net 0 (koydu+geri aldı), ledger net +1000 -> fark -1000
    assert abs(a1.physical_net - 0.0) < 0.01
    assert abs(a1.logical_net - 1000.0) < 0.01


def test_A2_withdrawal_reversal_race_detected():
    findings = _findings(scenario_A2_withdrawal_reversal_race())
    assert any(f.code == "A2" for f in findings), "A2 race yakalanmalı"


def test_A4_partial_dispense_detected():
    findings = _findings(scenario_A4_partial_dispense(requested=500, dispensed=300))
    assert any(f.code == "A4" for f in findings), "A4 kısmi dağıtım yakalanmalı"


def test_clean_deposit_no_findings():
    """Temiz senaryoda mutabakat motoru bulgu ÜRETMEMELİ (yanlış-pozitif yok)."""
    findings = _findings(scenario_clean_deposit())
    assert findings == [], f"Temiz senaryoda bulgu olmamalı, bulundu: {findings}"


def test_velocity_rule_flags_repeated_ops():
    """Aynı hesaptan tekrarlı işlemler hız kuralını tetiklemeli."""
    events = []
    for _ in range(5):
        events.extend(scenario_A1_deposit_cancel_race(account="ABUSER", amount=1000))
    findings = run_all_rules(events)
    assert any(f.code == "B4" for f in findings), "Hız istismarı yakalanmalı"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {t.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} test geçti")
    sys.exit(1 if failed else 0)
