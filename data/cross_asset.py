import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional, List
import yfinance as yf
from loguru import logger


@dataclass
class CrossAssetData:
    nasdaq: Optional[float] = None
    nasdaq_ema50: Optional[float] = None
    dxy: Optional[float] = None
    dxy_4h_change_pct: Optional[float] = None
    vix: Optional[float] = None
    gold: Optional[float] = None
    timestamp: int = field(default_factory=lambda: int(time.time()))

    def is_valid(self) -> bool:
        return all(v is not None for v in [self.nasdaq, self.dxy, self.vix])


class CrossAssetFeed:
    TICKERS = {
        "nasdaq": "^NDX",
        "dxy": "DX-Y.NYB",
        "vix": "^VIX",
        "gold": "GC=F",
    }

    def __init__(self, api_key: str = "", update_interval: int = 300):
        self.update_interval = update_interval
        self._data = CrossAssetData()
        self._dxy_history: List[float] = []
        self._nasdaq_closes: List[float] = []
        self._running = False

    def _fetch_price(self, ticker: str) -> Optional[float]:
        try:
            return yf.Ticker(ticker).fast_info.last_price
        except Exception as e:
            logger.warning(f"yfinance '{ticker}' alınamadı: {e}")
            return None

    def _fetch_nasdaq_ema50(self) -> Optional[float]:
        try:
            df = yf.Ticker("^NDX").history(period="60d", interval="1d")
            if df.empty:
                return None
            closes = df["Close"]
            ema50 = closes.ewm(span=50).mean().iloc[-1]
            return float(ema50)
        except Exception as e:
            logger.warning(f"NASDAQ EMA50 alınamadı: {e}")
            return None

    async def update(self):
        loop = asyncio.get_event_loop()

        nasdaq, dxy, vix, gold, nasdaq_ema = await asyncio.gather(
            loop.run_in_executor(None, self._fetch_price, "^NDX"),
            loop.run_in_executor(None, self._fetch_price, "DX-Y.NYB"),
            loop.run_in_executor(None, self._fetch_price, "^VIX"),
            loop.run_in_executor(None, self._fetch_price, "GC=F"),
            loop.run_in_executor(None, self._fetch_nasdaq_ema50),
        )

        # DXY 4 saatlik değişim (48 × 5dk = 240dk)
        dxy_4h_change = None
        if dxy is not None:
            self._dxy_history.append(dxy)
            if len(self._dxy_history) > 48:
                self._dxy_history.pop(0)
            if len(self._dxy_history) >= 2:
                prev = self._dxy_history[0]
                dxy_4h_change = (dxy - prev) / prev if prev else 0.0

        self._data = CrossAssetData(
            nasdaq=nasdaq,
            nasdaq_ema50=nasdaq_ema,
            dxy=dxy,
            dxy_4h_change_pct=dxy_4h_change,
            vix=vix,
            gold=gold,
            timestamp=int(time.time()),
        )
        logger.debug(
            f"Cross-asset | NASDAQ={nasdaq:.0f} EMA50={nasdaq_ema:.0f} "
            f"DXY={dxy:.2f} VIX={vix:.2f} Altın={gold:.0f}"
        )

    @property
    def data(self) -> CrossAssetData:
        return self._data

    async def start(self):
        self._running = True
        while self._running:
            try:
                await self.update()
            except Exception as e:
                logger.error(f"Cross-asset güncelleme hatası: {e}")
            await asyncio.sleep(self.update_interval)

    def stop(self):
        self._running = False

    async def close(self):
        self.stop()
