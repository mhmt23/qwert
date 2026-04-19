"""
Funding Rate Extreme + OI Spike sinyali.

Mantık (Trap Strategy — Katman A):
  Funding > +0.1%  → Longlar aşırı ödüyor → SHORT squeeze yakın
  Funding < -0.05% → Shortlar aşırı ödüyor → LONG squeeze yakın
  OI spike eşlik ediyorsa sinyal güçlenir (kaldıraçlı pozisyon birikimi)
"""
from dataclasses import dataclass


@dataclass
class FundingSignal:
    direction: int       # +1 LONG, -1 SHORT, 0 yok
    funding_rate: float
    oi_change_pct: float
    reason: str
    score: int           # 0-2: funding + oi_spike


FUNDING_EXTREME_HIGH = 0.001     # > +0.10% → longs over-leveraged → SHORT
FUNDING_EXTREME_LOW  = -0.0005  # < -0.05% → shorts over-leveraged → LONG
OI_SPIKE_THRESHOLD   = 0.01     # OI %1+ artış = spike


def evaluate_funding(funding_rate: float, oi_change_pct: float) -> FundingSignal:
    """
    Anlık funding ve OI değişiminden squeeze sinyali üretir.
    Canlı bot: her 5 dakikada BinanceRest'ten çekilir.
    Backtest: tarihsel funding + OI ile aynı mantık çalışır.
    """
    score = 0
    direction = 0
    reason_parts = []

    # --- Funding Rate ---
    if funding_rate > FUNDING_EXTREME_HIGH:
        direction = -1
        score += 1
        reason_parts.append(f"Funding %{funding_rate*100:.3f} aşırı long → SHORT")
    elif funding_rate < FUNDING_EXTREME_LOW:
        direction = 1
        score += 1
        reason_parts.append(f"Funding %{funding_rate*100:.3f} aşırı short → LONG")

    # --- OI Spike (yön bağımsız, sinyali güçlendirir) ---
    if abs(oi_change_pct) >= OI_SPIKE_THRESHOLD:
        score += 1
        reason_parts.append(f"OI spike %{oi_change_pct*100:.2f}")

    reason = " | ".join(reason_parts) if reason_parts else "Funding normal"

    return FundingSignal(
        direction=direction,
        funding_rate=funding_rate,
        oi_change_pct=oi_change_pct,
        reason=reason,
        score=score,
    )
