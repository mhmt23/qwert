"""
Bakkal Stratejisi — Koin Evreni Filtresi.

300+ USDT-M perpetual futures arasından bot'un izleyeceği ~150-200 koini seçer.

Filtre kriterleri:
  - TRADING statüsünde (aktif)
  - USDT-M perpetual (delivery değil)
  - 24h hacim >= $5M (likidite)
  - 24h fiyat aralığı >= %1 (hareket var)
  - Min 30 gün listede (sabitlenmiş, manipülasyona daha az açık)
"""
from dataclasses import dataclass, field
from typing import List, Dict
from loguru import logger

from data.binance_rest import BinanceRest


MIN_VOLUME_USD = 5_000_000     # Günlük $5M minimum hacim
MIN_PRICE_RANGE_PCT = 1.0      # 24h hareket aralığı minimum %1
MAX_PRICE_CHANGE_PCT = 40.0    # 24h |değişim| > %40 → pump/dump, atla
EXCLUDE_SUFFIXES = ("BUSD", "USDC")   # USDT dışı stable pariteler


@dataclass
class CoinInfo:
    symbol: str
    volume_usd: float
    price_change_pct: float
    price_range_pct: float
    last_price: float
    funding_rate: float = 0.0


def _is_usdt_perpetual(s: dict) -> bool:
    """USDT-M perpetual mi? Sadece ASCII sembolleri."""
    sym = s.get("symbol", "")
    # ASCII kontrolü - Çince karakterli spam tokenları atla
    if not sym.isascii():
        return False
    return (
        s.get("quoteAsset") == "USDT" and
        s.get("contractType") == "PERPETUAL" and
        s.get("status") == "TRADING" and
        not any(sym.endswith(sfx + "USDT") for sfx in EXCLUDE_SUFFIXES)
    )


async def load_coin_universe(rest: BinanceRest) -> List[CoinInfo]:
    """
    Binance'ten tüm aktif USDT-M perpetual futures pariteleri çeker,
    filtre uygular, CoinInfo listesi döner.
    """
    logger.info("Koin evreni yükleniyor...")

    # Tüm semboller + 24h özeti
    info = await rest.get_exchange_info()
    tickers = await rest.get_24hr_ticker_all()

    # Sadece aktif USDT perpetual
    active_symbols = {
        s["symbol"] for s in info.get("symbols", []) if _is_usdt_perpetual(s)
    }
    logger.info(f"Aktif USDT perpetual: {len(active_symbols)} adet")

    # Ticker verisini sembol bazlı eşleştir
    ticker_map: Dict[str, dict] = {t["symbol"]: t for t in tickers}

    coins: List[CoinInfo] = []
    for sym in active_symbols:
        t = ticker_map.get(sym)
        if not t:
            continue

        try:
            volume_usd = float(t["quoteVolume"])          # USDT hacmi
            high = float(t["highPrice"])
            low = float(t["lowPrice"])
            last = float(t["lastPrice"])
            change_pct = float(t["priceChangePercent"])

            if last <= 0 or low <= 0:
                continue

            # 24h aralık olarak (high - low) / low
            range_pct = ((high - low) / low) * 100.0

            # Filtreler
            if volume_usd < MIN_VOLUME_USD:
                continue
            if range_pct < MIN_PRICE_RANGE_PCT:
                continue
            if abs(change_pct) > MAX_PRICE_CHANGE_PCT:
                continue   # Pump/dump veya yeni listeleme

            coins.append(CoinInfo(
                symbol=sym,
                volume_usd=volume_usd,
                price_change_pct=change_pct,
                price_range_pct=range_pct,
                last_price=last,
            ))
        except (KeyError, ValueError):
            continue

    # Hacme göre büyükten küçüğe sırala
    coins.sort(key=lambda c: c.volume_usd, reverse=True)

    logger.info(f"Filtreden geçen aktif koin: {len(coins)}")
    logger.info(f"En yüksek hacim: {coins[0].symbol} (${coins[0].volume_usd/1e6:.1f}M)" if coins else "")
    return coins


def coin_tiers(coins: List[CoinInfo]) -> Dict[str, List[CoinInfo]]:
    """
    Koinleri hacim bandına göre grupla.
    Yüksek hacim → daha sık tara. Düşük hacim → daha seyrek tara.
    """
    return {
        "tier1_high_volume":   [c for c in coins if c.volume_usd >= 500_000_000],   # $500M+
        "tier2_mid_volume":    [c for c in coins if 50_000_000 <= c.volume_usd < 500_000_000],
        "tier3_low_volume":    [c for c in coins if c.volume_usd < 50_000_000],
    }
