from dataclasses import dataclass
from typing import List, Optional

from strategy.macro_filter import MacroResult, MarketBias
from strategy.smart_money import SmartMoneySignal
from strategy.entry_signal import EntrySignal
from config import CONFIG


@dataclass
class ConfluenceResult:
    should_trade: bool
    direction: int          # +1 LONG, -1 SHORT
    score: int              # 5 üzerinden
    size_multiplier: float  # 1.0 tam, 0.5 yarı
    all_reasons: List[str]


class ConfluenceScorer:
    def evaluate(
        self,
        macro: MacroResult,
        smart: SmartMoneySignal,
        entry: EntrySignal,
    ) -> ConfluenceResult:
        all_reasons = []
        scores = {"long": 0, "short": 0}

        # Makro filtre WAIT → işlem yok
        if macro.bias == MarketBias.WAIT:
            return ConfluenceResult(False, 0, 0, 0.0, macro.reasons)

        all_reasons.extend(macro.reasons)
        all_reasons.extend(smart.reasons)
        all_reasons.extend(entry.reasons)

        # Her katmanın yönüne göre puan
        # Katman 1: Makro
        if macro.bias == MarketBias.LONG_OK:
            scores["long"] += 1
        elif macro.bias == MarketBias.SHORT_OK:
            scores["short"] += 1
        elif macro.bias == MarketBias.BOTH_OK:
            scores["long"] += 0.5
            scores["short"] += 0.5

        # Katman 2: Smart Money (4 sinyal, her biri 0.5 puan = toplam 2 puan)
        for sig in [smart.oi_signal, smart.funding_signal,
                    smart.liquidation_signal, smart.cvd_signal]:
            if sig == 1:
                scores["long"] += 0.5
            elif sig == -1:
                scores["short"] += 0.5

        # Katman 3: Entry sinyali (3 sinyal, toplam 2 puan)
        if entry.volume_spike:
            if entry.direction == 1:
                scores["long"] += 0.67
            elif entry.direction == -1:
                scores["short"] += 0.67

        if entry.strong_candle:
            if entry.direction == 1:
                scores["long"] += 0.67
            elif entry.direction == -1:
                scores["short"] += 0.67

        if entry.liquidity_sweep:
            if entry.direction == 1:
                scores["long"] += 0.67
            elif entry.direction == -1:
                scores["short"] += 0.67

        # Yön belirle
        long_score = round(scores["long"])
        short_score = round(scores["short"])

        if long_score >= short_score and long_score >= CONFIG.MIN_CONFLUENCE_SCORE:
            direction = 1
            score = long_score
        elif short_score > long_score and short_score >= CONFIG.MIN_CONFLUENCE_SCORE:
            direction = -1
            score = short_score
        else:
            return ConfluenceResult(False, 0, max(long_score, short_score), 0.0, all_reasons)

        # Makro yönle çelişiyorsa iptal
        if macro.bias == MarketBias.LONG_OK and direction == -1:
            all_reasons.append("Makro filtreyle çelişiyor (LONG_OK vs SHORT signal)")
            return ConfluenceResult(False, 0, score, 0.0, all_reasons)
        if macro.bias == MarketBias.SHORT_OK and direction == 1:
            all_reasons.append("Makro filtreyle çelişiyor (SHORT_OK vs LONG signal)")
            return ConfluenceResult(False, 0, score, 0.0, all_reasons)

        # Pozisyon boyutu
        size_mult = 1.0 if score >= 5 else 0.5

        return ConfluenceResult(
            should_trade=True,
            direction=direction,
            score=score,
            size_multiplier=size_mult,
            all_reasons=all_reasons,
        )
