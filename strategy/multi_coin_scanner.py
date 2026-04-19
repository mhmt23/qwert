"""
Multi-coin Trap Strategy tarayıcısı.

Top 15 koini aynı anda tarar. En yüksek confluence skoru alan koin
o döngüde işlem alır.

Koinler: BTC, ETH, BNB, SOL, XRP, DOGE, AVAX, LINK, MATIC, DOT,
         LTC, ADA, ATOM, NEAR, ARB
"""
import asyncio
from dataclasses import dataclass
from typing import List, Optional, Dict
from loguru import logger

from data.binance_rest import BinanceRest
from strategy.funding_signal import evaluate_funding, FundingSignal


TOP_COINS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "MATICUSDT", "DOTUSDT",
    "LTCUSDT", "ADAUSDT", "ATOMUSDT", "NEARUSDT", "ARBUSDT",
]


@dataclass
class CoinScore:
    symbol: str
    direction: int          # +1 LONG, -1 SHORT
    total_score: int        # toplam confluence puanı
    funding_score: int
    oi_change_pct: float
    funding_rate: float
    reason: str


async def scan_all_coins(rest: BinanceRest, coins: List[str] = None) -> List[CoinScore]:
    """
    Tüm koinler için funding + OI verisi çek, sırala.
    En yüksek skorlu koin işleme alınır.
    """
    if coins is None:
        coins = TOP_COINS

    results: List[CoinScore] = []

    async def fetch_coin(symbol: str):
        try:
            funding_obj = await rest.get_funding_rate(symbol)
            oi_now = await rest.get_open_interest(symbol)
            oi_change = rest.get_oi_change_pct(lookback_count=1)

            sig = evaluate_funding(funding_obj.rate, oi_change)
            if sig.direction == 0:
                return

            results.append(CoinScore(
                symbol=symbol,
                direction=sig.direction,
                total_score=sig.score,
                funding_score=1 if abs(funding_obj.rate) > 0.0005 else 0,
                oi_change_pct=oi_change,
                funding_rate=funding_obj.rate,
                reason=f"{symbol}: {sig.reason}",
            ))
        except Exception as e:
            logger.debug(f"Coin scan {symbol}: {e}")

    await asyncio.gather(*[fetch_coin(s) for s in coins])

    # En yüksek skor, aynı skorda funding_rate mutlak değeri büyük olan öne geçer
    results.sort(key=lambda x: (x.total_score, abs(x.funding_rate)), reverse=True)
    return results


def best_coin(scores: List[CoinScore]) -> Optional[CoinScore]:
    """En yüksek puanlı koin. Skor >= 1 olmalı."""
    if not scores:
        return None
    top = scores[0]
    return top if top.total_score >= 1 else None
