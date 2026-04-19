"""
Bakkal Stratejisi — Sinyal Üretici.

Hibrit rejim adaptif stratejisi:
  ADX > 25 → TREND rejimi → EMA crossover + RSI onay
  ADX 20-25 → BELİRSİZLİK → işlem yok
  ADX < 20 → RANGE rejimi → RSI/BB mean reversion

Yön: rejime göre belirlenir.
Skor: 0-5 puan (rejim + yön + hacim + funding + CVD).
TP/SL: ATR-bazlı (her koin kendi volatilitesine ayarlanır).
"""
from dataclasses import dataclass, field
from typing import Optional, Tuple
import numpy as np
import pandas as pd


# --- Eşikler ---
ADX_TREND_MIN    = 25.0     # Trend rejimi başlangıcı
ADX_RANGE_MAX    = 20.0     # Range rejimi üst sınırı
RSI_OVERSOLD     = 30.0
RSI_OVERBOUGHT   = 70.0
BB_STD           = 2.0
VOLUME_MULT      = 1.5
FUNDING_EXTREME_HIGH = 0.0005     # > +0.05% — yeterince aşırı
FUNDING_EXTREME_LOW  = -0.0003    # < -0.03%

# TP/SL ATR çarpanları
TP_ATR_MULT = 1.5
SL_ATR_MULT = 1.0

# Rejim kontrol bayrağı — testlerde açıp kapamak için
ENABLE_TREND_REGIME = True
ENABLE_RANGE_REGIME = True


@dataclass
class BakkalSignal:
    direction: int               # +1 LONG, -1 SHORT, 0 yok
    score: int                   # 0-5
    regime: str                  # "TREND", "RANGE", "UNCERTAIN"
    entry_price: float
    tp_price: float
    sl_price: float
    atr: float
    reasons: list = field(default_factory=list)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    dm_pos = (high - high.shift(1)).clip(lower=0)
    dm_neg = (low.shift(1) - low).clip(lower=0)
    dm_pos = dm_pos.where(dm_pos > dm_neg, 0)
    dm_neg = dm_neg.where(dm_neg > dm_pos, 0)
    atr = tr.rolling(period).mean()
    di_pos = 100 * dm_pos.rolling(period).mean() / atr.replace(0, np.nan)
    di_neg = 100 * dm_neg.rolling(period).mean() / atr.replace(0, np.nan)
    dx = 100 * (di_pos - di_neg).abs() / (di_pos + di_neg).replace(0, np.nan)
    return dx.rolling(period).mean()


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tüm göstergeleri hesaplar. Tümü .shift(1) ile look-ahead önler.
    Bu fonksiyon tüm backtest ve canlı sinyal için merkezdir.
    """
    out = df.copy()

    # Indicators (shift ile kapanmış mum verisi)
    out["adx"]   = _adx(df, 14).shift(1)
    out["rsi"]   = _rsi(df["close"], 14).shift(1)
    out["ema9"]  = df["close"].ewm(span=9, adjust=False).mean().shift(1)
    out["ema21"] = df["close"].ewm(span=21, adjust=False).mean().shift(1)
    out["atr"]   = _atr(df, 14).shift(1)

    # Bollinger Bands (20, 2)
    bb_mid = df["close"].rolling(20).mean().shift(1)
    bb_std = df["close"].rolling(20).std().shift(1)
    out["bb_mid"]   = bb_mid
    out["bb_upper"] = bb_mid + BB_STD * bb_std
    out["bb_lower"] = bb_mid - BB_STD * bb_std

    # Volume oranı
    out["vol_avg"]   = df["volume"].rolling(20).mean().shift(1)
    out["vol_ratio"] = df["volume"].shift(1) / out["vol_avg"].replace(0, np.nan)

    # Önceki EMA durumu (taze cross tespiti için)
    out["ema_bull"] = out["ema9"] > out["ema21"]
    out["ema_bull_prev"] = (df["close"].ewm(span=9, adjust=False).mean().shift(2) >
                            df["close"].ewm(span=21, adjust=False).mean().shift(2))
    out["fresh_bull_cross"] =  out["ema_bull"] & ~out["ema_bull_prev"]
    out["fresh_bear_cross"] = ~out["ema_bull"] &  out["ema_bull_prev"]

    return out


def evaluate_row(
    row: pd.Series,
    funding_rate: float = 0.0,
    cvd_positive: Optional[bool] = None,
) -> BakkalSignal:
    """
    Tek bir mum kapanışında (row) sinyal üretir.
    Backtest: df.iloc[i] üzerinden çağırılır.
    Canlı bot: en son kapalı mum üzerinden çağırılır.
    """
    close   = row["close"]
    adx     = row.get("adx", np.nan)
    rsi     = row.get("rsi", np.nan)
    ema9    = row.get("ema9", np.nan)
    ema21   = row.get("ema21", np.nan)
    atr     = row.get("atr", np.nan)
    bb_up   = row.get("bb_upper", np.nan)
    bb_low  = row.get("bb_lower", np.nan)
    vol_rt  = row.get("vol_ratio", np.nan)
    fresh_b = row.get("fresh_bull_cross", False)
    fresh_s = row.get("fresh_bear_cross", False)

    reasons = []
    score = 0
    direction = 0
    regime = "UNCERTAIN"

    # --- NaN koruması ---
    if pd.isna(adx) or pd.isna(atr) or atr <= 0:
        return BakkalSignal(0, 0, "UNCERTAIN", close, close, close, 0.0,
                            ["Göstergeler hazır değil"])

    # --- REJİM ---
    if adx >= ADX_TREND_MIN and ENABLE_TREND_REGIME:
        regime = "TREND"
        score += 1
        reasons.append(f"ADX={adx:.1f} TREND")

        # Trend yönü: EMA crossover
        if ema9 > ema21:
            direction = 1
            score += 1
            reasons.append(f"EMA9>EMA21 LONG")
            if fresh_b:
                score += 1
                reasons.append("Taze LONG cross")
        elif ema9 < ema21:
            direction = -1
            score += 1
            reasons.append(f"EMA9<EMA21 SHORT")
            if fresh_s:
                score += 1
                reasons.append("Taze SHORT cross")

    elif adx <= ADX_RANGE_MAX and ENABLE_RANGE_REGIME:
        regime = "RANGE"
        score += 1
        reasons.append(f"ADX={adx:.1f} RANGE")

        # Range yönü: RSI + BB
        if not pd.isna(rsi):
            if rsi < RSI_OVERSOLD and close <= bb_low:
                direction = 1
                score += 2  # güçlü mean reversion sinyali
                reasons.append(f"RSI={rsi:.1f} + BB alt → LONG")
            elif rsi > RSI_OVERBOUGHT and close >= bb_up:
                direction = -1
                score += 2
                reasons.append(f"RSI={rsi:.1f} + BB üst → SHORT")
            elif rsi < RSI_OVERSOLD:
                direction = 1
                score += 1
                reasons.append(f"RSI={rsi:.1f} oversold")
            elif rsi > RSI_OVERBOUGHT:
                direction = -1
                score += 1
                reasons.append(f"RSI={rsi:.1f} overbought")
    else:
        # ADX 20-25 arası belirsizlik → işlem yok
        return BakkalSignal(0, 0, "UNCERTAIN", close, close, close, atr,
                            [f"ADX={adx:.1f} belirsiz"])

    # Yön bulunamadıysa çık
    if direction == 0:
        return BakkalSignal(0, score, regime, close, close, close, atr, reasons)

    # --- HACİM ONAYI ---
    if not pd.isna(vol_rt) and vol_rt >= VOLUME_MULT:
        score += 1
        reasons.append(f"Hacim {vol_rt:.1f}x")

    # --- FUNDING RATE ---
    if direction == 1 and funding_rate < FUNDING_EXTREME_LOW:
        score += 1
        reasons.append(f"Funding %{funding_rate*100:.3f} short-kalabalık → LONG fırsat")
    elif direction == -1 and funding_rate > FUNDING_EXTREME_HIGH:
        score += 1
        reasons.append(f"Funding %{funding_rate*100:.3f} long-kalabalık → SHORT fırsat")

    # --- CVD / ORDER FLOW ---
    if cvd_positive is not None:
        if (direction == 1 and cvd_positive) or (direction == -1 and not cvd_positive):
            score += 1
            reasons.append("CVD yönle uyumlu")

    # Skor 5'i geçemez
    score = min(score, 5)

    # --- TP / SL ---
    if direction == 1:
        tp = close + TP_ATR_MULT * atr
        sl = close - SL_ATR_MULT * atr
    else:
        tp = close - TP_ATR_MULT * atr
        sl = close + SL_ATR_MULT * atr

    return BakkalSignal(
        direction=direction,
        score=score,
        regime=regime,
        entry_price=close,
        tp_price=tp,
        sl_price=sl,
        atr=atr,
        reasons=reasons,
    )


def direction_flipped(current_side: int, row: pd.Series) -> bool:
    """
    Soft exit kontrolü: açık pozisyonun yön sinyali tersine döndü mü?
    LONG pozisyon + EMA bear cross → True (kapat)
    SHORT pozisyon + EMA bull cross → True (kapat)
    """
    ema9 = row.get("ema9", np.nan)
    ema21 = row.get("ema21", np.nan)
    if pd.isna(ema9) or pd.isna(ema21):
        return False

    if current_side == 1 and ema9 < ema21:
        return True
    if current_side == -1 and ema9 > ema21:
        return True
    return False
