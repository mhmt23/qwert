"""
Bakkal Stratejisi — Multi-Coin Walk-Forward Backtest.

1. Binance'ten tüm aktif USDT-M perpetual koin listesini çek.
2. Filtreden geçen TOP_N koin için 1h verisini indir (cache).
3. Funding rate verisini indir (cache).
4. Bakkal motoruyla walk-forward simülasyon.
5. Toplu rapor üret.
"""
import asyncio
import sys
import time
from pathlib import Path

# Windows konsol için UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass
from typing import Dict, List
import pandas as pd
from loguru import logger

from data.binance_rest import BinanceRest
from backtest.data_loader import BacktestDataLoader
from backtest.bakkal_engine import BakkalBacktestEngine, BakkalParams, BakkalTrade
from backtest.report import BacktestReport
from backtest.engine import TradeRecord
from strategy.coin_universe import load_coin_universe
from config import CONFIG


# Backtest ayarları
TOP_N_COINS   = 30        # En yüksek hacimli 30 koin (veri indirme maliyeti için)
DAYS          = 180       # 180 gün geri
INTERVAL      = "1h"
N_SPLITS      = 4
TRAIN_RATIO   = 0.70
INITIAL_BAL   = 100.0


async def _load_one_coin(
    loader: BacktestDataLoader,
    symbol: str,
    engine: BakkalBacktestEngine,
) -> pd.DataFrame:
    """Tek bir koin için kline + funding yükle, göstergeleri hazırla."""
    cache_file = Path(f"backtest/cache/{symbol}_{INTERVAL}_{DAYS}d.parquet")
    if cache_file.exists():
        klines = pd.read_parquet(cache_file)
    else:
        klines = await loader.load_klines(symbol, interval=INTERVAL, days=DAYS)

    if len(klines) < 100:
        logger.warning(f"{symbol}: yetersiz veri ({len(klines)} mum)")
        return None

    # Funding rate (opsiyonel, yoksa 0)
    try:
        funding_df = await loader.load_funding_rates(symbol, days=DAYS)
        funding = funding_df["fundingRate"] if "fundingRate" in funding_df.columns else None
    except Exception as e:
        logger.debug(f"{symbol} funding alınamadı: {e}")
        funding = None

    return engine.prepare_coin_data(klines, funding)


def _to_report_trade(t: BakkalTrade) -> TradeRecord:
    """BakkalTrade → TradeRecord (rapor uyumlu)"""
    return TradeRecord(
        open_idx=0,
        close_idx=t.duration_candles,
        side=t.side,
        entry_price=t.entry_price,
        close_price=t.close_price,
        qty=t.qty,
        pnl_usd=t.pnl_usd,
        pnl_pct=t.pnl_pct,
        close_reason=t.close_reason,
        confluence_score=t.score,
        duration_candles=t.duration_candles,
    )


async def main():
    rest   = BinanceRest(CONFIG.BINANCE_API_KEY, CONFIG.BINANCE_API_SECRET, testnet=False)
    loader = BacktestDataLoader(rest)

    # 1) Koin evrenini yükle
    print("\n" + "=" * 60)
    print("  BAKKAL STRATEJİSİ — MULTI-COIN BACKTEST")
    print("=" * 60)
    universe = await load_coin_universe(rest)
    top_coins = universe[:TOP_N_COINS]
    print(f"\nTest edilecek koinler: {len(top_coins)}")
    for c in top_coins[:10]:
        print(f"  {c.symbol:<12} Hacim: ${c.volume_usd/1e6:>7.1f}M  24h: {c.price_change_pct:+.2f}%")
    if len(top_coins) > 10:
        print(f"  ... + {len(top_coins) - 10} tane daha")

    # 2) Motor + her koini paralel yükle
    engine = BakkalBacktestEngine(BakkalParams())

    print(f"\n{len(top_coins)} koin için veri yükleniyor...")
    coin_data: Dict[str, pd.DataFrame] = {}

    # Sıralı yükle (rate limit friendly)
    for i, c in enumerate(top_coins):
        try:
            df = await _load_one_coin(loader, c.symbol, engine)
            if df is not None:
                coin_data[c.symbol] = df
            print(f"  [{i+1}/{len(top_coins)}] {c.symbol}: {len(df) if df is not None else 0} mum")
        except Exception as e:
            logger.warning(f"{c.symbol} yüklenemedi: {e}")

    if not coin_data:
        print("Hiçbir koin yüklenemedi!")
        return

    # 3) Walk-forward backtest
    print(f"\n{'-' * 60}")
    print(f"Walk-forward başlıyor: {N_SPLITS} dilim, {len(coin_data)} koin")
    print(f"{'-' * 60}\n")

    result = engine.walk_forward(
        coin_data,
        initial_balance=INITIAL_BAL,
        n_splits=N_SPLITS,
        train_ratio=TRAIN_RATIO,
    )

    # 4) Rapor
    trades = result["trades"]
    print(f"\n{'=' * 60}")
    print("  TOPLU SONUÇ")
    print("=" * 60)

    if not trades:
        print("  Hiç işlem gerçekleşmedi.")
        await rest.close()
        return

    report = BacktestReport()
    report.generate(
        [_to_report_trade(t) for t in trades],
        result["equity_curve"],
        {"method": "bakkal_walk_forward", "coins": len(coin_data), "splits": N_SPLITS},
        initial_balance=INITIAL_BAL,
    )

    # Koin bazlı analiz
    print(f"\n{'-' * 60}")
    print("KOİN BAZLI PERFORMANS")
    print(f"{'-' * 60}")
    by_symbol: Dict[str, List[BakkalTrade]] = {}
    for t in trades:
        by_symbol.setdefault(t.symbol, []).append(t)
    rows = []
    for sym, tts in by_symbol.items():
        wins = sum(1 for t in tts if t.pnl_usd > 0)
        pnl = sum(t.pnl_usd for t in tts)
        rows.append((sym, len(tts), wins/len(tts)*100, pnl))
    rows.sort(key=lambda x: x[3], reverse=True)
    print(f"{'Symbol':<12} {'İşlem':>6} {'Win%':>7} {'PnL':>10}")
    for sym, n, wr, pnl in rows:
        print(f"{sym:<12} {n:>6} {wr:>6.1f}% {pnl:>+9.2f}")

    # Rejim analizi
    print(f"\n{'-' * 60}")
    print("REJİM BAZLI PERFORMANS")
    print(f"{'-' * 60}")
    trend_trades = [t for t in trades if t.regime == "TREND"]
    range_trades = [t for t in trades if t.regime == "RANGE"]
    for name, tts in [("TREND", trend_trades), ("RANGE", range_trades)]:
        if not tts:
            continue
        wins = sum(1 for t in tts if t.pnl_usd > 0)
        pnl = sum(t.pnl_usd for t in tts)
        print(f"{name:<8}: {len(tts):>4} işlem | Win: %{wins/len(tts)*100:.1f} | PnL: ${pnl:+.2f}")

    # Çıkış nedeni analizi
    print(f"\n{'-' * 60}")
    print("ÇIKIŞ NEDENİ ANALİZİ")
    print(f"{'-' * 60}")
    reasons: Dict[str, List[float]] = {}
    for t in trades:
        reasons.setdefault(t.close_reason, []).append(t.pnl_usd)
    for reason, pnls in reasons.items():
        avg_pnl = sum(pnls) / len(pnls)
        print(f"{reason:<12}: {len(pnls):>4} işlem | Ort. PnL: ${avg_pnl:+.3f}")

    await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
