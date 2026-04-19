"""
Bakkal Stratejisi — Versiyon Karşılaştırma.

3 versiyonu aynı koin setinde çalıştırır, sonuçları yan yana gösterir:
  v1: RANGE-only,    min_score=3
  v2: RANGE-only,    min_score=4
  v3: TREND + RANGE, min_score=4
"""
import asyncio
import sys
from pathlib import Path
from typing import Dict
import pandas as pd
from loguru import logger

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from data.binance_rest import BinanceRest
from backtest.data_loader import BacktestDataLoader
from backtest.bakkal_engine import BakkalBacktestEngine, BakkalParams
from strategy import bakkal_signal
from strategy.coin_universe import load_coin_universe
from config import CONFIG


TOP_N = 30
DAYS = 180
INTERVAL = "1h"
N_SPLITS = 4


async def _load_coin(loader, symbol, engine):
    cache = Path(f"backtest/cache/{symbol}_{INTERVAL}_{DAYS}d.parquet")
    if cache.exists():
        klines = pd.read_parquet(cache)
    else:
        klines = await loader.load_klines(symbol, interval=INTERVAL, days=DAYS)
    if len(klines) < 100:
        return None
    try:
        fdf = await loader.load_funding_rates(symbol, days=DAYS)
        funding = fdf["fundingRate"] if "fundingRate" in fdf.columns else None
    except Exception:
        funding = None
    return engine.prepare_coin_data(klines, funding)


def _run_version(name: str, coin_data: Dict, params: BakkalParams,
                 enable_trend: bool, enable_range: bool) -> Dict:
    bakkal_signal.ENABLE_TREND_REGIME = enable_trend
    bakkal_signal.ENABLE_RANGE_REGIME = enable_range

    engine = BakkalBacktestEngine(params)
    result = engine.walk_forward(
        coin_data,
        initial_balance=100.0,
        n_splits=N_SPLITS,
        train_ratio=0.70,
    )
    trades = result["trades"]
    wins = sum(1 for t in trades if t.pnl_usd > 0)
    losses = len(trades) - wins
    pnl = sum(t.pnl_usd for t in trades)
    gross_profit = sum(t.pnl_usd for t in trades if t.pnl_usd > 0)
    gross_loss = abs(sum(t.pnl_usd for t in trades if t.pnl_usd < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    return {
        "name": name,
        "trades": len(trades),
        "win_rate": wins / max(len(trades), 1) * 100,
        "wins": wins,
        "losses": losses,
        "net_pnl": pnl,
        "profit_factor": pf,
        "final_balance": result["final_balance"],
        "avg_tp": sum(t.pnl_usd for t in trades if t.pnl_usd > 0) / max(wins, 1),
        "avg_sl": sum(t.pnl_usd for t in trades if t.pnl_usd < 0) / max(losses, 1),
    }


async def main():
    rest = BinanceRest(CONFIG.BINANCE_API_KEY, CONFIG.BINANCE_API_SECRET, testnet=False)
    loader = BacktestDataLoader(rest)

    print("Koin evreni yükleniyor...")
    universe = await load_coin_universe(rest)
    top_coins = universe[:TOP_N]

    engine_tmp = BakkalBacktestEngine(BakkalParams())
    coin_data = {}
    for i, c in enumerate(top_coins):
        try:
            df = await _load_coin(loader, c.symbol, engine_tmp)
            if df is not None:
                coin_data[c.symbol] = df
        except Exception as e:
            logger.debug(f"{c.symbol}: {e}")

    print(f"{len(coin_data)} koin hazır.\n")

    versions = []

    print("v1: RANGE-only, min_score=3 çalışıyor...")
    versions.append(_run_version(
        "v1_range_s3", coin_data, BakkalParams(min_score=3),
        enable_trend=False, enable_range=True,
    ))

    print("v2: RANGE-only, min_score=4 çalışıyor...")
    versions.append(_run_version(
        "v2_range_s4", coin_data, BakkalParams(min_score=4),
        enable_trend=False, enable_range=True,
    ))

    print("v3: TREND+RANGE, min_score=4 çalışıyor...")
    versions.append(_run_version(
        "v3_both_s4", coin_data, BakkalParams(min_score=4),
        enable_trend=True, enable_range=True,
    ))

    # Rapor
    print("\n" + "=" * 72)
    print("  VERSİYON KARŞILAŞTIRMA (OOS, 180 gün, 4-fold walk-forward)")
    print("=" * 72)
    print(f"{'Versiyon':<15}{'İşlem':>8}{'Win%':>8}{'PnL':>10}{'PF':>8}"
          f"{'AvgTP':>9}{'AvgSL':>9}{'Bakiye':>10}")
    print("-" * 72)
    for v in versions:
        print(
            f"{v['name']:<15}"
            f"{v['trades']:>8}"
            f"{v['win_rate']:>7.1f}%"
            f"{v['net_pnl']:>+9.2f}"
            f"{v['profit_factor']:>8.2f}"
            f"{v['avg_tp']:>+8.3f}"
            f"{v['avg_sl']:>+8.3f}"
            f"{v['final_balance']:>10.2f}"
        )

    best = max(versions, key=lambda v: v["net_pnl"])
    print("\n" + "=" * 72)
    print(f"  EN İYİ: {best['name']}  →  PnL: ${best['net_pnl']:+.2f}  "
          f"Win: %{best['win_rate']:.1f}  İşlem: {best['trades']}")
    print("=" * 72)

    await rest.close()


if __name__ == "__main__":
    asyncio.run(main())
