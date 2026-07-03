"""Tarayıcı çekirdeği: kaynak dosyayı okur, katman dedektörlerini çalıştırır."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Callable, Iterable


class Severity(IntEnum):
    INFO = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4

    @property
    def label(self) -> str:
        return self.name.capitalize()


@dataclass
class Finding:
    layer: str
    detector: str
    severity: Severity
    line: int
    snippet: str
    message: str
    remediation: str

    def sort_key(self):
        # Önce en yüksek önem, sonra dosya sırası
        return (-int(self.severity), self.line)


@dataclass
class SourceFile:
    path: str
    text: str
    lines: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "SourceFile":
        p = Path(path)
        text = p.read_text(encoding="utf-8", errors="replace")
        return cls(path=str(p), text=text, lines=text.splitlines())

    def line_of(self, index: int) -> int:
        """Karakter ofsetinden 1-tabanlı satır numarası."""
        return self.text.count("\n", 0, index) + 1

    def strip_comments(self) -> str:
        """Yorum ve string literalleri boşlukla değiştirir (yanlış pozitifi azaltır)."""
        text = re.sub(r"//[^\n]*", "", self.text)
        text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group().count("\n"), text, flags=re.DOTALL)
        return text


# Bir dedektör: kaynak dosyayı alır, Finding üretir.
Detector = Callable[[SourceFile], Iterable[Finding]]


class Scanner:
    def __init__(self, detectors: dict[str, list[Detector]]):
        # detectors: {katman_adı: [dedektör, ...]}
        self.detectors = detectors

    def scan_file(self, path: str | Path) -> list[Finding]:
        src = SourceFile.load(path)
        findings: list[Finding] = []
        for layer, dets in self.detectors.items():
            for det in dets:
                findings.extend(det(src))
        findings.sort(key=lambda f: f.sort_key())
        return findings

    def scan_paths(self, paths: Iterable[str | Path]) -> dict[str, list[Finding]]:
        results: dict[str, list[Finding]] = {}
        for path in paths:
            for sol in _iter_sol_files(path):
                results[str(sol)] = self.scan_file(sol)
        return results


def _iter_sol_files(path: str | Path) -> Iterable[Path]:
    p = Path(path)
    if p.is_dir():
        yield from sorted(p.rglob("*.sol"))
    elif p.suffix == ".sol":
        yield p
