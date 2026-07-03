"""Duman testleri: dedektörlerin örnek fixture üzerinde beklenen katmanları
yakaladığını doğrular. `python -m audit.tests.test_scanner` ile çalışır."""

from pathlib import Path

from audit.scanner import Scanner, Severity
from audit.layers import LAYERS

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "VulnerableLendingPool.sol"


def run():
    scanner = Scanner(LAYERS)
    findings = scanner.scan_file(SAMPLE)
    layers_hit = {f.layer for f in findings}

    expected_layers = {
        "1-access", "2-arithmetic", "3-reentrancy",
        "4-oracle", "5-logic",
    }
    missing = expected_layers - layers_hit
    assert not missing, f"Beklenen katmanlar yakalanmadı: {missing}"

    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert crit, "En az bir CRITICAL reentrancy bulgusu bekleniyordu"

    # Bulgular önem sırasına göre sıralı olmalı
    sevs = [int(f.severity) for f in findings]
    assert sevs == sorted(sevs, reverse=True), "Bulgular önem sırasına göre değil"

    print(f"OK — {len(findings)} bulgu, {len(layers_hit)} katman: {sorted(layers_hit)}")


if __name__ == "__main__":
    run()
