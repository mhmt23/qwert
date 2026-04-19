"""
Bakkal Stratejisi — Canlı (Paper) Çalıştırıcı.

- 1h adaptif hibrit rejim sinyali (TREND + RANGE, min_score=4)
- Top-N koini tarar, her koinde günde 1 işlem
- Maks 3 açık pozisyon (aynı yönde maks 2)
- TP/SL: ATR-bazlı
- Soft exit: Yön sinyali ters dönerse erken kapat
- Paper trading varsayılan — Telegram'dan tüm işlemler takip edilir

Komutlar (Telegram):
  /status  — açık pozisyon + günlük PnL
  /stats   — günlük istatistik
  /stop    — botu durdur
  /pause N — N dakika duraklat
"""
import asyncio
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

import pickle
from pathlib import Path
import numpy as np
import pandas as pd
from loguru import logger

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from config import CONFIG
from data.binance_rest import BinanceRest
from backtest.data_loader import BacktestDataLoader
from strategy import bakkal_signal
from strategy.bakkal_signal import (
    compute_indicators, evaluate_row, direction_flipped, BakkalSignal,
)
from strategy.coin_universe import load_coin_universe
from execution.order_manager import OrderManager
from execution.risk_manager import RiskManager
from telegram.bot import TelegramBot


logger.add("logs/bakkal_{time:YYYY-MM-DD}.log", rotation="00:00", retention="30 days")


# --- Çalışma parametreleri ---
TOP_N_COINS       = 100      # En hacimli koinler
INTERVAL          = "1h"
MIN_SCORE         = 4        # v3 kazananı
MAX_OPEN          = 3
MAX_SAME_DIR      = 2
MARGIN_BY_SCORE   = {3: 1.0, 4: 2.0, 5: 3.0}
MAX_CANDLES       = 24       # 24 saat = 1 gün zaman aşımı
SCAN_INTERVAL_SEC = 3600     # Her saatin sonunda tarama (1h mum)
PRICE_POLL_SEC    = 60       # TP/SL kontrolü her dakika

# Canlı bot için rejim bayrakları (v3: ikisi de aktif)
bakkal_signal.ENABLE_TREND_REGIME = True
bakkal_signal.ENABLE_RANGE_REGIME = True

# --- ML beyin ---
ML_MODEL_PATH  = Path("ml/out/model.pkl")
ML_TRAIN_PATH  = Path("ml/out/training_set.parquet")
ML_THRESHOLD   = 0.55              # P(WIN) >= bu → geç
ML_FEATURES    = [
    "adx", "rsi", "ema_diff_pct", "vol_ratio", "bb_pos",
    "atr_pct", "funding_rate", "hour", "dow", "direction",
]


def _extract_features(row: pd.Series, direction: int, ts: pd.Timestamp) -> Optional[list]:
    adx = row.get("adx"); rsi = row.get("rsi"); atr = row.get("atr")
    ema9 = row.get("ema9"); ema21 = row.get("ema21")
    close = row.get("close"); vol_ratio = row.get("vol_ratio")
    bb_up = row.get("bb_upper"); bb_lo = row.get("bb_lower"); bb_mid = row.get("bb_mid")
    funding = row.get("funding_rate", 0.0)
    if pd.isna(adx) or pd.isna(atr) or not close or close <= 0:
        return None
    bb_width = (bb_up - bb_lo) if (bb_up and bb_lo) else 0.0
    bb_pos = (close - bb_mid) / bb_width if bb_width > 0 else 0.0
    return [
        float(adx),
        float(rsi) if not pd.isna(rsi) else 50.0,
        float((ema9 - ema21) / close) if (ema9 and ema21) else 0.0,
        float(vol_ratio) if not pd.isna(vol_ratio) else 1.0,
        float(bb_pos),
        float(atr / close),
        float(funding) if funding is not None else 0.0,
        int(ts.hour),
        int(ts.dayofweek),
        int(direction),
    ]


@dataclass
class OpenPos:
    symbol: str
    side: str               # "BUY" / "SELL"
    direction: int          # +1 / -1
    entry_price: float
    qty: float
    tp_price: float
    sl_price: float
    atr: float
    score: int
    regime: str
    margin: float
    opened_at: float
    opened_candle: pd.Timestamp
    reasons: list = field(default_factory=list)


class BakkalLiveBot:
    def __init__(self):
        self.rest = BinanceRest(
            CONFIG.BINANCE_API_KEY, CONFIG.BINANCE_API_SECRET, testnet=False
        )
        self.loader = BacktestDataLoader(self.rest)
        self.orders = OrderManager(
            CONFIG.BINANCE_API_KEY, CONFIG.BINANCE_API_SECRET,
            testnet=False, paper=CONFIG.PAPER_TRADING,
        )
        self.risk = RiskManager()
        self.tg = TelegramBot(CONFIG.TELEGRAM_TOKEN, CONFIG.TELEGRAM_CHAT_ID)
        self._setup_telegram()

        self.universe_symbols: list = []
        self.open_positions: Dict[str, OpenPos] = {}
        self.traded_today: Dict[str, str] = {}   # symbol -> YYYY-MM-DD
        self.last_scan_day: int = -1
        self.last_scan_hour: int = -1

        # ML beyin
        self.ml_model = None
        self.ml_features = ML_FEATURES
        self.training_set: Optional[pd.DataFrame] = None
        self._load_brain()
        # Açık pozisyonların feature snapshot'ı — kapanışta dataset'e eklemek için
        self._pending_samples: Dict[str, dict] = {}

        # günlük istatistik
        self._daily_trades = 0
        self._daily_wins = 0
        self._daily_pnl = 0.0
        self._daily_fees = 0.0

        self._running = False

    # --- ML beyin ---
    def _load_brain(self):
        try:
            if ML_MODEL_PATH.exists():
                with open(ML_MODEL_PATH, "rb") as f:
                    data = pickle.load(f)
                self.ml_model = data["model"]
                self.ml_features = data.get("features", ML_FEATURES)
                logger.info(f"ML beyin yüklendi: {ML_MODEL_PATH}")
            if ML_TRAIN_PATH.exists():
                self.training_set = pd.read_parquet(ML_TRAIN_PATH)
                logger.info(f"Eğitim seti: {len(self.training_set)} örnek")
        except Exception as e:
            logger.warning(f"Beyin yüklenemedi: {e}")
            self.ml_model = None

    def _ml_predict(self, features: list) -> float:
        if self.ml_model is None:
            return 1.0  # model yok → her şeyi geçir (bakkal skoru filtreler)
        try:
            import numpy as _np
            X = _np.array([features], dtype=float)
            return float(self.ml_model.predict_proba(X)[0, 1])
        except Exception as e:
            logger.debug(f"ML tahmin hatası: {e}")
            return 1.0

    def _retrain_brain(self):
        if self.training_set is None or len(self.training_set) < 200:
            return
        if self.training_set["label"].nunique() < 2:
            return
        try:
            from sklearn.ensemble import HistGradientBoostingClassifier
            X = self.training_set[self.ml_features].values
            y = self.training_set["label"].values
            model = HistGradientBoostingClassifier(
                max_iter=400, max_depth=6, learning_rate=0.05,
                min_samples_leaf=30, random_state=42,
            )
            model.fit(X, y)
            self.ml_model = model
            ML_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(ML_MODEL_PATH, "wb") as f:
                pickle.dump({"model": model, "features": self.ml_features}, f)
            self.training_set.to_parquet(ML_TRAIN_PATH)
            logger.info(f"Beyin yenilendi: {len(self.training_set)} örnek")
        except Exception as e:
            logger.error(f"Retrain hatası: {e}")

    # --- Telegram handlers ---
    def _setup_telegram(self):
        self.tg.on_stop(self._handle_stop)
        self.tg.on_status(self._handle_status)
        self.tg.on_stats(self._handle_stats)
        self.tg.on_pause(self._handle_pause)

    async def _handle_stop(self):
        self._running = False
        await self.tg.send("🛑 Bot durduruluyor — açık pozisyonlar korunuyor.")

    async def _handle_status(self) -> str:
        if not self.open_positions:
            return (
                f"<b>Açık Pozisyon:</b> Yok\n"
                f"<b>Bugün İşlem:</b> {self._daily_trades}\n"
                f"<b>Bugün Net PnL:</b> {self._daily_pnl - self._daily_fees:+.4f} USDT\n"
                f"<b>Bakiye:</b> {self.risk.state.account_balance:.4f} USDT"
            )
        lines = [f"<b>Açık Pozisyon: {len(self.open_positions)}/3</b>"]
        for p in self.open_positions.values():
            d = "LONG" if p.direction == 1 else "SHORT"
            lines.append(
                f"• {p.symbol} {d} @ {p.entry_price:.6g} "
                f"TP:{p.tp_price:.6g} SL:{p.sl_price:.6g} "
                f"[{p.regime} s{p.score}]"
            )
        lines.append(f"\n<b>Bugün:</b> {self._daily_trades} işlem | "
                     f"Net {self._daily_pnl - self._daily_fees:+.4f} USDT")
        return "\n".join(lines)

    async def _handle_stats(self, full: bool = False) -> str:
        wr = (self._daily_wins / self._daily_trades * 100) if self._daily_trades > 0 else 0
        return (
            f"📊 <b>BAKKAL İSTATİSTİK</b>\n"
            f"Koin evreni: {len(self.universe_symbols)}\n"
            f"Açık poz: {len(self.open_positions)}/3\n"
            f"Bugün işlem: {self._daily_trades} | Win: %{wr:.1f}\n"
            f"Gross: {self._daily_pnl:+.4f} | Fee: -{self._daily_fees:.4f}\n"
            f"<b>Net: {self._daily_pnl - self._daily_fees:+.4f} USDT</b>\n"
            f"Bakiye: {self.risk.state.account_balance:.4f} USDT"
        )

    async def _handle_pause(self, minutes: int):
        self.risk.state.paused_until = time.time() + minutes * 60
        await self.tg.send(f"⏸ {minutes} dakika duraklatıldı.")

    # --- Veri ---
    async def _fetch_recent_klines(self, symbol: str, limit: int = 300) -> Optional[pd.DataFrame]:
        """Cache'siz, en güncel kline verisini çek."""
        try:
            raw = await self.rest._get(
                "/fapi/v1/klines",
                {"symbol": symbol, "interval": INTERVAL, "limit": limit},
            )
            if not raw:
                return None
            df = pd.DataFrame(raw, columns=[
                "open_time", "open", "high", "low", "close", "volume",
                "close_time", "quote_volume", "trades",
                "taker_buy_base", "taker_buy_quote", "ignore",
            ])
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
            df.set_index("open_time", inplace=True)
            for c in ["open", "high", "low", "close", "volume"]:
                df[c] = df[c].astype(float)
            # Son mum henüz kapanmamış olabilir — at
            return df.iloc[:-1]
        except Exception as e:
            logger.debug(f"{symbol} kline hatası: {e}")
            return None

    async def _load_recent_data(self, symbol: str) -> Optional[pd.DataFrame]:
        """Son 300 mum (1h) + funding — canlı sinyal için."""
        try:
            klines = await self._fetch_recent_klines(symbol, limit=300)
            if klines is None or len(klines) < 50:
                return None
            try:
                fdf = await self.loader.load_funding_rates(symbol, days=15)
                funding = fdf["fundingRate"] if "fundingRate" in fdf.columns else None
            except Exception:
                funding = None

            df = compute_indicators(klines)
            if funding is not None and not funding.empty:
                try:
                    funding.index = pd.to_datetime(funding.index)
                    if funding.index.tz is not None:
                        funding.index = funding.index.tz_localize(None)
                    funding = funding[~funding.index.duplicated()].sort_index()
                    df["funding_rate"] = funding.reindex(df.index, method="ffill").fillna(0.0)
                except Exception:
                    df["funding_rate"] = 0.0
            else:
                df["funding_rate"] = 0.0
            return df
        except Exception as e:
            logger.debug(f"{symbol} veri hatası: {e}")
            return None

    async def _current_price(self, symbol: str) -> Optional[float]:
        try:
            data = await self.rest._get("/fapi/v1/ticker/price", {"symbol": symbol})
            return float(data["price"])
        except Exception as e:
            logger.debug(f"{symbol} fiyat alınamadı: {e}")
            return None

    # --- Tarama ---
    async def _scan_and_open(self):
        now_utc = datetime.now(timezone.utc)

        # Gün dönüşü
        if now_utc.day != self.last_scan_day:
            if self.last_scan_day != -1:
                await self.tg.notify_daily_summary(
                    self._daily_trades, self._daily_wins,
                    self._daily_pnl, self._daily_fees,
                    self._daily_pnl - self._daily_fees,
                )
            self._daily_trades = 0
            self._daily_wins = 0
            self._daily_pnl = 0.0
            self._daily_fees = 0.0
            self.traded_today.clear()
            self.risk.reset_daily()
            self.last_scan_day = now_utc.day
            # Beyni yeniden eğit (dün gelen örneklerle)
            self._retrain_brain()

        # Risk kontrolü
        can, _ = self.risk.can_trade()
        if not can:
            return

        # Kapasite
        if len(self.open_positions) >= MAX_OPEN:
            return

        today_key = now_utc.strftime("%Y-%m-%d")
        candidates = []

        for sym in self.universe_symbols:
            if sym in self.open_positions:
                continue
            if self.traded_today.get(sym) == today_key:
                continue

            df = await self._load_recent_data(sym)
            if df is None or len(df) < 2:
                continue

            last = df.iloc[-1]
            funding = float(last.get("funding_rate", 0.0))
            sig = evaluate_row(last, funding_rate=funding)

            if sig.direction == 0 or sig.score < MIN_SCORE:
                continue

            # ML filtre
            feats = _extract_features(last, sig.direction, df.index[-1])
            if feats is None:
                continue
            prob = self._ml_predict(feats)
            if prob < ML_THRESHOLD:
                continue

            candidates.append((sym, sig, float(last["close"]), feats, prob))

            await asyncio.sleep(0.05)  # rate-limit nezaketi

        # ML olasılığına göre sırala, en güvenli sinyalleri önce aç
        candidates.sort(key=lambda x: x[4], reverse=True)

        for sym, sig, price, feats, prob in candidates:
            if len(self.open_positions) >= MAX_OPEN:
                break
            same_dir = sum(1 for p in self.open_positions.values()
                           if p.direction == sig.direction)
            if same_dir >= MAX_SAME_DIR:
                continue
            await self._open_position(sym, sig, price, today_key, feats, prob)

    async def _open_position(self, symbol: str, sig: BakkalSignal,
                             price: float, today_key: str,
                             feats: Optional[list] = None,
                             prob: Optional[float] = None):
        margin = MARGIN_BY_SCORE.get(sig.score, 1.0)
        notional = margin * CONFIG.LEVERAGE
        qty = notional / price
        side = "BUY" if sig.direction == 1 else "SELL"

        try:
            order = await self.orders.open_position(symbol, side, margin, price)
        except Exception as e:
            logger.error(f"{symbol} pozisyon açılamadı: {e}")
            return

        pos = OpenPos(
            symbol=symbol, side=side, direction=sig.direction,
            entry_price=order.price, qty=order.qty,
            tp_price=sig.tp_price, sl_price=sig.sl_price,
            atr=sig.atr, score=sig.score, regime=sig.regime,
            margin=margin, opened_at=time.time(),
            opened_candle=pd.Timestamp.utcnow().floor("h"),
            reasons=sig.reasons,
        )
        self.open_positions[symbol] = pos
        self.traded_today[symbol] = today_key

        # Kapanışta dataset'e eklemek için feature snapshot
        if feats is not None:
            self._pending_samples[symbol] = {
                "features": feats,
                "timestamp": pd.Timestamp.utcnow(),
                "bakkal_score": sig.score,
                "symbol": symbol,
            }

        mode = "[PAPER]" if CONFIG.PAPER_TRADING else ""
        d = "LONG" if sig.direction == 1 else "SHORT"
        emoji = "🟢" if sig.direction == 1 else "🔴"
        reasons_text = " | ".join(sig.reasons[:3])
        prob_text = f" | P(WIN)={prob:.2f}" if prob is not None else ""
        await self.tg.send(
            f"{emoji} <b>{mode} AÇILDI — {symbol}</b>\n"
            f"<b>{d}</b> @ {order.price:.6g}\n"
            f"TP: {sig.tp_price:.6g}  |  SL: {sig.sl_price:.6g}\n"
            f"ATR: {sig.atr:.6g} | Notional: ${notional:.2f} | {CONFIG.LEVERAGE}x\n"
            f"Rejim: <b>{sig.regime}</b> | Skor: <b>{sig.score}/5</b>{prob_text}\n"
            f"<i>{reasons_text}</i>"
        )

    # --- Açık pozisyon yönetimi ---
    async def _manage_open_positions(self):
        if not self.open_positions:
            return

        for sym in list(self.open_positions.keys()):
            pos = self.open_positions[sym]
            price = await self._current_price(sym)
            if price is None:
                continue

            reason = None
            # TP / SL
            if pos.direction == 1:
                if price >= pos.tp_price:
                    reason = "TP"
                elif price <= pos.sl_price:
                    reason = "SL"
            else:
                if price <= pos.tp_price:
                    reason = "TP"
                elif price >= pos.sl_price:
                    reason = "SL"

            # Zaman aşımı
            if reason is None:
                elapsed_h = (time.time() - pos.opened_at) / 3600.0
                if elapsed_h >= MAX_CANDLES:
                    reason = "TIMEOUT"

            # Soft exit — son 1h mum üzerinden yön ters mi dönmüş?
            if reason is None:
                df = await self._load_recent_data(sym)
                if df is not None and len(df) > 0:
                    if direction_flipped(pos.direction, df.iloc[-1]):
                        reason = "FLIP"

            if reason:
                await self._close_position(pos, price, reason)

    async def _close_position(self, pos: OpenPos, price: float, reason: str):
        try:
            await self.orders.close_position_market(pos.symbol, pos.side, pos.qty)
        except Exception as e:
            logger.error(f"{pos.symbol} kapatılamadı: {e}")

        if pos.direction == 1:
            pnl = (price - pos.entry_price) * pos.qty
        else:
            pnl = (pos.entry_price - price) * pos.qty

        notional_entry = pos.entry_price * pos.qty
        notional_exit = price * pos.qty
        fee = (notional_entry + notional_exit) * 0.0004   # taker-ish yaklaşım
        net = pnl - fee
        pnl_pct = pnl / pos.margin if pos.margin > 0 else 0.0

        self._daily_trades += 1
        self._daily_pnl += pnl
        self._daily_fees += fee
        if net > 0:
            self._daily_wins += 1
        self.risk.record_trade(net)

        # Eğitim seti güncelleme
        sample = self._pending_samples.pop(pos.symbol, None)
        if sample is not None and self.training_set is not None:
            row = {f: v for f, v in zip(self.ml_features, sample["features"])}
            row["label"] = 1 if net > 0 else 0
            row["timestamp"] = sample["timestamp"]
            row["symbol"] = sample["symbol"]
            row["bakkal_score"] = sample["bakkal_score"]
            self.training_set = pd.concat(
                [self.training_set, pd.DataFrame([row])], ignore_index=True,
            )

        del self.open_positions[pos.symbol]

        emoji = {"TP": "🎯", "SL": "❌", "TIMEOUT": "⏱", "FLIP": "🔄"}.get(reason, "◽")
        mode = "[PAPER]" if CONFIG.PAPER_TRADING else ""
        duration = int((time.time() - pos.opened_at) / 60)
        sign = "+" if net >= 0 else ""
        d = "LONG" if pos.direction == 1 else "SHORT"
        await self.tg.send(
            f"{emoji} <b>{mode} {reason} — {pos.symbol}</b>\n"
            f"{d} kapandı @ {price:.6g}\n"
            f"Net PnL: <b>{sign}{net:.4f} USDT</b> ({sign}{pnl_pct*100:.2f}%)\n"
            f"Fee: -{fee:.4f} | Süre: {duration} dk\n"
            f"Bugün: {self._daily_trades} işlem | "
            f"Net {self._daily_pnl - self._daily_fees:+.4f}"
        )

    # --- Döngüler ---
    async def _scan_loop(self):
        """Her yeni saat kapanışında tara."""
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                # Yeni saat mi?
                if now.hour != self.last_scan_hour:
                    self.last_scan_hour = now.hour
                    # 1h mum kapanışını sindirsin
                    await asyncio.sleep(10)
                    await self._scan_and_open()
                await asyncio.sleep(30)
            except Exception as e:
                logger.error(f"Scan loop hatası: {e}")
                await asyncio.sleep(30)

    async def _manage_loop(self):
        while self._running:
            try:
                await self._manage_open_positions()
                await asyncio.sleep(PRICE_POLL_SEC)
            except Exception as e:
                logger.error(f"Manage loop hatası: {e}")
                await asyncio.sleep(PRICE_POLL_SEC)

    async def run(self):
        self._running = True
        self.last_scan_day = datetime.now(timezone.utc).day
        self.risk.state.account_balance = 100.0   # paper başlangıç

        # Koin evreni
        universe = await load_coin_universe(self.rest)
        self.universe_symbols = [c.symbol for c in universe[:TOP_N_COINS]]

        mode = "PAPER TRADING" if CONFIG.PAPER_TRADING else "CANLI TRADING"
        await self.tg.send(
            f"🚀 <b>BAKKAL v3 BAŞLADI</b>\n"
            f"Mod: <b>{mode}</b>\n"
            f"Evren: {len(self.universe_symbols)} koin (top {TOP_N_COINS})\n"
            f"Sinyal: TREND+RANGE (ADX adaptif), min skor: {MIN_SCORE}\n"
            f"Maks açık: {MAX_OPEN} | Aynı yön: {MAX_SAME_DIR}\n"
            f"TP/SL: ATR-bazlı (1.5/1.0×)\n"
            f"Komutlar: /status /stats /stop /pause N"
        )

        await asyncio.gather(
            self._scan_loop(),
            self._manage_loop(),
            self.tg.start_polling(),
        )

    async def shutdown(self):
        try:
            await self.rest.close()
        except Exception:
            pass
        try:
            await self.orders.close()
        except Exception:
            pass
        try:
            await self.tg.stop()
        except Exception:
            pass


async def main():
    bot = BakkalLiveBot()
    try:
        await bot.run()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot kapatılıyor...")
    finally:
        await bot.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
