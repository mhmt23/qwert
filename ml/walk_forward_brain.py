"""
Bakkal Yapay Beyin — Gün-Gün Expanding Window Walk-Forward.

Kullanıcı mantığı:
  Gün 1  → model yok, sadece gözle (işlem yok)
  Gün N  → model gün 1..N-1 ile eğitilmiş, bugünü tahmin et
  Gün sonu → gün N'in gerçek sonuçlarını dataset'e ekle, modeli yenile

Avantaj: lookahead yok, canlı ortamı birebir simüle eder.

Çıktılar:
  ml/out/daily_report.csv      — her gün PnL, bakiye, model kararları
  ml/out/trades.csv            — her ML-onaylı işlem
  ml/out/summary.txt           — final özet
  ml/out/feature_importance.png (opsiyonel — matplotlib varsa)
"""
from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from loguru import logger

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sklearn.ensemble import HistGradientBoostingClassifier

from strategy import bakkal_signal
from strategy.bakkal_signal import compute_indicators, evaluate_row


# --- Parametreler ---
CACHE_DIR      = Path("backtest/cache")
OUT_DIR        = Path("ml/out")
INTERVAL       = "1h"
DAYS           = 180
WARMUP_DAYS    = 14                 # İlk 14 gün sadece gözlem
MIN_BAKKAL_SCORE = 3                # 3+ puanlı sinyalleri aday olarak al
ML_THRESHOLDS  = [0.50, 0.53, 0.55, 0.58, 0.60, 0.62, 0.65]  # Süpürme
MAX_HOLD_BARS  = 24                 # ATR TP/SL'e çarpmazsa çıkış
LEVERAGE       = 5
MARGIN_USDT    = 2.0                # Her işlem için margin
FEE_ROUND_TRIP = 0.0008             # %0.08 gidiş-dönüş (taker)
INITIAL_BAL    = 100.0

FEATURES = [
    "adx", "rsi", "ema_diff_pct", "vol_ratio", "bb_pos",
    "atr_pct", "funding_rate", "hour", "dow", "direction",
]


# --- Veri hazırlığı ---
def _list_cached_coins() -> List[str]:
    files = sorted(CACHE_DIR.glob(f"*_{INTERVAL}_{DAYS}d.parquet"))
    return [f.stem.replace(f"_{INTERVAL}_{DAYS}d", "") for f in files]


def _load_coin(symbol: str) -> Optional[pd.DataFrame]:
    kpath = CACHE_DIR / f"{symbol}_{INTERVAL}_{DAYS}d.parquet"
    fpath = CACHE_DIR / f"{symbol}_funding_{DAYS}d.parquet"
    if not kpath.exists():
        return None
    df = pd.read_parquet(kpath)
    if len(df) < 100:
        return None

    # Index sanitize
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df[~df.index.duplicated()].sort_index()

    df = compute_indicators(df)

    # Funding
    if fpath.exists():
        try:
            fdf = pd.read_parquet(fpath)
            if "fundingRate" in fdf.columns:
                s = fdf["fundingRate"]
                s.index = pd.to_datetime(s.index)
                if s.index.tz is not None:
                    s.index = s.index.tz_localize(None)
                s = s[~s.index.duplicated()].sort_index()
                df["funding_rate"] = s.reindex(df.index, method="ffill").fillna(0.0)
            else:
                df["funding_rate"] = 0.0
        except Exception:
            df["funding_rate"] = 0.0
    else:
        df["funding_rate"] = 0.0

    df["symbol"] = symbol
    return df


def _build_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Bakkal sinyali ≥ MIN_BAKKAL_SCORE olan tüm satırları aday olarak üret.
    Her aday: features + direction + entry_price + tp + sl + future outcome.
    """
    rows = []
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    atr = df["atr"].values
    adx = df["adx"].values
    rsi = df["rsi"].values
    ema9 = df["ema9"].values
    ema21 = df["ema21"].values
    vol_ratio = df["vol_ratio"].values
    bb_up = df["bb_upper"].values
    bb_lo = df["bb_lower"].values
    bb_mid = df["bb_mid"].values
    funding = df["funding_rate"].values
    idx = df.index

    n = len(df)
    for i in range(n - 1):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        if not np.isfinite(adx[i]):
            continue

        sig = evaluate_row(df.iloc[i], funding_rate=float(funding[i]))
        if sig.direction == 0 or sig.score < MIN_BAKKAL_SCORE:
            continue

        entry = float(close[i])
        direction = sig.direction
        if direction == 1:
            tp = entry + 1.5 * a
            sl = entry - 1.0 * a
        else:
            tp = entry - 1.5 * a
            sl = entry + 1.0 * a

        # Forward simulation — TP mi SL mi timeout mu?
        outcome = None
        exit_price = entry
        exit_bar = 0
        end = min(n, i + 1 + MAX_HOLD_BARS)
        for j in range(i + 1, end):
            hi = high[j]; lo = low[j]
            if direction == 1:
                if lo <= sl:
                    outcome = "SL"; exit_price = sl; exit_bar = j - i; break
                if hi >= tp:
                    outcome = "TP"; exit_price = tp; exit_bar = j - i; break
            else:
                if hi >= sl:
                    outcome = "SL"; exit_price = sl; exit_bar = j - i; break
                if lo <= tp:
                    outcome = "TP"; exit_price = tp; exit_bar = j - i; break
        if outcome is None:
            outcome = "TIMEOUT"
            exit_price = float(close[end - 1]) if end - 1 < n else entry
            exit_bar = end - 1 - i

        # PnL hesapla
        qty = (MARGIN_USDT * LEVERAGE) / entry
        if direction == 1:
            gross = (exit_price - entry) * qty
        else:
            gross = (entry - exit_price) * qty
        fee = (entry + exit_price) * qty * (FEE_ROUND_TRIP / 2)
        net = gross - fee

        label = 1 if net > 0 else 0

        # Features
        bb_width = bb_up[i] - bb_lo[i]
        bb_pos_val = (entry - bb_mid[i]) / bb_width if bb_width > 0 else 0.0
        ema_diff_pct = (ema9[i] - ema21[i]) / entry if entry > 0 else 0.0
        atr_pct = a / entry if entry > 0 else 0.0

        rows.append({
            "timestamp": idx[i],
            "symbol": df["symbol"].iloc[i] if "symbol" in df.columns else "",
            "direction": direction,
            "bakkal_score": sig.score,
            "regime": sig.regime,
            "entry_price": entry,
            "tp_price": tp,
            "sl_price": sl,
            "exit_price": exit_price,
            "exit_bar": exit_bar,
            "outcome": outcome,
            "net_pnl": net,
            "label": label,
            # features
            "adx": float(adx[i]),
            "rsi": float(rsi[i]) if np.isfinite(rsi[i]) else 50.0,
            "ema_diff_pct": float(ema_diff_pct),
            "vol_ratio": float(vol_ratio[i]) if np.isfinite(vol_ratio[i]) else 1.0,
            "bb_pos": float(bb_pos_val),
            "atr_pct": float(atr_pct),
            "funding_rate": float(funding[i]),
            "hour": int(idx[i].hour),
            "dow": int(idx[i].dayofweek),
        })
    return pd.DataFrame(rows)


def build_all_candidates() -> pd.DataFrame:
    coins = _list_cached_coins()
    logger.info(f"{len(coins)} cache'li koin bulundu.")
    all_rows = []
    for i, sym in enumerate(coins):
        df = _load_coin(sym)
        if df is None:
            continue
        cands = _build_candidates(df)
        if len(cands) > 0:
            all_rows.append(cands)
        logger.info(f"[{i+1}/{len(coins)}] {sym}: {len(cands) if len(cands)>0 else 0} aday")
    if not all_rows:
        return pd.DataFrame()
    out = pd.concat(all_rows, ignore_index=True)
    out = out.sort_values("timestamp").reset_index(drop=True)
    out["date"] = out["timestamp"].dt.date
    return out


# --- Walk-forward motor ---
def walk_forward(candidates: pd.DataFrame) -> Dict:
    """
    Her gün modeli yeniden eğit, tüm eşikler için kararları kaydet.
    Sonuç: eşik başına net PnL tablosu.
    """
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Bakkal baseline: min_score=4 ile açılanlarda ne olurdu?
    baseline_mask = candidates["bakkal_score"] >= 4
    baseline_pnl = candidates.loc[baseline_mask, "net_pnl"].sum()
    baseline_trades = int(baseline_mask.sum())
    baseline_wr = candidates.loc[baseline_mask, "label"].mean() * 100 \
        if baseline_trades > 0 else 0.0

    dates = sorted(candidates["date"].unique())
    logger.info(f"Toplam gün: {len(dates)}, toplam aday: {len(candidates)}")

    # Her eşik için ayrı sayaç
    results = {th: {"trades": 0, "wins": 0, "pnl": 0.0} for th in ML_THRESHOLDS}
    all_probs = []   # her aday için olasılık ve gerçek sonuç — son raporda lazım

    model = None
    training_set = pd.DataFrame()
    daily_rows = []

    for day_idx, d in enumerate(dates):
        today = candidates[candidates["date"] == d]
        phase = "WARMUP" if (day_idx < WARMUP_DAYS or model is None) else "TRADE"

        if phase == "TRADE" and len(today) > 0:
            X = today[FEATURES].values
            probs = model.predict_proba(X)[:, 1]

            # Gün bazlı log için en düşük eşik (0.50) kullan
            for th in ML_THRESHOLDS:
                m = probs >= th
                results[th]["trades"] += int(m.sum())
                results[th]["wins"] += int(today.loc[m, "label"].sum())
                results[th]["pnl"] += float(today.loc[m, "net_pnl"].sum())

            # Her aday için olasılığı kaydet (overall analiz)
            for prob, (_, row) in zip(probs, today.iterrows()):
                all_probs.append({
                    "date": d, "symbol": row["symbol"], "prob": prob,
                    "label": row["label"], "net_pnl": row["net_pnl"],
                    "bakkal_score": row["bakkal_score"],
                })

            day_prob_mean = float(probs.mean())
            day_prob_max = float(probs.max())
        else:
            day_prob_mean = day_prob_max = 0.0

        # Dataset'e bugünü ekle ve modeli yenile
        training_set = pd.concat([training_set, today], ignore_index=True)
        if len(training_set) >= 100 and training_set["label"].nunique() == 2:
            X_train = training_set[FEATURES].values
            y_train = training_set["label"].values
            model = HistGradientBoostingClassifier(
                max_iter=300, max_depth=6, learning_rate=0.05,
                min_samples_leaf=20, random_state=42,
            )
            model.fit(X_train, y_train)

        daily_rows.append({
            "day_idx": day_idx, "date": d, "phase": phase,
            "candidates": len(today),
            "prob_mean": day_prob_mean, "prob_max": day_prob_max,
            "training_size": len(training_set),
        })

        if day_idx % 15 == 0 or day_idx == len(dates) - 1:
            logger.info(
                f"Gün {day_idx+1:3d}/{len(dates)} [{phase:6s}] "
                f"aday={len(today):3d} train={len(training_set):5d} "
                f"P(mean)={day_prob_mean:.3f} P(max)={day_prob_max:.3f}"
            )

    # Günlük rapor
    daily_df = pd.DataFrame(daily_rows)
    daily_df.to_csv(OUT_DIR / "daily_report.csv", index=False)

    # Olasılık dağılımı
    probs_df = pd.DataFrame(all_probs)
    if len(probs_df) > 0:
        probs_df.to_parquet(OUT_DIR / "all_predictions.parquet")

    # Eşik tablosu
    rows = []
    for th in ML_THRESHOLDS:
        r = results[th]
        wr = (r["wins"] / r["trades"] * 100) if r["trades"] > 0 else 0.0
        final = INITIAL_BAL + r["pnl"]
        rows.append({
            "threshold": th, "trades": r["trades"], "wins": r["wins"],
            "win_rate": wr, "net_pnl": r["pnl"], "final_balance": final,
        })
    thr_df = pd.DataFrame(rows)
    thr_df.to_csv(OUT_DIR / "threshold_sweep.csv", index=False)

    best = thr_df.loc[thr_df["net_pnl"].idxmax()]

    # Olasılık dağılım istatistiği
    if len(probs_df) > 0:
        q = probs_df["prob"].quantile([0.25, 0.5, 0.75, 0.9, 0.95, 0.99])
        prob_summary = (
            f"Olasılık dağılımı (tüm aday tahminleri, n={len(probs_df)}):\n"
            f"  medyan={q[0.5]:.3f}  p75={q[0.75]:.3f}  p90={q[0.9]:.3f}  "
            f"p95={q[0.95]:.3f}  p99={q[0.99]:.3f}\n"
            f"  Gerçek WIN oranı (ham): %{probs_df['label'].mean()*100:.1f}\n"
        )
    else:
        prob_summary = ""

    # Özet
    lines = [
        "=" * 78,
        "  BAKKAL YAPAY BEYİN — WALK-FORWARD (EŞİK SÜPÜRMESİ)",
        "=" * 78,
        f"Gün sayısı: {len(dates)}  Warmup: {WARMUP_DAYS}  Aday: {len(candidates)}",
        "",
        prob_summary,
        f"{'Eşik':>6} {'İşlem':>8} {'Kazanan':>8} {'Win%':>7} {'Net PnL':>10} {'Bakiye':>10}",
        "-" * 60,
    ]
    for _, r in thr_df.iterrows():
        lines.append(
            f"{r['threshold']:>6.2f} {int(r['trades']):>8d} {int(r['wins']):>8d} "
            f"{r['win_rate']:>6.1f}% {r['net_pnl']:>+9.2f} {r['final_balance']:>9.2f}"
        )
    lines += [
        "",
        f"EN İYİ EŞİK: {best['threshold']:.2f}  →  {int(best['trades'])} işlem, "
        f"%{best['win_rate']:.1f} win, ${best['net_pnl']:+.2f}",
        "",
        f"BAKKAL BASELINE (min_score>=4): {baseline_trades} işlem, "
        f"%{baseline_wr:.1f} win, ${baseline_pnl:+.2f}",
        f"ML en iyi - Baseline: ${best['net_pnl'] - baseline_pnl:+.2f}",
        "=" * 78,
    ]
    summary = "\n".join(lines)
    print("\n" + summary)
    (OUT_DIR / "summary.txt").write_text(summary, encoding="utf-8")
    logger.info(f"Çıktılar: {OUT_DIR.resolve()}")

    return {
        "best_threshold": float(best["threshold"]),
        "best_pnl": float(best["net_pnl"]),
        "baseline_pnl": float(baseline_pnl),
    }


def main():
    print("1) Aday sinyalleri üretiliyor (geçmiş simülasyon)...")
    candidates = build_all_candidates()
    if candidates.empty:
        print("Hiç aday üretilemedi — cache kontrol et.")
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    candidates.to_parquet(OUT_DIR / "candidates.parquet")
    print(f"Toplam aday: {len(candidates)} "
          f"(WIN: {candidates['label'].sum()}, "
          f"kazanç oranı ham: %{candidates['label'].mean()*100:.1f})")

    print("\n2) Expanding window walk-forward başlıyor...")
    walk_forward(candidates)


if __name__ == "__main__":
    # Bakkal bayrakları v3 ile aynı
    bakkal_signal.ENABLE_TREND_REGIME = True
    bakkal_signal.ENABLE_RANGE_REGIME = True
    main()
