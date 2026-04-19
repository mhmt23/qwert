"""
ADİL TEST — Bakkal v3 kısıtlarıyla walk-forward brain.

Aynı ortam:
  min_score >= 4
  max 3 eşzamanlı pozisyon
  1 işlem/coin/gün
  Aynı TP/SL (ATR 1.5/1.0)

Fark:
  (A) Düz bakkal v3       — skor>=4 ise al
  (B) Brain + bakkal      — skor>=4 VE brain P(WIN)>=threshold ise al
  (C) Brain tek başına    — bakkal skoruna bakma, brain P(WIN)>=threshold ise al

Karşılaştırma: üçü aynı günleri görür, aynı kısıtlarla işler.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd
from loguru import logger

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sklearn.ensemble import HistGradientBoostingClassifier

from strategy import bakkal_signal


CACHE_DIR      = Path("backtest/cache")
OUT_DIR        = Path("ml/out_fair")
INTERVAL       = "1h"
DAYS           = 180
WARMUP_DAYS    = 14
MAX_OPEN       = 3
MAX_HOLD_BARS  = 24
LEVERAGE       = 5
FEE_ROUND_TRIP = 0.0008
INITIAL_BAL    = 100.0
MARGIN_BY_SCORE = {3: 1.0, 4: 2.0, 5: 3.0}
ML_THRESHOLD   = 0.55

FEATURES = [
    "adx", "rsi", "ema_diff_pct", "vol_ratio", "bb_pos",
    "atr_pct", "funding_rate", "hour", "dow", "direction",
]


def _load_candidates() -> pd.DataFrame:
    """ml/out/candidates.parquet — önceki run'dan kullan."""
    path = Path("ml/out/candidates.parquet")
    if not path.exists():
        raise SystemExit("Önce walk_forward_brain.py çalıştır (candidates.parquet gerek).")
    df = pd.read_parquet(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.sort_values("timestamp").reset_index(drop=True)
    df["date"] = df["timestamp"].dt.date
    df["hour_ts"] = df["timestamp"].dt.floor("h")
    return df


def _simulate(candidates: pd.DataFrame, strategy: str,
              model_by_day: Dict = None, threshold: float = 0.55,
              min_score: int = 4) -> Dict:
    """
    candidates: tüm aday sinyaller (her satır bir coin-saat-yön).
    strategy:
      "bakkal"       → skor >= min_score
      "brain_and"    → skor >= min_score VE prob >= threshold
      "brain_only"   → prob >= threshold (skor filtresi yok)

    Kısıtlar: max 3 eşzamanlı, 1/coin/gün. Pozisyonun bitiş zamanı biliniyor
    (exit_bar × 1 saat), o ana kadar yeni pozisyon sadece slot boşsa açılır.
    """
    # Seçim filtresi
    mask = pd.Series(True, index=candidates.index)
    if strategy == "bakkal":
        mask &= candidates["bakkal_score"] >= min_score
    elif strategy == "brain_and":
        mask &= candidates["bakkal_score"] >= min_score
        mask &= candidates["ml_prob"] >= threshold
    elif strategy == "brain_only":
        mask &= candidates["ml_prob"] >= threshold
    else:
        raise ValueError(strategy)

    cand = candidates[mask].copy().sort_values("timestamp")

    open_positions = []   # (close_time,) listesi
    traded_today = set()   # (date, symbol)
    taken_rows = []

    for _, r in cand.iterrows():
        now = r["hour_ts"]
        # Bitmiş pozisyonları temizle
        open_positions = [t for t in open_positions if t > now]

        if len(open_positions) >= MAX_OPEN:
            continue
        key = (r["date"], r["symbol"])
        if key in traded_today:
            continue

        close_time = now + pd.Timedelta(hours=int(r["exit_bar"]))
        open_positions.append(close_time)
        traded_today.add(key)
        taken_rows.append(r)

    taken = pd.DataFrame(taken_rows) if taken_rows else pd.DataFrame()
    if taken.empty:
        return {"trades": 0, "wins": 0, "pnl": 0.0, "wr": 0.0, "df": taken}

    # Margin-ölçekli PnL (score'a göre)
    taken = taken.copy()
    taken["margin"] = taken["bakkal_score"].map(MARGIN_BY_SCORE).fillna(1.0)
    # orijinal net_pnl MARGIN_USDT=2 varsayımıyla hesaplanmıştı — ölçekle
    taken["scaled_pnl"] = taken["net_pnl"] * (taken["margin"] / 2.0)

    return {
        "trades": int(len(taken)),
        "wins": int(taken["label"].sum()),
        "pnl": float(taken["scaled_pnl"].sum()),
        "wr": float(taken["label"].mean() * 100),
        "df": taken,
    }


def build_ml_probs(candidates: pd.DataFrame) -> pd.DataFrame:
    """
    Expanding window: her gün için, o güne kadarki tüm adaylarla eğitilmiş
    modelin o günün adaylarına verdiği P(WIN) olasılığını hesapla.
    """
    dates = sorted(candidates["date"].unique())
    probs_out = np.zeros(len(candidates))
    idx_arr = candidates.index.values

    training_mask = candidates["date"] < dates[WARMUP_DAYS]
    warmup_end_date = dates[WARMUP_DAYS]

    logger.info(f"ML eğitim başlıyor — warmup {WARMUP_DAYS} gün sonrası.")
    model = None
    training_set = candidates[training_mask]

    for day_idx, d in enumerate(dates):
        today_mask = candidates["date"] == d
        today_idx = candidates.index[today_mask]

        if day_idx >= WARMUP_DAYS and len(training_set) >= 100 \
                and training_set["label"].nunique() == 2:
            if model is None:
                model = HistGradientBoostingClassifier(
                    max_iter=300, max_depth=6, learning_rate=0.05,
                    min_samples_leaf=20, random_state=42,
                )
                model.fit(training_set[FEATURES].values,
                          training_set["label"].values)

            X = candidates.loc[today_idx, FEATURES].values
            probs_out[today_idx] = model.predict_proba(X)[:, 1]

            # Bugünü training'e ekle ve bir sonraki güne model güncelle
            training_set = pd.concat(
                [training_set, candidates.loc[today_idx]], ignore_index=False
            )
            # Modeli yeni veriyle yeniden fit et
            model = HistGradientBoostingClassifier(
                max_iter=300, max_depth=6, learning_rate=0.05,
                min_samples_leaf=20, random_state=42,
            )
            model.fit(training_set[FEATURES].values,
                      training_set["label"].values)
        else:
            # Warmup — 0 olasılık (seçilmez)
            probs_out[today_idx] = 0.0
            if day_idx < WARMUP_DAYS:
                training_set = pd.concat(
                    [training_set, candidates.loc[today_idx]], ignore_index=False
                )

        if day_idx % 20 == 0 or day_idx == len(dates) - 1:
            logger.info(f"Gün {day_idx+1}/{len(dates)} — train={len(training_set)}")

    candidates = candidates.copy()
    candidates["ml_prob"] = probs_out
    return candidates


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    bakkal_signal.ENABLE_TREND_REGIME = True
    bakkal_signal.ENABLE_RANGE_REGIME = True

    print("1) Adaylar yükleniyor (ml/out/candidates.parquet)...")
    candidates = _load_candidates()
    print(f"   {len(candidates)} aday, {candidates['symbol'].nunique()} coin, "
          f"{len(candidates['date'].unique())} gün")

    print("\n2) Expanding window ML olasılıkları üretiliyor...")
    candidates = build_ml_probs(candidates)
    candidates.to_parquet(OUT_DIR / "candidates_with_probs.parquet")

    print("\n3) Aynı kısıtlarla 3 strateji simüle ediliyor "
          "(max 3 eşzamanlı, 1/coin/gün)...")

    thresholds = [0.50, 0.53, 0.55, 0.58, 0.60, 0.62, 0.65]
    rows = []

    # Bakkal v3 baseline (min_score>=4, ML yok)
    res_bakkal = _simulate(candidates, "bakkal", min_score=4)
    rows.append({
        "strategy": "BAKKAL v3 (min_score>=4)", "threshold": "-",
        **{k: res_bakkal[k] for k in ["trades", "wins", "wr", "pnl"]},
    })

    # Bakkal + Brain AND (min_score>=4 VE prob>=th)
    for th in thresholds:
        r = _simulate(candidates, "brain_and", threshold=th, min_score=4)
        rows.append({
            "strategy": "BAKKAL + BRAIN", "threshold": f"{th:.2f}",
            **{k: r[k] for k in ["trades", "wins", "wr", "pnl"]},
        })

    # Brain only (skor filtresi yok, sadece ML)
    for th in thresholds:
        r = _simulate(candidates, "brain_only", threshold=th, min_score=3)
        rows.append({
            "strategy": "BRAIN TEK BAŞINA", "threshold": f"{th:.2f}",
            **{k: r[k] for k in ["trades", "wins", "wr", "pnl"]},
        })

    df = pd.DataFrame(rows)
    df["final_balance"] = INITIAL_BAL + df["pnl"]
    df.to_csv(OUT_DIR / "fair_comparison.csv", index=False)

    # Rapor
    print("\n" + "=" * 82)
    print("  ADİL KARŞILAŞTIRMA — Aynı kısıtlar (max 3 eşzamanlı, 1/coin/gün)")
    print("=" * 82)
    print(f"{'Strateji':<25} {'Eşik':>6} {'İşlem':>7} {'Win%':>7} "
          f"{'Net PnL':>10} {'Bakiye':>10}")
    print("-" * 82)

    current_group = None
    for _, r in df.iterrows():
        if current_group is not None and r["strategy"] != current_group:
            print("-" * 82)
        current_group = r["strategy"]
        print(
            f"{r['strategy']:<25} {str(r['threshold']):>6} {int(r['trades']):>7d} "
            f"{r['wr']:>6.1f}% {r['pnl']:>+9.2f} {r['final_balance']:>9.2f}"
        )

    best = df.loc[df["pnl"].idxmax()]
    print("=" * 82)
    print(f"\nEN İYİ: {best['strategy']} @ eşik {best['threshold']} → "
          f"${best['pnl']:+.2f} ({int(best['trades'])} işlem, "
          f"%{best['wr']:.1f} win)")
    print(f"Bakkal-sadece: ${res_bakkal['pnl']:+.2f}")
    print(f"Brain FARKI:   ${best['pnl'] - res_bakkal['pnl']:+.2f}")
    print()


if __name__ == "__main__":
    main()
