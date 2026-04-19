"""
Bakkal Beyin — Canlı Tahmin Modülü.

- Geçmiş candidates.parquet verisiyle eğitilir
- Her gün yeni veri gelince online retrain
- main_bakkal.py her sinyalde predict() çağırır
"""
from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger
from sklearn.ensemble import HistGradientBoostingClassifier


MODEL_PATH    = Path("ml/model.pkl")
CANDIDATES    = Path("ml/out/candidates.parquet")

FEATURES = [
    "adx", "rsi", "ema_diff_pct", "vol_ratio", "bb_pos",
    "atr_pct", "funding_rate", "hour", "dow", "direction",
]


class BakkalBrain:
    def __init__(self, threshold: float = 0.60):
        self.threshold = threshold
        self.model: Optional[HistGradientBoostingClassifier] = None
        self.training_size: int = 0

    # --- Eğitim ---
    def fit(self, df: pd.DataFrame) -> bool:
        """df: features + 'label' kolonu."""
        if len(df) < 100 or df["label"].nunique() < 2:
            logger.warning(f"Yetersiz eğitim verisi (n={len(df)}).")
            return False
        X = df[FEATURES].values
        y = df["label"].values
        self.model = HistGradientBoostingClassifier(
            max_iter=300, max_depth=6, learning_rate=0.05,
            min_samples_leaf=20, random_state=42,
        )
        self.model.fit(X, y)
        self.training_size = len(df)
        logger.info(f"Brain eğitildi: {len(df)} örnek, "
                    f"ham WIN oranı: %{y.mean()*100:.1f}")
        return True

    def fit_from_cache(self) -> bool:
        """ml/out/candidates.parquet'ten ilk eğitim."""
        if not CANDIDATES.exists():
            logger.error(f"Yok: {CANDIDATES} — önce walk_forward_brain.py çalıştır.")
            return False
        df = pd.read_parquet(CANDIDATES)
        return self.fit(df)

    # --- Tahmin ---
    def predict_proba(self, features: dict) -> float:
        """Tek sinyal için P(WIN)."""
        if self.model is None:
            return 0.0
        x = np.array([[features.get(f, 0.0) for f in FEATURES]])
        return float(self.model.predict_proba(x)[0, 1])

    def accept(self, features: dict) -> tuple[bool, float]:
        """True/False + olasılık döndürür."""
        p = self.predict_proba(features)
        return (p >= self.threshold, p)

    # --- Kalıcılık ---
    def save(self, path: Path = MODEL_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({
                "model": self.model,
                "threshold": self.threshold,
                "training_size": self.training_size,
            }, f)
        logger.info(f"Brain kaydedildi: {path}")

    @classmethod
    def load(cls, path: Path = MODEL_PATH, threshold: float = None) -> "BakkalBrain":
        with open(path, "rb") as f:
            data = pickle.load(f)
        b = cls(threshold=threshold or data["threshold"])
        b.model = data["model"]
        b.training_size = data["training_size"]
        logger.info(f"Brain yüklendi: {path} (n={b.training_size})")
        return b


def extract_features(row: pd.Series, direction: int, funding_rate: float) -> dict:
    """bakkal_signal.compute_indicators çıktısı row'undan feature çıkar."""
    import numpy as np
    close = float(row["close"])
    atr = float(row.get("atr", 0.0)) if np.isfinite(row.get("atr", 0.0)) else 0.0
    ema9 = float(row.get("ema9", close))
    ema21 = float(row.get("ema21", close))
    bb_up = float(row.get("bb_upper", close))
    bb_lo = float(row.get("bb_lower", close))
    bb_mid = float(row.get("bb_mid", close))
    bb_width = bb_up - bb_lo

    ts = row.name if hasattr(row.name, "hour") else pd.Timestamp.utcnow()

    return {
        "adx": float(row.get("adx", 0.0)) if np.isfinite(row.get("adx", 0.0)) else 0.0,
        "rsi": float(row.get("rsi", 50.0)) if np.isfinite(row.get("rsi", 50.0)) else 50.0,
        "ema_diff_pct": (ema9 - ema21) / close if close > 0 else 0.0,
        "vol_ratio": float(row.get("vol_ratio", 1.0)) if np.isfinite(row.get("vol_ratio", 1.0)) else 1.0,
        "bb_pos": (close - bb_mid) / bb_width if bb_width > 0 else 0.0,
        "atr_pct": atr / close if close > 0 else 0.0,
        "funding_rate": float(funding_rate),
        "hour": int(ts.hour),
        "dow": int(ts.dayofweek),
        "direction": int(direction),
    }


if __name__ == "__main__":
    # Cache'ten eğit ve kaydet
    brain = BakkalBrain(threshold=0.60)
    if brain.fit_from_cache():
        brain.save()
        print(f"✓ Brain kaydedildi → {MODEL_PATH}")
    else:
        print("✗ Eğitim başarısız")
