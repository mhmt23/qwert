import asyncio
import time
from datetime import datetime
from loguru import logger

from config import CONFIG
from data.binance_ws import BinanceWebSocket
from data.binance_rest import BinanceRest
from data.cross_asset import CrossAssetFeed
from data.sentiment import SentimentFeed
from strategy.macro_filter import MacroFilter
from strategy.smart_money import SmartMoneyAnalyzer
from strategy.entry_signal import EntrySignalDetector
from strategy.confluence import ConfluenceScorer
from strategy.funding_signal import evaluate_funding
from strategy.liquidation_zones import evaluate_liquidations_live
from strategy.multi_coin_scanner import scan_all_coins, best_coin, TOP_COINS
from execution.order_manager import OrderManager
from execution.position_tracker import PositionTracker, Position
from execution.risk_manager import RiskManager
from telegram.bot import TelegramBot


logger.add("logs/bot_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days")


class SmartMoneyBot:
    def __init__(self):
        self.ws = BinanceWebSocket(CONFIG.SYMBOL, testnet=CONFIG.BINANCE_TESTNET)
        self.rest = BinanceRest(CONFIG.BINANCE_API_KEY, CONFIG.BINANCE_API_SECRET, CONFIG.BINANCE_TESTNET)
        self.cross = CrossAssetFeed(CONFIG.TWELVE_DATA_API_KEY, CONFIG.CROSS_ASSET_UPDATE_INTERVAL)
        self.sentiment = SentimentFeed(CONFIG.CRYPTOPANIC_API_KEY)

        self.macro_filter = MacroFilter()
        self.smart_money = SmartMoneyAnalyzer()
        self.entry_detector = EntrySignalDetector()
        self.confluence = ConfluenceScorer()

        self.orders = OrderManager(
            CONFIG.BINANCE_API_KEY, CONFIG.BINANCE_API_SECRET,
            testnet=CONFIG.BINANCE_TESTNET, paper=CONFIG.PAPER_TRADING
        )
        self.tracker = PositionTracker()
        self.risk = RiskManager()

        self.tg = TelegramBot(CONFIG.TELEGRAM_TOKEN, CONFIG.TELEGRAM_CHAT_ID)
        self._setup_telegram()

        self._running = False
        self._last_oi_update = 0
        self._last_day = -1
        self._last_coin_scan = 0
        self._active_symbol = CONFIG.SYMBOL   # Trap tarayıcısının seçtiği koin

        # Günlük istatistik
        self._daily_gross_pnl = 0.0
        self._daily_fees = 0.0
        self._daily_wins = 0
        self._daily_trades = 0

    def _setup_telegram(self):
        self.tg.on_stop(self._handle_stop)
        self.tg.on_status(self._handle_status)
        self.tg.on_stats(self._handle_stats)
        self.tg.on_pause(self._handle_pause)

    async def _handle_stop(self):
        self._running = False
        if self.tracker.is_open():
            price = self.ws.current_kline.close if self.ws.current_kline else 0
            self.tracker.close(price, "MANUAL_STOP")
        await self.tg.send("Bot durduruldu.")

    async def _handle_status(self) -> str:
        pos = self.tracker.current
        if pos:
            price = self.ws.current_kline.close if self.ws.current_kline else pos.entry_price
            pnl = pos.calc_pnl(price)
            return (
                f"<b>Açık Pozisyon:</b> {'LONG' if pos.side=='BUY' else 'SHORT'}\n"
                f"Giriş: {pos.entry_price:,.2f} | Şimdi: {price:,.2f}\n"
                f"PnL: {pnl:+.4f} USDT\n"
                f"<b>Günlük Net:</b> {self._daily_gross_pnl - self._daily_fees:+.4f} USDT"
            )
        return (
            f"<b>Açık Pozisyon:</b> Yok\n"
            f"<b>Günlük İşlem:</b> {self._daily_trades}\n"
            f"<b>Günlük Net PnL:</b> {self._daily_gross_pnl - self._daily_fees:+.4f} USDT"
        )

    async def _handle_stats(self, full: bool = False) -> str:
        win_rate = (self._daily_wins / self._daily_trades * 100) if self._daily_trades > 0 else 0
        return (
            f"📊 <b>İSTATİSTİK</b>\n"
            f"İşlem: {self._daily_trades} | Win: %{win_rate:.1f}\n"
            f"Gross: {self._daily_gross_pnl:+.4f} USDT\n"
            f"Fee: -{self._daily_fees:.4f} USDT\n"
            f"Net: {self._daily_gross_pnl - self._daily_fees:+.4f} USDT\n"
            f"Bakiye: {self.risk.state.account_balance:.4f} USDT"
        )

    async def _handle_pause(self, minutes: int):
        self.risk.state.paused_until = time.time() + minutes * 60

    async def _update_oi_and_funding(self):
        now = time.time()
        if now - self._last_oi_update >= CONFIG.OI_UPDATE_INTERVAL:
            await asyncio.gather(
                self.rest.get_open_interest(self._active_symbol),
                self.rest.get_funding_rate(self._active_symbol),
            )
            self._last_oi_update = now

    async def _trap_scan(self) -> int:
        """
        Multi-coin Trap tarayıcısı: funding + OI ile en iyi koini seçer.
        Her 5 dakikada bir çalışır.
        Seçilen koin self._active_symbol'a kaydedilir.
        Returns: direction (+1/-1/0)
        """
        now = time.time()
        if now - self._last_coin_scan < 300:  # 5 dakikada bir
            return 0
        self._last_coin_scan = now

        scores = await scan_all_coins(self.rest, TOP_COINS[:10])
        top = best_coin(scores)
        if top:
            self._active_symbol = top.symbol
            logger.info(
                f"Trap tarama: {top.symbol} seçildi | "
                f"Yön={'LONG' if top.direction==1 else 'SHORT'} | "
                f"Skor={top.total_score} | {top.reason}"
            )
            return top.direction
        return 0

    async def _on_kline_closed(self, kline):
        # Günlük sıfırlama
        current_day = datetime.now().day
        if current_day != self._last_day:
            self.risk.reset_daily()
            await self.tg.notify_daily_summary(
                self._daily_trades, self._daily_wins,
                self._daily_gross_pnl, self._daily_fees,
                self._daily_gross_pnl - self._daily_fees
            )
            self._daily_gross_pnl = 0.0
            self._daily_fees = 0.0
            self._daily_wins = 0
            self._daily_trades = 0
            self._last_day = current_day

        # OI / Funding güncelle
        await self._update_oi_and_funding()

        current_price = kline.close

        # Açık pozisyon kontrolü
        if self.tracker.is_open():
            reason = self.tracker.check_exit(current_price)
            if reason:
                record = self.tracker.close(current_price, reason)
                gross = record["pnl_usd"] + (record["pnl_usd"] * 0.0004)  # fee yaklaşım
                fee = abs(record["entry_price"] * record["qty"] * 0.0004)
                net = record["pnl_usd"]

                self.risk.record_trade(net)
                self._daily_gross_pnl += gross
                self._daily_fees += fee
                self._daily_trades += 1
                if net > 0:
                    self._daily_wins += 1

                await self.tg.notify_trade_closed(
                    reason, current_price, net,
                    record["pnl_pct"], time.time() - record["open_time"],
                    paper=CONFIG.PAPER_TRADING
                )

                # Makro ters dönüşte zorla kapat
                if reason == "TIMEOUT":
                    await self.orders.cancel_all_orders(CONFIG.SYMBOL)
            return

        # Yeni işlem değerlendirmesi
        can, reason_str = self.risk.can_trade()
        if not can:
            return

        # --- Trap Strategy ---
        trap_direction = await self._trap_scan()

        # Eski strateji (fallback): klasik confluence değerlendirmesi
        macro = self.macro_filter.evaluate(self.cross.data, self.sentiment.data)
        smart = self.smart_money.evaluate(self.rest, self.ws)
        entry = self.entry_detector.evaluate(self.ws)
        classic = self.confluence.evaluate(macro, smart, entry)

        # Funding + OI anlık sinyali (seçili koin için)
        funding_obj = self.rest._last_funding
        funding_rate = funding_obj.rate if funding_obj else 0.0
        oi_change = self.rest.get_oi_change_pct(lookback_count=1)
        funding_sig = evaluate_funding(funding_rate, oi_change)

        # Likidasyon (canlı)
        recent_liqs = self.ws.get_recent_liquidations(seconds=300)
        liq_sig = evaluate_liquidations_live(current_price, recent_liqs)

        # Trap skoru: funding + OI + liq + ema_trend (macro)
        trap_score = (
            (1 if funding_sig.direction != 0 else 0) +
            (1 if abs(oi_change) >= 0.01 else 0) +
            (1 if liq_sig.direction != 0 else 0) +
            (1 if classic.should_trade else 0)
        )

        # Yön belirleme: Trap öncelikli, classic fallback
        final_direction = 0
        score = 0

        if funding_sig.direction != 0 and trap_score >= 2:
            final_direction = funding_sig.direction
            score = trap_score
        elif classic.should_trade:
            final_direction = classic.direction
            score = classic.score

        if final_direction == 0:
            return

        # Sembol: Trap seçtiyse o koin, değilse config sembol
        symbol = self._active_symbol if trap_direction != 0 else CONFIG.SYMBOL

        # Bakiye al
        if not CONFIG.PAPER_TRADING:
            balance = await self.rest.get_account_balance()
            self.risk.update_balance(balance)
        balance = self.risk.state.account_balance

        size_mult = 1.0 if score >= 4 else 0.5
        usdt_margin = self.risk.get_position_size(balance, size_mult)
        notional = usdt_margin * CONFIG.LEVERAGE

        side = "BUY" if final_direction == 1 else "SELL"
        if final_direction == 1:
            tp_price = current_price * (1 + CONFIG.TAKE_PROFIT_PCT)
            sl_price = current_price * (1 - CONFIG.STOP_LOSS_PCT)
        else:
            tp_price = current_price * (1 - CONFIG.TAKE_PROFIT_PCT)
            sl_price = current_price * (1 + CONFIG.STOP_LOSS_PCT)

        try:
            order = await self.orders.open_position(symbol, side, usdt_margin, current_price)
            await self.orders.place_tp_sl(symbol, side, order.qty, tp_price, sl_price)

            pos = Position(
                symbol=symbol,
                side=side,
                entry_price=order.price,
                qty=order.qty,
                tp_price=tp_price,
                sl_price=sl_price,
                confluence_score=score,
                paper=CONFIG.PAPER_TRADING,
            )
            self.tracker.open(pos)

            reasons = []
            if funding_sig.reason:
                reasons.append(funding_sig.reason)
            if liq_sig.reason and liq_sig.direction != 0:
                reasons.append(liq_sig.reason)

            await self.tg.notify_trade_opened(
                side, order.price, tp_price, sl_price,
                notional, score, CONFIG.PAPER_TRADING
            )
        except Exception as e:
            logger.error(f"İşlem açma hatası: {e}")

    async def run(self):
        self._running = True
        self._last_day = datetime.now().day

        # Kaldıraç ayarla
        await self.orders.set_leverage(CONFIG.SYMBOL, CONFIG.LEVERAGE)

        # WebSocket kline callback
        self.ws.on_kline_closed(self._on_kline_closed)

        mode = "PAPER TRADING" if CONFIG.PAPER_TRADING else "CANLI TRADING"
        await self.tg.send(
            f"🚀 <b>Smart Money Bot Başladı</b>\n"
            f"Mod: {mode}\n"
            f"Sembol: {CONFIG.SYMBOL} | {CONFIG.LEVERAGE}x\n"
            f"TP: %{CONFIG.TAKE_PROFIT_PCT*100:.2f} | SL: %{CONFIG.STOP_LOSS_PCT*100:.2f}"
        )

        await asyncio.gather(
            self.ws.start(),
            self.cross.start(),
            self.sentiment.start(),
            self.tg.start_polling(),
        )

    async def shutdown(self):
        self.ws.stop()
        self.cross.stop()
        self.sentiment.stop()
        await self.rest.close()
        await self.orders.close()
        await self.tg.stop()


async def main():
    bot = SmartMoneyBot()
    try:
        await bot.run()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot kapatılıyor...")
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
