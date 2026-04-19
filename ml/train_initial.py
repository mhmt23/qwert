"""
Bakkal İlk Beyin — 180 günlük tüm adaylarla eğit, diske kaydet.

Çıktı:
  ml/out/model.pkl         — eğitilmiş HistGradientBoostingClassifier
  ml/out/training_set.parquet — beynin bildiği veri (online retrain için)
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import pandas as pd
from loguru import logger

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sklearn.ensemble import HistGradientBoostingClassifier

from ml.walk_forward_brain import (
    build_all_candidates, FEATURES, OUT_DIR,
)
from strategy import bakkal_signal


MODEL_PATH = OUT_DIR / "model.pkl"
TRAIN_PATH = OUT_DIR / "training_set.parquet"


def train():
    bakkal_signal.ENABLE_TREND_REGIME = True
    bakkal_signal.ENABLE_RANGE_REGIME = True

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Aday cache varsa onu kullan
    cands_path = OUT_DIR / "candidates.parquet"
    if cands_path.exists():
        logger.info("Aday cache bulundu, yükleniyor...")
        cands = pd.read_parquet(cands_path)
    else:
        logger.info("Adaylar sıfırdan üretiliyor...")
        cands = build_all_candidates()
        cands.to_parquet(cands_path)

    logger.info(f"Toplam aday: {len(cands)}")
    logger.info(f"Ham WIN oranı: %{cands['label'].mean()*100:.1f}")

    X = cands[FEATURES].values
    y = cands["label"].values

    model = HistGradientBoostingClassifier(
        max_iter=400, max_depth=6, learning_rate=0.05,
        min_samples_leaf=30, random_state=42,
    )
    model.fit(X, y)

    # Sanity: kendi eğitim setinde doğruluk
    probs = model.predict_proba(X)[:, 1]
    logger.info(f"Eğitim seti olasılık dağılımı: "
                f"p50={pd.Series(probs).quantile(0.5):.3f}  "
                f"p75={pd.Series(probs).quantile(0.75):.3f}  "
                f"p90={pd.Series(probs).quantile(0.9):.3f}")

    with open(MODEL_PATH, "wb") as f:
        pickle.dump({"model": model, "features": FEATURES}, f)
    logger.info(f"Model kaydedildi: {MODEL_PATH}")

    keep_cols = list(dict.fromkeys(FEATURES + ["label", "timestamp", "symbol", "bakkal_score"]))
    cands[keep_cols].to_parquet(TRAIN_PATH)
    logger.info(f"Eğitim seti kaydedildi: {TRAIN_PATH} ({len(cands)} örnek)")


if __name__ == "__main__":
    train()
