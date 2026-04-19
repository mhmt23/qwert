import asyncio
import hashlib
import hmac
import time
import urllib.parse
from dataclasses import dataclass
from typing import Optional
import aiohttp
from loguru import logger

from config import CONFIG


@dataclass
class OrderResult:
    order_id: Optional[int]
    symbol: str
    side: str
    price: float
    qty: float
    status: str
    paper: bool = False


class OrderManager:
    BASE = "https://fapi.binance.com"
    TESTNET = "https://testnet.binancefuture.com"

    def __init__(self, api_key: str, api_secret: str, testnet: bool = False, paper: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base = self.TESTNET if testnet else self.BASE
        self.paper = paper
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"X-MBX-APIKEY": self.api_key}
            )
        return self._session

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        query = urllib.parse.urlencode(params)
        sig = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    async def _post(self, path: str, params: dict) -> dict:
        session = await self._get_session()
        signed = self._sign(params)
        async with session.post(f"{self.base}{path}", params=signed) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise Exception(f"Order hatası {resp.status}: {data}")
            return data

    async def set_leverage(self, symbol: str, leverage: int):
        if self.paper:
            return
        try:
            await self._post("/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})
            logger.info(f"Kaldıraç ayarlandı: {leverage}x")
        except Exception as e:
            logger.error(f"Kaldıraç ayarlanamadı: {e}")

    async def open_position(
        self,
        symbol: str,
        side: str,           # "BUY" veya "SELL"
        usdt_amount: float,
        current_price: float,
    ) -> OrderResult:
        qty = round(usdt_amount * CONFIG.LEVERAGE / current_price, 3)

        if self.paper:
            logger.info(f"[PAPER] {side} {qty} {symbol} @ {current_price:.2f}")
            return OrderResult(
                order_id=None, symbol=symbol, side=side,
                price=current_price, qty=qty, status="FILLED", paper=True
            )

        try:
            data = await self._post("/fapi/v1/order", {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": qty,
                "price": round(current_price, 2),
                "reduceOnly": "false",
            })
            return OrderResult(
                order_id=data["orderId"], symbol=symbol, side=side,
                price=float(data["price"]), qty=float(data["origQty"]),
                status=data["status"]
            )
        except Exception as e:
            logger.error(f"Pozisyon açma hatası: {e}")
            raise

    async def place_tp_sl(
        self,
        symbol: str,
        entry_side: str,    # giriş yönü
        qty: float,
        tp_price: float,
        sl_price: float,
    ):
        close_side = "SELL" if entry_side == "BUY" else "BUY"

        if self.paper:
            logger.info(f"[PAPER] TP={tp_price:.2f} SL={sl_price:.2f}")
            return

        # Take Profit (limit)
        try:
            await self._post("/fapi/v1/order", {
                "symbol": symbol,
                "side": close_side,
                "type": "TAKE_PROFIT",
                "timeInForce": "GTC",
                "quantity": qty,
                "price": round(tp_price, 2),
                "stopPrice": round(tp_price, 2),
                "reduceOnly": "true",
            })
        except Exception as e:
            logger.error(f"TP yerleştirme hatası: {e}")

        # Stop Loss (market)
        try:
            await self._post("/fapi/v1/order", {
                "symbol": symbol,
                "side": close_side,
                "type": "STOP_MARKET",
                "quantity": qty,
                "stopPrice": round(sl_price, 2),
                "reduceOnly": "true",
            })
        except Exception as e:
            logger.error(f"SL yerleştirme hatası: {e}")

    async def cancel_all_orders(self, symbol: str):
        if self.paper:
            return
        try:
            await self._post("/fapi/v1/allOpenOrders", {"symbol": symbol})
        except Exception as e:
            logger.error(f"Emir iptal hatası: {e}")

    async def close_position_market(self, symbol: str, side: str, qty: float):
        """Zorla piyasa fiyatından kapat."""
        close_side = "SELL" if side == "BUY" else "BUY"
        if self.paper:
            logger.info(f"[PAPER] Market ile kapat: {close_side} {qty} {symbol}")
            return
        try:
            await self._post("/fapi/v1/order", {
                "symbol": symbol,
                "side": close_side,
                "type": "MARKET",
                "quantity": qty,
                "reduceOnly": "true",
            })
        except Exception as e:
            logger.error(f"Market kapama hatası: {e}")

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
