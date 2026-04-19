from dataclasses import dataclass
from enum import Enum
from typing import Optional
from loguru import logger

from data.cross_asset import CrossAssetData
from data.sentiment import SentimentData
from config import CONFIG


class MarketBias(Enum):
    LONG_OK = "LONG_OK"
    SHORT_OK = "SHORT_OK"
    BOTH_OK = "BOTH_OK"
    WAIT = "WAIT"


@dataclass
class MacroResult:
    bias: MarketBias
    reasons: list


class MacroFilter:
    def evaluate(
        self,
        cross: CrossAssetData,
        sentiment: SentimentData,
    ) -> MacroResult:
        reasons = []

        # VIX aşırı yüksekse hiç işlem yapma
        if cross.vix and cross.vix > CONFIG.VIX_PAUSE_THRESHOLD:
            reasons.append(f"VIX={cross.vix:.1f} > {CONFIG.VIX_PAUSE_THRESHOLD} → BEKLE")
            return MacroResult(bias=MarketBias.WAIT, reasons=reasons)

        long_score = 0
        short_score = 0

        # NASDAQ trendi
        if cross.nasdaq and cross.nasdaq_ema50:
            if cross.nasdaq > cross.nasdaq_ema50:
                long_score += 1
                reasons.append(f"NASDAQ({cross.nasdaq:.0f}) > EMA50({cross.nasdaq_ema50:.0f}) → risk-on")
            else:
                short_score += 1
                reasons.append(f"NASDAQ({cross.nasdaq:.0f}) < EMA50({cross.nasdaq_ema50:.0f}) → risk-off")

        # DXY güçlü hareket
        if cross.dxy_4h_change_pct is not None:
            if cross.dxy_4h_change_pct > CONFIG.DXY_STRONG_MOVE_PCT:
                short_score += 1
                reasons.append(f"DXY +{cross.dxy_4h_change_pct*100:.2f}% (4h) → kripto baskı")
            elif cross.dxy_4h_change_pct < -CONFIG.DXY_STRONG_MOVE_PCT:
                long_score += 1
                reasons.append(f"DXY {cross.dxy_4h_change_pct*100:.2f}% (4h) → kripto yükseliş")

        # Fear & Greed
        fg = sentiment.fear_greed_value
        if fg < CONFIG.FEAR_GREED_EXTREME_FEAR:
            long_score += 1
            reasons.append(f"F&G={fg} (aşırı korku) → LONG fırsat")
        elif fg > CONFIG.FEAR_GREED_EXTREME_GREED:
            short_score += 1
            reasons.append(f"F&G={fg} (aşırı açgözlülük) → SHORT dikkat")

        # Karar
        if long_score > short_score:
            bias = MarketBias.LONG_OK
        elif short_score > long_score:
            bias = MarketBias.SHORT_OK
        else:
            bias = MarketBias.BOTH_OK

        logger.debug(f"Makro filtre: {bias.value} | long={long_score} short={short_score}")
        return MacroResult(bias=bias, reasons=reasons)
