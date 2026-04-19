"""
Trap Strategy — Multi-Coin Walk-Forward Backtest.

Her koin için:
  1. 1h verisi yükle (cache'den veya Binance'ten indir)
  2. Funding rate verisiyle birleştir
  3. Trap Engine ile walk-forward test (5 dilim)
  4. Raporu ekrana bas

Toplam rapor: tüm koinlerin OOS işlemleri birleştirilir.
"""
import asyncio
from pathlib import Path
from typing import List, Dict, Any
import pandas as pd
from loguru import logger

from data.binance_rest import BinanceRest
from backtest.data_loader import BacktestDataLoader
from backtest.trap_engine import TrapBacktestEngine, TrapTradeRecord
from backtest.report import BacktestReport
from config import CONFIG

COINS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "AVAXUSDT", "LINKUSDT", "DOGEUSDT", "DOTUSDT", "ADAUSDT",
]

DAYS   = 180
SPLITS = 5


def _to_report_trade(t: TrapTradeRecord):
    """TrapTradeRecord'u BacktestReport'un beklediği formata dönüştür."""
    from backtest.engine import TradeRecord
    return TradeRecord(
        open_idx=t.open_idx,
        close_idx=t.close_idx,
        side=t.side,
        entry_price=t.entry_price,
        close_price=t.close_price,
        qty=t.qty,
        pnl_usd=t.pnl_usd,
        pnl_pct=t.pnl_pct,
        close_reason=t.close_reason,
        confluence_score=t.trap_score,
        duration_candles=t.duration_candles,
    )


async def main():
    rest   = BinanceRest(CONFIG.BINANCE_API_KEY, CONFIG.BINANCE_API_SECRET, testnet=False)
    loader = BacktestDataLoader(rest)

    all_trades: List[TrapTradeRecord] = []
    all_equity: List[float] = [100.0]
    running_balance = 100.0

    for symbol in COINS:
        print(f"\n{'='*55}")
        print(f"  {symbol}")
        print(f"{'='*55}")

        # 1h cache yükle
        cache_1h = Path(f"backtest/cache/{symbol}_1h_{DAYS}d.parquet")
        if cache_1h.exists():
            klines = pd.read_parquet(cache_1h)
            logger.info(f"{symbol}: cache'ten {len(klines)} mum")
        else:
            logger.info(f"{symbol}: Binance'ten indiriliyor...")
            klines = await loader.load_klines(symbol, interval="1h", days=DAYS)
            klines.to_parquet(cache_1h)

        if len(klines) < 200:
            logger.warning(f"{symbol}: Yeterli veri yok, atlanıyor.")
            continue

        funding = await loader.load_funding_rates(symbol, days=DAYS)
        cross   = loader.load_cross_asset(days=DAYS)
        df      = loader.merge_for_backtest(klines, funding, cross)

        logger.info(f"{symbol}: {len(df)} mum hazır")

        engine = TrapBacktestEngine()
        result = engine.walk_forward(
            df,
            symbol=symbol,
            initial_balance=running_balance,
            n_splits=SPLITS,
            train_ratio=0.70,
        )

        coin_trades = result["trades"]
        all_trades.extend(coin_trades)
        running_balance = result["final_balance"]
        all_equity.extend(result["equity_curve"][1:])

        if coin_trades:
            wins = sum(1 for t in coin_trades if t.pnl_usd > 0)
            pnl  = sum(t.pnl_usd for t in coin_trades)
            print(f"  İşlem: {len(coin_trades)} | Win: %{wins/len(coin_trades)*100:.1f} | PnL: ${pnl:.2f}")
        else:
            print(f"  İşlem yok.")

    # --- Genel Rapor ---
    print(f"\n{'='*55}")
    print(f"  TRAP STRATEJİ — TOPLAM OOS SONUÇ")
    print(f"{'='*55}")

    if all_trades:
        report = BacktestReport()
        converted = [_to_report_trade(t) for t in all_trades]
        report.generate(
            converted,
            all_equity,
            {"method": "trap_walk_forward", "coins": len(COINS), "splits": SPLITS},
            initial_balance=100.0,
        )
    else:
        print("  Hiç işlem gerçekleşmedi.")

    await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
