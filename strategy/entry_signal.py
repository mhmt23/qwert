from collections import deque
from dataclasses import dataclass
from typing import List
import numpy as np
from loguru import logger

from data.binance_ws import BinanceWebSocket, Kline
from config import CONFIG


@dataclass
class EntrySignal:
    direction: int           # +1 LONG, -1 SHORT, 0 sinyal yok
    ema_trend: bool
    rsi_zone: bool
    volume_spike: bool
    liquidity_sweep: bool
    cvd_confirm: bool
    rsi_value: float
    reasons: List[str]


def _calc_rsi(closes: List[float], period: int = 6) -> float:
    if len(closes) < period + 1:
        return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    avg_gain = sum(gains[-period:]) / period if gains else 0
    avg_loss = sum(losses[-period:]) / period if losses else 1e-9
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_ema(closes: List[float], span: int) -> float:
    if not closes:
        return 0.0
    k = 2 / (span + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return ema


class EntrySignalDetector:
    def evaluate(self, ws: BinanceWebSocket) -> EntrySignal:
        klines = ws.get_klines_list(CONFIG.VOLUME_LOOKBACK + 15)
        reasons = []

        if len(klines) < CONFIG.VOLUME_LOOKBACK + 5:
            return EntrySignal(0, False, False, False, False, False, 50.0, ["Yeterli veri yok"])

        closes = [k.close for k in klines]
        last   = klines[-1]
        prev   = klines[-(CONFIG.VOLUME_LOOKBACK + 1):-1]

        # --- EMA Trend (1m) ---
        ema5  = _calc_ema(closes[:-1], 5)   # shift(1): mevcut mum hariç
        ema13 = _calc_ema(closes[:-1], 13)
        ema_bull = ema5 > ema13
        ema_bear = ema5 < ema13
        ema_trend = ema_bull or ema_bear
        if ema_bull:
            reasons.append(f"EMA5({ema5:.1f}) > EMA13({ema13:.1f}) → yükseliş")
        elif ema_bear:
            reasons.append(f"EMA5({ema5:.1f}) < EMA13({ema13:.1f}) → düşüş")

        # --- RSI(6) ---
        rsi = _calc_rsi(closes[:-1], period=6)
        rsi_long_ok  = rsi < 50
        rsi_short_ok = rsi > 50
        rsi_zone = rsi < 35 or rsi > 65
        if rsi < 35:
            reasons.append(f"RSI={rsi:.1f} aşırı satım → LONG")
        elif rsi > 65:
            reasons.append(f"RSI={rsi:.1f} aşırı alım → SHORT")
        else:
            reasons.append(f"RSI={rsi:.1f}")

        # --- Volume Spike ---
        avg_vol    = sum(k.volume for k in prev) / len(prev)
        vol_spike  = last.volume >= avg_vol * CONFIG.VOLUME_SPIKE_MULTIPLIER
        if vol_spike:
            reasons.append(f"Hacim spike {last.volume/avg_vol:.1f}x")

        # --- Likidite Avı ---
        sweep_klines  = klines[-(CONFIG.LIQUIDITY_SWEEP_LOOKBACK + 1):-1]
        recent_low    = min(k.low  for k in sweep_klines)
        recent_high   = max(k.high for k in sweep_klines)
        rng           = last.high - last.low
        liq_long  = (last.low < recent_low)  and rng > 0 and (last.close > last.low  + rng * 0.5)
        liq_short = (last.high > recent_high) and rng > 0 and (last.close < last.high - rng * 0.5)
        if liq_long:
            reasons.append("Likidite avı LONG")
        if liq_short:
            reasons.append("Likidite avı SHORT")

        # --- CVD ---
        cvd = ws.get_cvd(lookback=CONFIG.CVD_LOOKBACK)
        cvd_long  = cvd > 0
        cvd_short = cvd < 0
        cvd_confirm = cvd_long or cvd_short

        # --- Yön Karar ---
        long_score = (
            int(ema_bull) +
            int(rsi_long_ok or rsi < 35) +
            int(vol_spike) +
            int(liq_long) +
            int(cvd_long)
        )
        short_score = (
            int(ema_bear) +
            int(rsi_short_ok or rsi > 65) +
            int(vol_spike) +
            int(liq_short) +
            int(cvd_short)
        )

        if long_score > short_score:
            direction = 1
        elif short_score > long_score:
            direction = -1
        else:
            direction = 0

        return EntrySignal(
            direction=direction,
            ema_trend=ema_trend,
            rsi_zone=rsi_zone,
            volume_spike=vol_spike,
            liquidity_sweep=liq_long or liq_short,
            cvd_confirm=cvd_confirm,
            rsi_value=rsi,
            reasons=reasons,
        )
