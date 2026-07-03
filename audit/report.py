"""Bulguları terminal ve markdown olarak biçimlendirir."""

from __future__ import annotations

from .scanner import Finding, Severity
from .layers import LAYER_TITLES

_COLOR = {
    Severity.CRITICAL: "\033[95m",
    Severity.HIGH: "\033[91m",
    Severity.MEDIUM: "\033[93m",
    Severity.LOW: "\033[96m",
    Severity.INFO: "\033[90m",
}
_RESET = "\033[0m"


def summarize(findings: list[Finding]) -> dict[Severity, int]:
    counts = {s: 0 for s in Severity}
    for f in findings:
        counts[f.severity] += 1
    return counts


def render_terminal(path: str, findings: list[Finding], color: bool = True) -> str:
    out = [f"\n=== {path} ==="]
    if not findings:
        out.append("  Dedektörler bir şey işaretlemedi. (Bu 'açık yok' demek DEĞİL — elle inceleme şart.)")
        return "\n".join(out)

    counts = summarize(findings)
    badge = "  ".join(
        f"{s.label}: {counts[s]}" for s in reversed(Severity) if counts[s]
    )
    out.append(f"  Özet: {badge}")
    out.append("")

    for f in findings:
        c = _COLOR[f.severity] if color else ""
        r = _RESET if color else ""
        layer_title = LAYER_TITLES.get(f.layer, f.layer)
        out.append(f"  {c}[{f.severity.label:8}]{r} L{f.line:<4} {layer_title} · {f.detector}")
        out.append(f"           {f.message}")
        out.append(f"           kod: {f.snippet}")
        out.append(f"           çözüm: {f.remediation}")
        out.append("")
    return "\n".join(out)


def render_markdown(results: dict[str, list[Finding]]) -> str:
    lines = ["# Katman bazlı denetim raporu", ""]
    total = sum(len(v) for v in results.values())
    lines.append(f"Taranan dosya: {len(results)} · Toplam bulgu: {total}")
    lines.append("")
    lines.append("> Bulgular heuristiktir — inceleme noktasıdır, kanıt değil. Her birini elle doğrula.")
    lines.append("")

    for path, findings in results.items():
        lines.append(f"## `{path}`")
        if not findings:
            lines.append("_Dedektörler işaretlemedi (elle inceleme yine de gerekli)._")
            lines.append("")
            continue
        counts = summarize(findings)
        badge = ", ".join(f"{s.label}: {counts[s]}" for s in reversed(Severity) if counts[s])
        lines.append(f"**Özet:** {badge}")
        lines.append("")
        lines.append("| Önem | Satır | Katman | Dedektör | Bulgu |")
        lines.append("|---|---|---|---|---|")
        for f in findings:
            layer_title = LAYER_TITLES.get(f.layer, f.layer)
            msg = f.message.replace("|", "\\|")
            lines.append(f"| {f.severity.label} | {f.line} | {layer_title} | {f.detector} | {msg} |")
        lines.append("")
    return "\n".join(lines)
