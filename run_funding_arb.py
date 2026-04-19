"""
Funding Arbitraj — Cash-and-Carry Simülasyonu.

Mantık:
  Spot long + Perp short → delta-nötr pozisyon.
  Yön riski yok, tek gelir funding ödemeleri (perp short ödeyeni alır).
  Positive funding = long'lar short'lara para öder = bizim karımız.

Cache'teki 180 günlük fundingRate verisiyle hesaplar:
  - Her ödeme 8 saatte bir
  - Toplam funding geliri (%)
  - Yıllık getiri (%)
  - Fee sonrası net

Yıllık >%15 → kurulur. %5-15 → marjinal. <%5 → vakit kaybı.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from loguru import logger

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


CACHE_DIR = Path("backtest/cache")
DAYS = 180
# Cash-and-carry maliyetler (yaklaşık):
# - Spot taker buy: %0.1
# - Perp taker short: %0.05
# - Kapanışta her ikisi: %0.15
# - Yuvarla: %0.3 round-trip
ROUND_TRIP_FEE = 0.003


def analyze_funding():
    files = sorted(CACHE_DIR.glob(f"*_funding_{DAYS}d.parquet"))
    logger.info(f"{len(files)} funding dosyası bulundu.")

    rows = []
    for f in files:
        sym = f.stem.replace(f"_funding_{DAYS}d", "")
        try:
            df = pd.read_parquet(f)
        except Exception as e:
            logger.debug(f"{sym}: {e}")
            continue

        if "fundingRate" not in df.columns or len(df) == 0:
            continue

        rates = df["fundingRate"].astype(float)
        n_payments = len(rates)
        if n_payments < 50:   # 180 günde ~540 ödeme beklenir
            continue

        # Toplam funding geliri: 1 birim nominal başına
        total_yield = rates.sum()               # %, ondalık (0.0001 = %0.01)
        mean_rate = rates.mean()
        pct_positive = (rates > 0).mean() * 100

        # Yıllıklandır
        days_covered = (df.index.max() - df.index.min()).total_seconds() / 86400.0
        if days_covered < 30:
            continue
        annualized = total_yield * (365.0 / days_covered) * 100   # %

        # Fee sonrası — varsayım: pozisyonu 180 gün tuttuk, 1 round-trip
        net_annual = annualized - (ROUND_TRIP_FEE * 100) * (365.0 / days_covered)

        rows.append({
            "symbol": sym,
            "payments": n_payments,
            "days": round(days_covered, 1),
            "mean_funding_pct": mean_rate * 100,          # % per 8h
            "total_yield_pct": total_yield * 100,          # % over period
            "annualized_pct": annualized,                  # % / yıl (gross)
            "net_annual_pct": net_annual,                  # % / yıl (fee sonrası)
            "pct_positive": pct_positive,
        })

    if not rows:
        print("Veri yok.")
        return

    res = pd.DataFrame(rows).sort_values("annualized_pct", ascending=False)

    print("\n" + "=" * 95)
    print("  FUNDING ARBITRAJ — CASH-AND-CARRY SİMÜLASYONU (180 gün)")
    print("=" * 95)
    print(f"{'Sembol':<18}{'Ödeme':>7}{'Gün':>6}"
          f"{'Mean8h%':>10}{'Top%':>9}{'Yıllık%':>10}{'Net/yıl%':>10}{'+Pay%':>8}")
    print("-" * 95)
    for _, r in res.head(25).iterrows():
        print(
            f"{r['symbol']:<18}{int(r['payments']):>7}{r['days']:>6.0f}"
            f"{r['mean_funding_pct']:>+9.4f}{r['total_yield_pct']:>+8.2f}"
            f"{r['annualized_pct']:>+9.2f}{r['net_annual_pct']:>+9.2f}"
            f"{r['pct_positive']:>7.1f}"
        )

    print("\n" + "-" * 95)
    # Özet — portföy senaryosu
    top5 = res.head(5)
    print(f"\nTOP 5 ORTALAMA: yıllık %{top5['annualized_pct'].mean():.2f} "
          f"(net %{top5['net_annual_pct'].mean():.2f})")
    top10 = res.head(10)
    print(f"TOP 10 ORTALAMA: yıllık %{top10['annualized_pct'].mean():.2f} "
          f"(net %{top10['net_annual_pct'].mean():.2f})")

    # Karar
    best_net = res["net_annual_pct"].max()
    best_sym = res.loc[res["net_annual_pct"].idxmax(), "symbol"]
    print("\n" + "=" * 95)
    print("  KARAR")
    print("=" * 95)
    print(f"En iyi tek coin: {best_sym} → yıllık net %{best_net:.2f}")
    if best_net >= 15:
        verdict = "✓ CİDDİ EDGE — kurulmaya değer"
    elif best_net >= 5:
        verdict = "~ MARJİNAL — risk/efor dengesi tartışmalı"
    else:
        verdict = "✗ EDGE YOK — vakit kaybı"
    print(f"Sonuç: {verdict}")
    print("=" * 95)

    out = Path("ml/out/funding_arb_report.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    res.to_csv(out, index=False)
    print(f"\nRapor: {out.resolve()}")


if __name__ == "__main__":
    analyze_funding()
