"""
Likidite/Likidasyon Zonu Avı.

Mantık:
  Büyük likidasyonlar (force orders) belirli fiyat seviyelerinde kümelenir.
  Bot bu seviyelere yaklaşıldığında ters yön squeeze bekler.

Backtest için:
  Tarihsel forceOrder verisi bulunmadığından yaklaşık simülasyon:
  Son N mumun high/low pivot noktaları = potansiyel likidasyon bölgesi.
  Fiyat bu bölgeye girdiğinde ve hızla geri dönüyorsa sinyal üret.

Canlı bot için:
  BinanceWebSocket.get_recent_liquidations() kullanır.
"""
from dataclasses import dataclass, field
from typing import List


@dataclass
class LiquidationZone:
    price_level: float
    side: str           # "LONG_LIQ" veya "SHORT_LIQ"
    total_usd: float    # toplam likidasyon hacmi


@dataclass
class LiqZoneSignal:
    direction: int      # +1 LONG (short likidasyon = yukarı baskı), -1 SHORT, 0 yok
    nearest_zone: float
    distance_pct: float
    total_liq_usd: float
    reason: str


MIN_LIQ_USD = 500_000     # Bu kadar $ likidasyon olmadan sinyal yok
ZONE_PROXIMITY_PCT = 0.002  # Fiyat zona %0.2 yakınsa "proximity" kabul


def evaluate_liquidations_live(
    current_price: float,
    recent_liquidations: list,  # BinanceWebSocket.get_recent_liquidations() çıktısı
) -> LiqZoneSignal:
    """
    Canlı bot: son likidasyon verilerini değerlendirir.
    recent_liquidations = [{"side": "BUY"/"SELL", "price": float, "qty": float, "usd": float}, ...]
    """
    if not recent_liquidations:
        return LiqZoneSignal(0, 0.0, 0.0, 0.0, "Likidasyon yok")

    long_liq_usd  = sum(l["usd"] for l in recent_liquidations if l["side"] == "SELL")
    short_liq_usd = sum(l["usd"] for l in recent_liquidations if l["side"] == "BUY")

    direction = 0
    reason = ""

    if short_liq_usd > long_liq_usd and short_liq_usd >= MIN_LIQ_USD:
        # Short likidasyon (BUY liq) → yukarı fırlatma
        direction = 1
        reason = f"Short liq ${short_liq_usd/1e6:.2f}M → LONG fırlatma"
        total_usd = short_liq_usd
    elif long_liq_usd > short_liq_usd and long_liq_usd >= MIN_LIQ_USD:
        # Long likidasyon (SELL liq) → aşağı baskı
        direction = -1
        reason = f"Long liq ${long_liq_usd/1e6:.2f}M → SHORT baskı"
        total_usd = long_liq_usd
    else:
        return LiqZoneSignal(0, 0.0, 0.0, long_liq_usd + short_liq_usd, "Likidasyon eşiği altında")

    return LiqZoneSignal(
        direction=direction,
        nearest_zone=current_price,
        distance_pct=0.0,
        total_liq_usd=total_usd,
        reason=reason,
    )


def evaluate_liquidations_backtest(
    current_price: float,
    recent_highs: List[float],  # son N mumun high değerleri
    recent_lows: List[float],   # son N mumun low değerleri
    price_change_pct: float,    # son 2 mumun fiyat değişimi
) -> LiqZoneSignal:
    """
    Backtest: Tarihsel likidasyon yoksa pivot-based yaklaşım.
    Pivot high'ların üstüne çıkış → short liq tetiklendi → LONG
    Pivot low'ların altına düşüş → long liq tetiklendi → SHORT
    """
    if not recent_highs or not recent_lows:
        return LiqZoneSignal(0, 0.0, 0.0, 0.0, "Veri yok")

    pivot_high = max(recent_highs)
    pivot_low  = min(recent_lows)

    swept_high = current_price > pivot_high
    swept_low  = current_price < pivot_low
    reverting  = abs(price_change_pct) > 0.001  # aktif hareket

    if swept_low and reverting and price_change_pct > 0:
        # Pivot low altına indi ve yukarı döndü → long liq avlandı
        dist = (pivot_low - current_price) / pivot_low
        return LiqZoneSignal(
            direction=1,
            nearest_zone=pivot_low,
            distance_pct=abs(dist),
            total_liq_usd=0.0,
            reason=f"Pivot low sweep ({pivot_low:.1f}) → LONG",
        )
    elif swept_high and reverting and price_change_pct < 0:
        # Pivot high üstüne çıktı ve aşağı döndü → short liq avlandı
        dist = (current_price - pivot_high) / pivot_high
        return LiqZoneSignal(
            direction=-1,
            nearest_zone=pivot_high,
            distance_pct=abs(dist),
            total_liq_usd=0.0,
            reason=f"Pivot high sweep ({pivot_high:.1f}) → SHORT",
        )

    return LiqZoneSignal(0, 0.0, 0.0, 0.0, "Likidasyon zonu avlanmıyor")
