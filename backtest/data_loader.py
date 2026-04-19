import asyncio
import time
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import yfinance as yf
from loguru import logger

from data.binance_rest import BinanceRest
from config import CONFIG


CACHE_DIR = Path("backtest/cache")


class BacktestDataLoader:
    def __init__(self, rest: BinanceRest):
        self.rest = rest
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    async def load_klines(
        self,
        symbol: str = "BTCUSDT",
        interval: str = "1m",
        days: int = 365,
    ) -> pd.DataFrame:
        cache_file = CACHE_DIR / f"{symbol}_{interval}_{days}d.parquet"

        if cache_file.exists():
            age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_hours < 24:
                logger.info(f"Cache'ten yükleniyor: {cache_file}")
                return pd.read_parquet(cache_file)

        logger.info(f"Binance'ten indiriliyor: {symbol} {interval} {days} gün...")
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - days * 86400 * 1000

        raw = await self.rest.get_historical_klines(symbol, interval, start_ms, end_ms)

        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore"
        ])
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
        df.set_index("open_time", inplace=True)

        for col in ["open", "high", "low", "close", "volume",
                    "taker_buy_base", "taker_buy_quote"]:
            df[col] = df[col].astype(float)

        # CVD yaklaşımı: taker buy - taker sell volume
        df["taker_sell_base"] = df["volume"] - df["taker_buy_base"]
        df["cvd_delta"] = df["taker_buy_base"] - df["taker_sell_base"]
        df["cvd"] = df["cvd_delta"].rolling(CONFIG.CVD_LOOKBACK).sum()

        df.to_parquet(cache_file)
        logger.info(f"Kaydedildi: {cache_file} ({len(df)} mum)")
        return df

    async def load_funding_rates(
        self, symbol: str = "BTCUSDT", days: int = 365
    ) -> pd.DataFrame:
        cache_file = CACHE_DIR / f"{symbol}_funding_{days}d.parquet"

        if cache_file.exists():
            age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_hours < 24:
                return pd.read_parquet(cache_file)

        end_ms = int(time.time() * 1000)
        start_ms = end_ms - days * 86400 * 1000
        raw = await self.rest.get_historical_funding_rates(symbol, start_ms, end_ms)

        if not raw or "fundingTime" not in raw[0]:
            logger.warning("Funding rate verisi alınamadı, sıfır kabul ediliyor.")
            return pd.DataFrame({"fundingRate": []}, index=pd.DatetimeIndex([]))

        df = pd.DataFrame(raw)
        df["fundingTime"] = pd.to_datetime(df["fundingTime"].astype(int), unit="ms")
        df["fundingRate"] = df["fundingRate"].astype(float)
        df.set_index("fundingTime", inplace=True)
        df.to_parquet(cache_file)
        return df

    def load_cross_asset(self, days: int = 365) -> pd.DataFrame:
        """Yahoo Finance'ten günlük cross-asset verisi."""
        cache_file = CACHE_DIR / f"cross_asset_{days}d.parquet"

        if cache_file.exists():
            age_hours = (time.time() - cache_file.stat().st_mtime) / 3600
            if age_hours < 24:
                return pd.read_parquet(cache_file)

        logger.info("Cross-asset verisi indiriliyor (Yahoo Finance)...")
        end = datetime.now()
        start = end - timedelta(days=days + 10)
        period_str = f"{start.strftime('%Y-%m-%d')}"

        tickers = {
            "NDX": "^NDX",
            "VIX": "^VIX",
            "DXY": "DX-Y.NYB",
            "GOLD": "GC=F",
        }

        frames = {}
        for name, ticker in tickers.items():
            try:
                df = yf.Ticker(ticker).history(period=f"{days+10}d", interval="1d")
                if not df.empty:
                    frames[name] = df["Close"].rename(name)
                    logger.info(f"  {name}: {len(df)} gün")
            except Exception as e:
                logger.warning(f"Yahoo Finance {ticker}: {e}")

        if not frames:
            logger.warning("Cross-asset verisi alınamadı, sabit değerler kullanılıyor.")
            idx = pd.date_range(end=datetime.now(), periods=days, freq="D")
            cross = pd.DataFrame({
                "NDX": 19000.0, "VIX": 20.0, "DXY": 104.0, "GOLD": 2000.0
            }, index=idx)
        else:
            cross = pd.concat(frames.values(), axis=1)
        cross.index = pd.to_datetime(cross.index)
        cross = cross.ffill()

        # NASDAQ EMA50
        cross["NDX_EMA50"] = cross["NDX"].ewm(span=50).mean()
        cross["NDX_ABOVE_EMA50"] = (cross["NDX"] > cross["NDX_EMA50"]).astype(int)
        cross["DXY_4H_CHG"] = cross["DXY"].pct_change(periods=1)  # günlük veri, 1 gün değişim

        cross.to_parquet(cache_file)
        logger.info(f"Cross-asset kaydedildi: {len(cross)} gün")
        return cross

    def merge_for_backtest(
        self, klines: pd.DataFrame, funding: pd.DataFrame, cross: pd.DataFrame
    ) -> pd.DataFrame:
        """Tüm veri kaynaklarını 1m kline'larla birleştir."""
        df = klines.copy()

        # Timezone'ları temizle, monotonic sırala
        df.index = pd.to_datetime(df.index).tz_localize(None)
        df = df[~df.index.duplicated()].sort_index()

        # Funding rate: 8 saatte bir → forward fill (boşsa sıfır)
        if not funding.empty and "fundingRate" in funding.columns:
            fr = funding["fundingRate"].copy()
            fr.index = pd.to_datetime(fr.index).tz_localize(None)
            fr = fr[~fr.index.duplicated()].sort_index()
            funding_reindexed = fr.reindex(df.index, method="ffill")
        else:
            funding_reindexed = pd.Series(0.0, index=df.index)
        df["funding_rate"] = funding_reindexed.fillna(0.0)

        # Cross-asset: günlük → forward fill
        cols = [c for c in ["NDX_ABOVE_EMA50", "VIX", "DXY_4H_CHG"] if c in cross.columns]
        cross_daily = cross[cols].copy()
        cross_daily.index = pd.to_datetime(cross_daily.index).tz_localize(None)
        cross_daily = cross_daily[~cross_daily.index.duplicated()].sort_index()
        cross_reindexed = cross_daily.reindex(df.index, method="ffill")
        df = pd.concat([df, cross_reindexed], axis=1)

        # Sadece temel sütunlardaki NaN'ları düşür
        df.dropna(subset=["open", "high", "low", "close", "volume"], inplace=True)
        df["funding_rate"] = df["funding_rate"].fillna(0.0)
        df["NDX_ABOVE_EMA50"] = df.get("NDX_ABOVE_EMA50", pd.Series(1, index=df.index)).fillna(1)
        df["VIX"] = df.get("VIX", pd.Series(20.0, index=df.index)).fillna(20.0)
        return df
