"""
Funding Arbitraj — Cash-and-Carry Bot (Paper).

Mantık:
  Delta-nötr pozisyon: spot long + perp short (veya tam tersi).
  Yön riski yok; tek gelir 8 saatte bir gelen funding ödemeleri.

Çalışma:
  1) Her gün top 10 coin'i güncel funding oranlarına göre sırala
  2) Seçileni aç, her pozisyon için sermayeyi eşit böl
  3) 8 saatte bir funding ödemesi: rate × notional → bakiye
  4) Gün sonunda: en kötü performans coinleri kapat, yenilerini aç
  5) Telegram: her açılış, her funding ödemesi (toplu), günlük özet

Şu an: PAPER MODE — gerçek emir atılmaz, simülasyon.
"""
import asyncio
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from config import CONFIG
from data.binance_rest import BinanceRest
from telegram.bot import TelegramBot


logger.add("logs/funding_arb_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days")


# --- Parametreler ---
TOP_N_COINS        = 5           # Eş zamanlı açık pozisyon sayısı
CANDIDATE_POOL     = 30          # En yüksek hacimli kaç coin değerlendirilsin
MIN_FUNDING_PCT    = 0.0001      # %0.01 / 8h = yıllık ~%11 eşiği
REBALANCE_HOURS    = 24          # Her 24 saatte bir yeniden dengele
FUNDING_CHECK_SEC  = 600         # Her 10 dakikada funding ödemesi kontrol
FLIP_CLOSE_PCT     = 0.00005     # Rate yönümüze göre < bu eşiğe inerse kapat
INITIAL_BALANCE    = 100.0
ROUND_TRIP_FEE_PCT = 0.003       # Pozisyon başına aç+kapa yaklaşık fee
EXCLUDE_NON_ASCII  = True        # Çince/Arapça sembolleri atla


@dataclass
class FundingPosition:
    symbol: str
    direction: int              # +1: spot long / perp short (funding+ kazanıyor)
                                # -1: spot short / perp long (funding- kazanıyor)
    notional: float             # Pozisyon büyüklüğü (USDT)
    opened_at: float
    last_funding_paid: int      # Kaç ödeme aldık (log için)
    accrued_funding: float      # Kümülatif funding geliri (USDT)


class FundingArbBot:
    def __init__(self):
        self.rest = BinanceRest(
            CONFIG.BINANCE_API_KEY, CONFIG.BINANCE_API_SECRET, testnet=False,
        )
        self.tg = TelegramBot(CONFIG.TELEGRAM_TOKEN, CONFIG.TELEGRAM_CHAT_ID)
        self._setup_tg()

        self.balance = INITIAL_BALANCE
        self.positions: Dict[str, FundingPosition] = {}
        self.last_rebalance = 0.0
        self.last_daily_reset_day = -1
        self._daily_funding = 0.0
        self._daily_fees = 0.0
        self._running = False

        # Funding zamanlayıcıları — her sembolün son kontrolü
        self._last_next_funding: Dict[str, int] = {}

    # --- Telegram ---
    def _setup_tg(self):
        self.tg.on_stop(self._handle_stop)
        self.tg.on_status(self._handle_status)
        self.tg.on_stats(self._handle_stats)
        self.tg.on_pause(lambda m: None)

    async def _handle_stop(self):
        self._running = False
        await self.tg.send("🛑 Funding Arb durduruluyor.")

    async def _handle_status(self) -> str:
        if not self.positions:
            return (
                f"<b>Açık Pozisyon:</b> Yok\n"
                f"<b>Bakiye:</b> ${self.balance:.4f}\n"
                f"<b>Bugün Funding:</b> ${self._daily_funding:+.4f}"
            )
        lines = [f"<b>Açık: {len(self.positions)}/{TOP_N_COINS}</b>"]
        for p in self.positions.values():
            d = "LONG-SPOT/SHORT-PERP" if p.direction == 1 else "SHORT-SPOT/LONG-PERP"
            lines.append(
                f"• {p.symbol} [{d}]  ${p.notional:.2f}  "
                f"funding: ${p.accrued_funding:+.4f} ({p.last_funding_paid}x)"
            )
        lines.append(f"\n<b>Bakiye:</b> ${self.balance:.4f}")
        lines.append(f"<b>Bugün Funding:</b> ${self._daily_funding:+.4f} "
                     f"Fee: -${self._daily_fees:.4f}")
        return "\n".join(lines)

    async def _handle_stats(self, full: bool = False) -> str:
        total_pnl = self.balance - INITIAL_BALANCE
        days = max(1, (time.time() - self._start_ts) / 86400)
        annualized = (total_pnl / INITIAL_BALANCE) * (365 / days) * 100
        return (
            f"📊 <b>FUNDING ARB</b>\n"
            f"Açık poz: {len(self.positions)}/{TOP_N_COINS}\n"
            f"Bakiye: ${self.balance:.4f}\n"
            f"Toplam PnL: ${total_pnl:+.4f} ({total_pnl/INITIAL_BALANCE*100:+.2f}%)\n"
            f"Tahmini yıllık: {annualized:+.2f}%\n"
            f"Çalışma süresi: {days:.1f} gün"
        )

    # --- Veri ---
    async def _fetch_all_premiums(self) -> List[Dict]:
        """Tüm sembollerin premium index'i (son funding + tahmini bir sonraki)."""
        try:
            data = await self.rest._get("/fapi/v1/premiumIndex")
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.error(f"Premium index alınamadı: {e}")
            return []

    async def _rank_candidates(self) -> List[Dict]:
        """Mutlak funding'e göre en büyük edge'li coinleri döndür."""
        premiums = await self._fetch_all_premiums()
        if not premiums:
            return []

        ranked = []
        for p in premiums:
            sym = p.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            if EXCLUDE_NON_ASCII and not sym.isascii():
                continue
            try:
                rate = float(p.get("lastFundingRate", 0.0))
                next_ft = int(p.get("nextFundingTime", 0))
            except (ValueError, TypeError):
                continue

            if abs(rate) < MIN_FUNDING_PCT:
                continue

            direction = 1 if rate > 0 else -1
            annualized = abs(rate) * 3 * 365 * 100   # 8h → yıl
            ranked.append({
                "symbol": sym, "rate": rate, "direction": direction,
                "annualized": annualized, "next_funding": next_ft,
            })

        ranked.sort(key=lambda x: abs(x["rate"]), reverse=True)
        return ranked[:CANDIDATE_POOL]

    # --- Pozisyon yönetimi ---
    async def _open_position(self, cand: Dict, notional: float):
        sym = cand["symbol"]
        fee = notional * ROUND_TRIP_FEE_PCT / 2   # sadece açılış kısmı
        self.balance -= fee
        self._daily_fees += fee

        pos = FundingPosition(
            symbol=sym, direction=cand["direction"], notional=notional,
            opened_at=time.time(), last_funding_paid=0, accrued_funding=0.0,
        )
        self.positions[sym] = pos
        self._last_next_funding[sym] = cand["next_funding"]

        d = "LONG-SPOT / SHORT-PERP" if cand["direction"] == 1 else "SHORT-SPOT / LONG-PERP"
        await self.tg.send(
            f"🟢 <b>[PAPER] AÇILDI — {sym}</b>\n"
            f"{d}\n"
            f"Notional: ${notional:.2f}\n"
            f"Mevcut rate: %{cand['rate']*100:.4f} / 8h\n"
            f"Tahmini yıllık: %{cand['annualized']:.2f}\n"
            f"Fee: -${fee:.4f}"
        )

    async def _close_position(self, pos: FundingPosition, reason: str):
        fee = pos.notional * ROUND_TRIP_FEE_PCT / 2
        self.balance -= fee
        self._daily_fees += fee
        del self.positions[pos.symbol]
        self._last_next_funding.pop(pos.symbol, None)

        age_h = (time.time() - pos.opened_at) / 3600
        await self.tg.send(
            f"🔄 <b>[PAPER] KAPANDI — {pos.symbol}</b>\n"
            f"Sebep: {reason}\n"
            f"Funding geliri: ${pos.accrued_funding:+.4f}\n"
            f"Süre: {age_h:.1f}h ({pos.last_funding_paid} ödeme)\n"
            f"Fee: -${fee:.4f}"
        )

    async def _check_funding_events(self):
        """Her sembol için bir sonraki funding zamanı geçmişse ödemeyi uygula."""
        if not self.positions:
            return

        now_ms = int(time.time() * 1000)
        # Güncel rate + next funding zamanlarını çek
        try:
            premiums = await self._fetch_all_premiums()
            rate_map = {p["symbol"]: p for p in premiums}
        except Exception:
            return

        for sym, pos in list(self.positions.items()):
            p = rate_map.get(sym)
            if not p:
                continue
            try:
                rate = float(p["lastFundingRate"])
                next_ft = int(p["nextFundingTime"])
            except (ValueError, TypeError, KeyError):
                continue

            prev_next = self._last_next_funding.get(sym, 0)
            # nextFundingTime ileri gittiyse yeni ödeme olmuş demektir
            if next_ft > prev_next and prev_next > 0:
                # Ödeme: perp short isek pozitif rate bize gelir
                payment = pos.notional * rate * pos.direction
                self.balance += payment
                pos.accrued_funding += payment
                pos.last_funding_paid += 1
                self._daily_funding += payment

                emoji = "💰" if payment > 0 else "⚠️"
                sign = "+" if payment >= 0 else ""
                await self.tg.send(
                    f"{emoji} <b>FUNDING — {sym}</b>\n"
                    f"Rate: %{rate*100:.4f} × ${pos.notional:.2f}\n"
                    f"Ödeme: <b>{sign}${payment:.4f}</b>\n"
                    f"Kümülatif: ${pos.accrued_funding:+.4f}\n"
                    f"Bakiye: ${self.balance:.4f}"
                )
            self._last_next_funding[sym] = next_ft

            # Funding yönümüze karşı döndü mü? Pozisyonu kapat
            effective = rate * pos.direction   # bize gelen işaret
            if effective < FLIP_CLOSE_PCT:
                await self._close_position(
                    pos, f"Funding döndü (rate: {rate*100:+.4f}%)",
                )

    async def _rebalance(self):
        """Günlük rebalance: kötü performansı kapat, yeni top'u aç."""
        candidates = await self._rank_candidates()
        if not candidates:
            logger.info("Aday bulunamadı.")
            return

        # Aday sembolleri
        top_symbols = {c["symbol"] for c in candidates[:TOP_N_COINS * 2]}

        # Açıklardan adayda olmayanları kapat
        for sym in list(self.positions.keys()):
            if sym not in top_symbols:
                await self._close_position(self.positions[sym], "TOP listesinden düştü")

        # Kapasite varsa yeni aç
        empty_slots = TOP_N_COINS - len(self.positions)
        if empty_slots <= 0:
            return

        per_pos = max(5.0, self.balance / TOP_N_COINS * 0.5)   # tutucu paylaşım
        for c in candidates:
            if empty_slots <= 0:
                break
            if c["symbol"] in self.positions:
                continue
            if per_pos > self.balance * 0.5:
                break
            await self._open_position(c, per_pos)
            empty_slots -= 1

        await self.tg.send(
            f"♻️ <b>REBALANCE</b>\n"
            f"Açık: {len(self.positions)}/{TOP_N_COINS}\n"
            f"Bakiye: ${self.balance:.4f}"
        )

    # --- Ana döngüler ---
    async def _main_loop(self):
        while self._running:
            try:
                # Günlük sıfırlama
                now_utc = datetime.now(timezone.utc)
                if now_utc.day != self.last_daily_reset_day:
                    if self.last_daily_reset_day != -1:
                        await self.tg.send(
                            f"📊 <b>GÜNLÜK ÖZET</b>\n"
                            f"Funding: ${self._daily_funding:+.4f}\n"
                            f"Fee: -${self._daily_fees:.4f}\n"
                            f"Net: ${self._daily_funding - self._daily_fees:+.4f}\n"
                            f"Bakiye: ${self.balance:.4f}"
                        )
                    self._daily_funding = 0.0
                    self._daily_fees = 0.0
                    self.last_daily_reset_day = now_utc.day

                # Rebalance
                if time.time() - self.last_rebalance >= REBALANCE_HOURS * 3600:
                    await self._rebalance()
                    self.last_rebalance = time.time()

                # Funding ödemeleri
                await self._check_funding_events()

                await asyncio.sleep(FUNDING_CHECK_SEC)
            except Exception as e:
                logger.error(f"Ana döngü hatası: {e}")
                await asyncio.sleep(FUNDING_CHECK_SEC)

    async def run(self):
        self._running = True
        self._start_ts = time.time()
        self.last_daily_reset_day = datetime.now(timezone.utc).day

        await self.tg.send(
            f"🚀 <b>FUNDING ARB BAŞLADI</b>\n"
            f"Mod: <b>PAPER TRADING</b>\n"
            f"Sermaye: ${INITIAL_BALANCE:.2f}\n"
            f"Eş zamanlı poz: {TOP_N_COINS}\n"
            f"Rebalance: her {REBALANCE_HOURS} saat\n"
            f"Min funding: %{MIN_FUNDING_PCT*100:.4f} / 8h "
            f"(~yıllık %{MIN_FUNDING_PCT*3*365*100:.1f})\n"
            f"Komutlar: /status /stats /stop"
        )

        # İlk rebalance
        await self._rebalance()
        self.last_rebalance = time.time()

        await asyncio.gather(
            self._main_loop(),
            self.tg.start_polling(),
        )

    async def shutdown(self):
        try:
            await self.rest.close()
        except Exception:
            pass
        try:
            await self.tg.stop()
        except Exception:
            pass


async def main():
    bot = FundingArbBot()
    try:
        await bot.run()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot kapatılıyor...")
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
