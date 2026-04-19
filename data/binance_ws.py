import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Deque
import websockets
from loguru import logger


@dataclass
class Kline:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    is_closed: bool


@dataclass
class AggTrade:
    price: float
    qty: float
    is_buyer_maker: bool  # True = satış (taker sell), False = alış (taker buy)
    timestamp: int


@dataclass
class Liquidation:
    side: str          # "BUY" veya "SELL"
    price: float
    qty: float
    usd_value: float
    timestamp: int


class BinanceWebSocket:
    WS_BASE = "wss://fstream.binance.com/stream?streams="
    WS_TESTNET = "wss://stream.binancefuture.com/stream?streams="

    def __init__(self, symbol: str, testnet: bool = False):
        self.symbol = symbol.lower()
        self.testnet = testnet
        self.base_url = self.WS_TESTNET if testnet else self.WS_BASE

        # Kline buffer: son 100 tamamlanmış mum
        self.klines: Deque[Kline] = deque(maxlen=100)
        self.current_kline: Optional[Kline] = None

        # CVD hesabı için agg trade buffer
        self.agg_trades: Deque[AggTrade] = deque(maxlen=1000)

        # Likidasyonlar: son 5 dakika
        self.liquidations: Deque[Liquidation] = deque(maxlen=500)

        self._running = False
        self._callbacks = []

    def on_kline_closed(self, callback):
        self._callbacks.append(("kline_closed", callback))

    def on_liquidation(self, callback):
        self._callbacks.append(("liquidation", callback))

    async def _notify(self, event: str, data):
        for evt, cb in self._callbacks:
            if evt == event:
                try:
                    await cb(data)
                except Exception as e:
                    logger.error(f"Callback hatası ({event}): {e}")

    def _parse_kline(self, k: dict) -> Kline:
        return Kline(
            open_time=k["t"],
            open=float(k["o"]),
            high=float(k["h"]),
            low=float(k["l"]),
            close=float(k["c"]),
            volume=float(k["v"]),
            close_time=k["T"],
            is_closed=k["x"],
        )

    def _parse_agg_trade(self, msg: dict) -> AggTrade:
        return AggTrade(
            price=float(msg["p"]),
            qty=float(msg["q"]),
            is_buyer_maker=msg["m"],
            timestamp=msg["T"],
        )

    def _parse_liquidation(self, msg: dict) -> Liquidation:
        o = msg["o"]
        price = float(o["p"])
        qty = float(o["q"])
        return Liquidation(
            side=o["S"],
            price=price,
            qty=qty,
            usd_value=price * qty,
            timestamp=int(time.time() * 1000),
        )

    async def _handle_message(self, raw: str):
        try:
            msg = json.loads(raw)
            stream = msg.get("stream", "")
            data = msg.get("data", msg)

            if "kline" in stream:
                k = data["k"]
                kline = self._parse_kline(k)
                self.current_kline = kline
                if kline.is_closed:
                    self.klines.append(kline)
                    await self._notify("kline_closed", kline)

            elif "aggTrade" in stream:
                trade = self._parse_agg_trade(data)
                self.agg_trades.append(trade)

            elif "forceOrder" in stream:
                liq = self._parse_liquidation(data)
                self.liquidations.append(liq)
                logger.info(f"Likidasyon: {liq.side} ${liq.usd_value:,.0f}")

        except Exception as e:
            logger.error(f"Mesaj işleme hatası: {e}")

    def get_cvd(self, lookback: int = 10) -> float:
        """Son N mumun Cumulative Volume Delta değeri."""
        if not self.klines:
            return 0.0

        # Her kapanmış mum için taker buy - taker sell
        # Yaklaşım: yeşil mum → net alım, kırmızı mum → net satım
        # Gerçek CVD için agg_trade kullanılır
        trades = list(self.agg_trades)
        if not trades:
            return 0.0

        # Son N mum zaman aralığı
        klines = list(self.klines)[-lookback:]
        if not klines:
            return 0.0
        start_ts = klines[0].open_time

        cvd = 0.0
        for t in trades:
            if t.timestamp >= start_ts:
                if t.is_buyer_maker:
                    cvd -= t.qty   # taker satış
                else:
                    cvd += t.qty   # taker alış
        return cvd

    def get_recent_liquidations(self, seconds: int = 300):
        """Son N saniyedeki likidasyonları döndür."""
        cutoff = (time.time() - seconds) * 1000
        return [l for l in self.liquidations if l.timestamp >= cutoff]

    def get_klines_list(self, n: int = 50) -> List[Kline]:
        return list(self.klines)[-n:]

    async def start(self):
        streams = [
            f"{self.symbol}@kline_1m",
            f"{self.symbol}@aggTrade",
            f"{self.symbol}@forceOrder",
        ]
        url = self.base_url + "/".join(streams)
        self._running = True

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"WebSocket bağlantısı kuruldu: {self.symbol}")
                    async for raw in ws:
                        if not self._running:
                            break
                        await self._handle_message(raw)
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket bağlantısı kesildi, yeniden bağlanılıyor...")
                await asyncio.sleep(3)
            except Exception as e:
                logger.error(f"WebSocket hatası: {e}")
                await asyncio.sleep(5)

    def stop(self):
        self._running = False
