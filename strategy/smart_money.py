from dataclasses import dataclass
from typing import Optional, List
from loguru import logger

from data.binance_rest import BinanceRest, FundingRate
from data.binance_ws import BinanceWebSocket, Liquidation
from config import CONFIG


@dataclass
class SmartMoneySignal:
    direction: int          # +1 = LONG, -1 = SHORT, 0 = NÖTR
    oi_signal: int          # +1 / -1 / 0
    funding_signal: int     # +1 / -1 / 0
    liquidation_signal: int # +1 / -1 / 0
    cvd_signal: int         # +1 / -1 / 0
    reasons: List[str]


class SmartMoneyAnalyzer:
    def evaluate(
        self,
        rest: BinanceRest,
        ws: BinanceWebSocket,
    ) -> SmartMoneySignal:
        reasons = []
        oi_sig = 0
        funding_sig = 0
        liq_sig = 0
        cvd_sig = 0

        # --- OI Analizi ---
        oi_change = rest.get_oi_change_pct(lookback_count=1)
        klines = ws.get_klines_list(2)
        price_change = 0.0
        if len(klines) >= 2:
            price_change = (klines[-1].close - klines[-2].close) / klines[-2].close

        if abs(oi_change) >= CONFIG.OI_CHANGE_THRESHOLD:
            if oi_change > 0 and price_change < 0:
                # OI artıyor, fiyat düşüyor → short squeeze hazırlığı
                oi_sig = 1
                reasons.append(f"OI+{oi_change*100:.2f}% fiyat↓ → squeeze LONG")
            elif oi_change > 0 and price_change > 0:
                # trend devam
                oi_sig = 1
                reasons.append(f"OI+{oi_change*100:.2f}% fiyat↑ → trend güçlü LONG")
            elif oi_change < 0:
                # pozisyon kapatılıyor
                oi_sig = -1
                reasons.append(f"OI{oi_change*100:.2f}% → pozisyon kapanıyor SHORT")

        # --- Funding Rate ---
        if rest._last_funding:
            fr = rest._last_funding.rate
            if fr > CONFIG.FUNDING_RATE_HIGH:
                funding_sig = -1
                reasons.append(f"Funding={fr*100:.4f}% (yüksek) → long kalabalık SHORT")
            elif fr < CONFIG.FUNDING_RATE_LOW:
                funding_sig = 1
                reasons.append(f"Funding={fr*100:.4f}% (düşük) → short kalabalık LONG")

        # --- Likidasyon Avcısı ---
        recent_liqs = ws.get_recent_liquidations(seconds=300)
        long_liq_usd = sum(l.usd_value for l in recent_liqs if l.side == "BUY")
        short_liq_usd = sum(l.usd_value for l in recent_liqs if l.side == "SELL")

        if short_liq_usd > CONFIG.LIQUIDATION_THRESHOLD_USD:
            liq_sig = 1
            reasons.append(f"SHORT likidasyon ${short_liq_usd:,.0f} → sıçrama LONG")
        elif long_liq_usd > CONFIG.LIQUIDATION_THRESHOLD_USD:
            liq_sig = -1
            reasons.append(f"LONG likidasyon ${long_liq_usd:,.0f} → düşüş SHORT")

        # --- CVD ---
        cvd = ws.get_cvd(lookback=CONFIG.CVD_LOOKBACK)
        if cvd > 0:
            cvd_sig = 1
            reasons.append(f"CVD={cvd:.2f} → alım baskısı LONG")
        elif cvd < 0:
            cvd_sig = -1
            reasons.append(f"CVD={cvd:.2f} → satış baskısı SHORT")

        total = oi_sig + funding_sig + liq_sig + cvd_sig
        direction = 1 if total > 0 else (-1 if total < 0 else 0)

        return SmartMoneySignal(
            direction=direction,
            oi_signal=oi_sig,
            funding_signal=funding_sig,
            liquidation_signal=liq_sig,
            cvd_signal=cvd_sig,
            reasons=reasons,
        )
