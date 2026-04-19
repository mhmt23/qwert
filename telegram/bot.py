import asyncio
from datetime import datetime
from typing import Optional, Callable
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from loguru import logger

from config import CONFIG


class TelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.chat_id = chat_id
        self._stop_callback: Optional[Callable] = None
        self._status_callback: Optional[Callable] = None
        self._stats_callback: Optional[Callable] = None
        self._pause_callback: Optional[Callable] = None
        self._register_handlers()

    def on_stop(self, cb): self._stop_callback = cb
    def on_status(self, cb): self._status_callback = cb
    def on_stats(self, cb): self._stats_callback = cb
    def on_pause(self, cb): self._pause_callback = cb

    def _register_handlers(self):
        @self.dp.message(Command("start"))
        async def cmd_start(msg: types.Message):
            await msg.answer(
                "Bot aktif!\n"
                "/status — açık pozisyon + günlük PnL\n"
                "/stats — 7 günlük istatistik\n"
                "/pnl — toplam PnL\n"
                "/pause 30 — dakika duraklat\n"
                "/stop — botu durdur"
            )

        @self.dp.message(Command("stop"))
        async def cmd_stop(msg: types.Message):
            await msg.answer("Bot durduruluyor...")
            if self._stop_callback:
                await self._stop_callback()

        @self.dp.message(Command("status"))
        async def cmd_status(msg: types.Message):
            if self._status_callback:
                text = await self._status_callback()
                await msg.answer(text, parse_mode="HTML")
            else:
                await msg.answer("Durum bilgisi yok.")

        @self.dp.message(Command("stats"))
        async def cmd_stats(msg: types.Message):
            if self._stats_callback:
                text = await self._stats_callback()
                await msg.answer(text, parse_mode="HTML")

        @self.dp.message(Command("pnl"))
        async def cmd_pnl(msg: types.Message):
            if self._stats_callback:
                text = await self._stats_callback(full=True)
                await msg.answer(text, parse_mode="HTML")

        @self.dp.message(Command("pause"))
        async def cmd_pause(msg: types.Message):
            parts = msg.text.split()
            minutes = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 30
            if self._pause_callback:
                await self._pause_callback(minutes)
            await msg.answer(f"{minutes} dakika duraklıyor.")

    async def send(self, text: str):
        try:
            await self.bot.send_message(self.chat_id, text, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Telegram gönderme hatası: {e}")

    async def notify_trade_opened(
        self, side: str, price: float, tp: float, sl: float,
        notional: float, confluence: int, paper: bool = True
    ):
        emoji = "🟢" if side == "BUY" else "🔴"
        mode = "[PAPER] " if paper else ""
        direction = "LONG" if side == "BUY" else "SHORT"
        text = (
            f"{emoji} <b>{mode}İŞLEM AÇILDI</b>\n"
            f"<b>{direction} BTCUSDT</b> @ {price:,.2f}\n"
            f"TP: {tp:,.2f} (+%{CONFIG.TAKE_PROFIT_PCT*100:.2f})\n"
            f"SL: {sl:,.2f} (-%{CONFIG.STOP_LOSS_PCT*100:.2f})\n"
            f"Notional: ${notional:.2f} | {CONFIG.LEVERAGE}x\n"
            f"Confluence: {confluence}/5"
        )
        await self.send(text)

    async def notify_trade_closed(
        self, reason: str, close_price: float, pnl_usd: float,
        pnl_pct: float, duration_sec: float, paper: bool = True
    ):
        if reason == "TP":
            emoji = "🎯"
        elif reason == "SL":
            emoji = "❌"
        else:
            emoji = "⏱"

        sign = "+" if pnl_usd >= 0 else ""
        mode = "[PAPER] " if paper else ""
        mins = int(duration_sec // 60)
        secs = int(duration_sec % 60)

        text = (
            f"{emoji} <b>{mode}{reason} ÇALIŞTI</b>\n"
            f"Kapanış: {close_price:,.2f}\n"
            f"PnL: <b>{sign}{pnl_usd:.4f} USDT</b> ({sign}{pnl_pct*100:.3f}%)\n"
            f"Süre: {mins}dk {secs}s"
        )
        await self.send(text)

    async def notify_daily_summary(
        self, trade_count: int, win_count: int, gross_pnl: float,
        fee: float, net_pnl: float
    ):
        win_rate = (win_count / trade_count * 100) if trade_count > 0 else 0
        text = (
            f"📊 <b>GÜNLÜK ÖZET</b> ({datetime.now().strftime('%d.%m.%Y')})\n"
            f"İşlem: {trade_count} | Kazanan: {win_count} (%{win_rate:.1f})\n"
            f"Gross PnL: {gross_pnl:+.4f} USDT\n"
            f"Fee: -{fee:.4f} USDT\n"
            f"Net PnL: <b>{net_pnl:+.4f} USDT</b>"
        )
        await self.send(text)

    async def notify_risk_alert(self, message: str):
        await self.send(f"⚠️ <b>RİSK UYARISI</b>\n{message}")

    async def start_polling(self):
        logger.info("Telegram bot başlatılıyor...")
        await self.dp.start_polling(self.bot, handle_signals=False)

    async def stop(self):
        await self.bot.session.close()
