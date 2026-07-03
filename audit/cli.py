"""Komut satırı arayüzü.

Kullanım:
    python -m audit.cli <yol> [<yol> ...] [--md rapor.md] [--min low|medium|high|critical]

<yol> bir .sol dosyası ya da .sol içeren bir klasör olabilir.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .scanner import Scanner, Severity
from .layers import LAYERS
from .report import render_terminal, render_markdown, summarize

_SEV_BY_NAME = {s.name.lower(): s for s in Severity}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="audit",
        description="Katman bazlı Solidity açık tarayıcı (heuristik ilk geçiş).",
    )
    parser.add_argument("paths", nargs="+", help=".sol dosyası veya klasör")
    parser.add_argument("--md", metavar="DOSYA", help="Markdown raporu yaz")
    parser.add_argument("--min", default="info", choices=list(_SEV_BY_NAME),
                        help="Bu önemin altındaki bulguları gizle")
    parser.add_argument("--no-color", action="store_true", help="Renksiz çıktı")
    parser.add_argument("--fail-on", default=None, choices=list(_SEV_BY_NAME),
                        help="Bu önem ve üstünde bulgu varsa çıkış kodu 1")
    args = parser.parse_args(argv)

    min_sev = _SEV_BY_NAME[args.min]
    scanner = Scanner(LAYERS)
    results = scanner.scan_paths(args.paths)

    # Eşik altını filtrele
    for path in results:
        results[path] = [f for f in results[path] if f.severity >= min_sev]

    for path, findings in results.items():
        print(render_terminal(path, findings, color=not args.no_color))

    grand = {s: 0 for s in Severity}
    for findings in results.values():
        for s, n in summarize(findings).items():
            grand[s] += n
    total = sum(grand.values())
    print(f"\n>>> Toplam: {total} bulgu · "
          + " ".join(f"{s.label}={grand[s]}" for s in reversed(Severity) if grand[s]))

    if args.md:
        Path(args.md).write_text(render_markdown(results), encoding="utf-8")
        print(f">>> Markdown raporu yazıldı: {args.md}")

    if args.fail_on:
        threshold = _SEV_BY_NAME[args.fail_on]
        if any(f.severity >= threshold for fs in results.values() for f in fs):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
