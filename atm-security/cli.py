#!/usr/bin/env python3
"""ATM-Guard komut satırı arayüzü.

Kullanım:
    python cli.py demo            # A1 (sizin olayınız) üret + tespit et
    python cli.py run A1          # tek senaryo koş
    python cli.py run all         # tüm senaryoları koş
    python cli.py list            # senaryoları listele
"""

from __future__ import annotations

import argparse
import sys

from simulator.scenarios import ALL_SCENARIOS
from detector.reconciliation import ReconciliationEngine
from detector.rules import run_all_rules


def _analyze(events):
    engine = ReconciliationEngine()
    findings = engine.analyze(events) + run_all_rules(events)
    return findings


def _print_events(events):
    print("── Olay akışı " + "─" * 50)
    for ev in sorted(events, key=lambda e: e.timestamp):
        print("  " + str(ev))


def _print_findings(findings):
    print("── Tespitler " + "─" * 51)
    if not findings:
        print("  ✅ Bulgu yok — mutabakat sağlandı.")
        return
    for f in findings:
        print("  " + str(f).replace("\n", "\n  "))
        print()


def cmd_demo(_args):
    print("\n### DEMO — A1: Para yatırma iptal race (Kosova tipi olay)\n")
    events = ALL_SCENARIOS["A1"]()
    _print_events(events)
    print()
    _print_findings(_analyze(events))


def cmd_run(args):
    key = args.scenario
    keys = list(ALL_SCENARIOS) if key == "all" else [key]
    if key != "all" and key not in ALL_SCENARIOS:
        print(f"Bilinmeyen senaryo: {key}. Mevcut: {', '.join(ALL_SCENARIOS)}")
        return 1
    total = 0
    for k in keys:
        print(f"\n### Senaryo {k}\n")
        events = ALL_SCENARIOS[k]()
        _print_events(events)
        print()
        findings = _analyze(events)
        _print_findings(findings)
        total += len(findings)
    print(f"\nToplam bulgu: {total}")
    return 0


def cmd_list(_args):
    print("Mevcut senaryolar:")
    for k, fn in ALL_SCENARIOS.items():
        doc = (fn.__doc__ or "").strip().splitlines()[0]
        print(f"  {k:<8} {doc}")


def main(argv=None):
    p = argparse.ArgumentParser(description="ATM-Guard — ATM/Core Banking mutabakat test aracı")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("demo", help="A1 demo senaryosu").set_defaults(func=cmd_demo)

    pr = sub.add_parser("run", help="senaryo(lar) koş")
    pr.add_argument("scenario", help="senaryo kodu (A1/A2/A4/clean) veya 'all'")
    pr.set_defaults(func=cmd_run)

    sub.add_parser("list", help="senaryoları listele").set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
