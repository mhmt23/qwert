import asyncio
import time
from dataclasses import dataclass
from typing import List, Optional, Dict
import aiohttp
from loguru import logger


@dataclass
class OISnapshot:
    open_interest: float
    timestamp: int


@dataclass
class FundingRate:
    rate: float
    next_funding_time: int
    timestamp: int


class BinanceRest:
    BASE = "https://fapi.binance.com"
    TESTNET = "https://testnet.binancefuture.com"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base = self.TESTNET if testnet else self.BASE
        self._session: Optional[aiohttp.ClientSession] = None

        # Cache
        self._oi_history: List[OISnapshot] = []
        self._last_funding: Optional[FundingRate] = None
        self._account_balance: float = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-MBX-APIKEY": self.api_key}
            )
        return self._session

    async def _get(self, path: str, params: dict = None) -> dict:
        session = await self._get_session()
        url = f"{self.base}{path}"
        timeout = aiohttp.ClientTimeout(total=15)
        async with session.get(url, params=params, timeout=timeout) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_open_interest(self, symbol: str) -> OISnapshot:
        try:
            data = await self._get("/fapi/v1/openInterest", {"symbol": symbol})
            snap = OISnapshot(
                open_interest=float(data["openInterest"]),
                timestamp=int(time.time() * 1000),
            )
            self._oi_history.append(snap)
            if len(self._oi_history) > 200:
                self._oi_history.pop(0)
            return snap
        except Exception as e:
            logger.error(f"OI alınamadı: {e}")
            return OISnapshot(0.0, int(time.time() * 1000))

    def get_oi_change_pct(self, lookback_count: int = 1) -> float:
        """OI yüzde değişimi. + artış, - azalış."""
        if len(self._oi_history) < lookback_count + 1:
            return 0.0
        prev = self._oi_history[-(lookback_count + 1)].open_interest
        curr = self._oi_history[-1].open_interest
        if prev == 0:
            return 0.0
        return (curr - prev) / prev

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        try:
            data = await self._get("/fapi/v1/premiumIndex", {"symbol": symbol})
            fr = FundingRate(
                rate=float(data["lastFundingRate"]),
                next_funding_time=int(data["nextFundingTime"]),
                timestamp=int(time.time() * 1000),
            )
            self._last_funding = fr
            return fr
        except Exception as e:
            logger.error(f"Funding rate alınamadı: {e}")
            return FundingRate(0.0, 0, int(time.time() * 1000))

    async def get_account_balance(self) -> float:
        """USDT available balance."""
        try:
            import hmac
            import hashlib
            import urllib.parse

            ts = int(time.time() * 1000)
            params = f"timestamp={ts}"
            sig = hmac.new(
                self.api_secret.encode(),
                params.encode(),
                hashlib.sha256,
            ).hexdigest()

            session = await self._get_session()
            url = f"{self.base}/fapi/v2/balance"
            async with session.get(
                url, params={"timestamp": ts, "signature": sig}
            ) as resp:
                data = await resp.json()
                for asset in data:
                    if asset["asset"] == "USDT":
                        self._account_balance = float(asset["availableBalance"])
                        return self._account_balance
        except Exception as e:
            logger.error(f"Bakiye alınamadı: {e}")
        return self._account_balance

    async def get_exchange_info(self) -> dict:
        """Tüm sembollerin bilgisini döner."""
        try:
            return await self._get("/fapi/v1/exchangeInfo")
        except Exception as e:
            logger.error(f"Exchange info alınamadı: {e}")
            return {"symbols": []}

    async def get_24hr_ticker_all(self) -> List[Dict]:
        """Tüm sembollerin 24h özeti — hacim, fiyat değişim vs."""
        try:
            return await self._get("/fapi/v1/ticker/24hr")
        except Exception as e:
            logger.error(f"24h ticker alınamadı: {e}")
            return []

    async def get_historical_klines(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int,
        limit: int = 1500,
    ) -> List[List]:
        """Backtest için tarihsel OHLCV verisi."""
        all_data = []
        current_start = start_ms

        while current_start < end_ms:
            try:
                data = await self._get(
                    "/fapi/v1/klines",
                    {
                        "symbol": symbol,
                        "interval": interval,
                        "startTime": current_start,
                        "endTime": end_ms,
                        "limit": limit,
                    },
                )
                if not data:
                    break
                all_data.extend(data)
                current_start = data[-1][0] + 1
                await asyncio.sleep(0.1)  # rate limit
            except Exception as e:
                logger.error(f"Kline verisi alınamadı: {e}")
                await asyncio.sleep(1)
                break

        return all_data

    async def get_historical_funding_rates(
        self, symbol: str, start_ms: int, end_ms: int
    ) -> List[Dict]:
        """Backtest için tarihsel funding rate."""
        all_data = []
        current_start = start_ms
        while current_start < end_ms:
            try:
                data = await self._get(
                    "/fapi/v1/fundingRate",
                    {"symbol": symbol, "startTime": current_start, "endTime": end_ms, "limit": 1000},
                )
                if not data:
                    break
                all_data.extend(data)
                current_start = int(data[-1]["fundingTime"]) + 1
                await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Funding rate tarihsel: {e}")
                break
        return all_data

    async def get_historical_oi(
        self, symbol: str, period: str = "5m", start_ms: int = None, limit: int = 500
    ) -> List[Dict]:
        """Backtest için tarihsel Open Interest."""
        params = {"symbol": symbol, "period": period, "limit": limit}
        if start_ms:
            params["startTime"] = start_ms
        try:
            return await self._get("/futures/data/openInterestHist", params)
        except Exception as e:
            logger.error(f"OI tarihsel: {e}")
            return []

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
