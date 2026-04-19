"""
Backtest çalıştırma scripti.

Walk-forward test kullanılır:
  - Veri N dilime bölünür
  - Her dilimde %70 train (parametre optimizasyonu)
  - Her dilimde %30 test (hiç görmediği veri)
  - Sadece out-of-sample (OOS) sonuçlar raporlanır
  - Gelecek veriyi görmeden, gerçekçi performans ölçümü
"""
import asyncio
from pathlib import Path
from loguru import logger

from data.binance_rest import BinanceRest
from backtest.data_loader import BacktestDataLoader
from backtest.engine import BacktestEngine, BacktestParams
from backtest.report import BacktestReport
from config import CONFIG


async def main():
    rest = BinanceRest(CONFIG.BINANCE_API_KEY, CONFIG.BINANCE_API_SECRET, testnet=False)
    loader = BacktestDataLoader(rest)

    logger.info("Veriler yükleniyor...")
    # 5m veri — cache'den yükle (1m'den resample edildi)
    import pandas as pd
    cache_1h = Path("backtest/cache/BTCUSDT_1h_180d.parquet")
    klines = pd.read_parquet(cache_1h)
    logger.info(f"1h cache yüklendi: {len(klines)} mum")

    funding = await loader.load_funding_rates(CONFIG.SYMBOL, days=180)
    cross = loader.load_cross_asset(days=180)

    logger.info(f"Kline: {len(klines)} mum | Funding: {len(funding)} | Cross: {len(cross)} gün")

    df = loader.merge_for_backtest(klines, funding, cross)
    logger.info(f"Birleştirilmiş veri: {len(df)} mum ({len(df)/24:.1f} gün)")

    engine = BacktestEngine()
    report = BacktestReport()

    print("\n" + "=" * 60)
    print("WALK-FORWARD TEST")
    print("Gelecek veriyi görmeden, out-of-sample performans")
    print("=" * 60)

    # Walk-forward: 5 dilim, her dilimde %70 train / %30 test
    result = engine.walk_forward(
        df,
        initial_balance=100.0,
        n_splits=5,
        train_ratio=0.70,
    )

    report.generate(
        result["trades"],
        result["equity_curve"],
        {"method": "walk_forward", "n_splits": 5},
        initial_balance=100.0,
    )

    print("\nNOT: Bu sonuçlar yalnızca out-of-sample (OOS) veriye dayanır.")
    print("Strateji hiç görmediği verinde bu performansı gösterdi.")

    await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
