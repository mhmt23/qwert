"""
Bakkal Stratejisi — Multi-Coin Portföy Backtest Motoru.

Kurallar:
  - Max 3 pozisyon aynı anda açık
  - Aynı koin günde 1 işlem
  - 3 pozisyon aynı yönde olamaz (korelasyon koruması)
  - Pozisyon boyutu skor bazlı: 3p → $1, 4p → $2, 5p → $3 margin
  - TP/SL ATR bazlı (her koine uyumlu)
  - Soft exit: yön sinyali ters döndüğünde anında kapat
  - Timeout: 24 mum (1 gün)

Look-ahead önleme:
  - Tüm göstergeler .shift(1)
  - Giriş bir sonraki mumun AÇILIŞ fiyatından
  - TP/SL aynı mumda ise SL kazanır (kötümser)
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from loguru import logger

from config import CONFIG
from strategy.bakkal_signal import (
    compute_indicators,
    evaluate_row,
    direction_flipped,
    BakkalSignal,
)


@dataclass
class BakkalTrade:
    symbol: str
    open_ts: pd.Timestamp
    close_ts: pd.Timestamp
    side: int
    entry_price: float
    close_price: float
    qty: float
    margin: float
    notional: float
    pnl_usd: float
    pnl_pct: float
    close_reason: str
    score: int
    regime: str
    duration_candles: int


@dataclass
class OpenPosition:
    symbol: str
    open_idx: int
    open_ts: pd.Timestamp
    side: int
    entry_price: float
    qty: float
    margin: float
    tp_price: float
    sl_price: float
    score: int
    regime: str
    candles_in_trade: int = 0


@dataclass
class BakkalParams:
    leverage: int = 5
    maker_fee: float = 0.0002
    min_score: int = 3
    max_open_positions: int = 3
    max_candles_in_trade: int = 24     # 1h × 24 = 1 gün timeout
    # Skor → margin mapping (bakiye oranı değil, sabit dolar miktar)
    margin_by_score: Dict[int, float] = field(default_factory=lambda: {3: 1.0, 4: 2.0, 5: 3.0})
    # Günlük risk koruması
    max_daily_loss_pct: float = 0.05       # Bakiyenin %5'i
    max_consecutive_losses: int = 3        # Art arda 3 kayıp → 2 saat pause
    pause_hours_after_streak: int = 2


class BakkalBacktestEngine:
    def __init__(self, params: BakkalParams = None):
        self.params = params or BakkalParams()

    # ---------------- Sinyal hazırlama ----------------

    def prepare_coin_data(
        self, klines: pd.DataFrame, funding: pd.Series = None
    ) -> pd.DataFrame:
        """
        Bir koin için OHLCV + funding verisini göstergelerle zenginleştir.
        """
        # Index sanitize: tz yok, dedup, sıralı
        klines = klines.copy()
        klines.index = pd.to_datetime(klines.index).tz_localize(None) \
            if klines.index.tz is not None else pd.to_datetime(klines.index)
        klines = klines[~klines.index.duplicated()].sort_index()

        df = compute_indicators(klines)

        if funding is not None and not funding.empty:
            f = funding.copy()
            f.index = pd.to_datetime(f.index).tz_localize(None) \
                if f.index.tz is not None else pd.to_datetime(f.index)
            f = f[~f.index.duplicated()].sort_index()
            f_reindexed = f.reindex(df.index, method="ffill").fillna(0.0)
            df["funding_rate"] = f_reindexed
        else:
            df["funding_rate"] = 0.0

        if "cvd" in klines.columns:
            df["cvd"] = klines["cvd"]
        else:
            df["cvd"] = 0.0
        return df

    # ---------------- Ana backtest döngüsü ----------------

    def run(
        self,
        coin_data: Dict[str, pd.DataFrame],
        initial_balance: float = 100.0,
    ) -> Dict:
        """
        Tüm koinleri paralel olarak simüle eder.
        coin_data = {"BTCUSDT": df_with_indicators, "ETHUSDT": df_with_indicators, ...}
        Tüm df'lerin aynı zaman indeksinde olması gerekmez; senkronize edilir.
        """
        p = self.params

        # Tüm zaman indekslerinin birleşimini al (master time axis)
        all_indices = set()
        for df in coin_data.values():
            all_indices.update(df.index)
        master_index: List[pd.Timestamp] = sorted(all_indices)

        if not master_index:
            return {"trades": [], "equity_curve": [initial_balance], "final_balance": initial_balance}

        # Durum
        balance = initial_balance
        peak_balance = initial_balance
        equity_curve = [balance]
        open_positions: List[OpenPosition] = []
        trades: List[BakkalTrade] = []

        # Günlük takip
        traded_today: Dict[str, pd.Timestamp] = {}  # symbol → tarih (günlük 1 işlem kuralı)
        daily_loss = 0.0
        last_day: Optional[pd.Timestamp] = None
        consecutive_losses = 0
        paused_until: Optional[pd.Timestamp] = None

        # Her koin için satır indeksi tutucu (performans için)
        coin_iters: Dict[str, Dict[pd.Timestamp, int]] = {}
        for sym, df in coin_data.items():
            coin_iters[sym] = {ts: i for i, ts in enumerate(df.index)}

        for ts in master_index:
            # Günlük sıfırlama
            current_day = pd.Timestamp(ts.date())
            if last_day is None or current_day != last_day:
                daily_loss = 0.0
                last_day = current_day
                # traded_today'i günlük temizle
                traded_today = {k: v for k, v in traded_today.items()
                                if pd.Timestamp(v.date()) == current_day}

            # ---------------- 1) Açık pozisyonları kontrol et ----------------
            still_open: List[OpenPosition] = []
            for pos in open_positions:
                df = coin_data[pos.symbol]
                idx = coin_iters[pos.symbol].get(ts)
                if idx is None:
                    # Bu timestamp'te koin verisi yok → bir sonraki ts'i bekle
                    still_open.append(pos)
                    continue

                row = df.iloc[idx]
                high = row["high"]
                low = row["low"]
                close = row["close"]
                pos.candles_in_trade += 1

                exit_price = None
                reason = None

                if pos.side == 1:
                    tp_hit = high >= pos.tp_price
                    sl_hit = low <= pos.sl_price
                else:
                    tp_hit = low <= pos.tp_price
                    sl_hit = high >= pos.sl_price

                if tp_hit and sl_hit:
                    # Aynı mum: kötümser (SL)
                    reason = "SL"
                    exit_price = pos.sl_price
                elif tp_hit:
                    reason = "TP"
                    exit_price = pos.tp_price
                elif sl_hit:
                    reason = "SL"
                    exit_price = pos.sl_price
                elif direction_flipped(pos.side, row):
                    reason = "SOFT_EXIT"
                    exit_price = close
                elif pos.candles_in_trade >= p.max_candles_in_trade:
                    reason = "TIMEOUT"
                    exit_price = close

                if reason:
                    gross = (exit_price - pos.entry_price) * pos.qty * pos.side
                    fee = pos.entry_price * pos.qty * p.maker_fee * 2  # giriş+çıkış
                    net = gross - fee
                    pnl_pct = net / pos.margin

                    balance += net
                    daily_loss += min(0.0, net)
                    peak_balance = max(peak_balance, balance)

                    if net > 0:
                        consecutive_losses = 0
                    else:
                        consecutive_losses += 1
                        if consecutive_losses >= p.max_consecutive_losses:
                            paused_until = ts + pd.Timedelta(hours=p.pause_hours_after_streak)

                    trades.append(BakkalTrade(
                        symbol=pos.symbol,
                        open_ts=pos.open_ts,
                        close_ts=ts,
                        side=pos.side,
                        entry_price=pos.entry_price,
                        close_price=exit_price,
                        qty=pos.qty,
                        margin=pos.margin,
                        notional=pos.margin * p.leverage,
                        pnl_usd=net,
                        pnl_pct=pnl_pct,
                        close_reason=reason,
                        score=pos.score,
                        regime=pos.regime,
                        duration_candles=pos.candles_in_trade,
                    ))
                    equity_curve.append(balance)
                else:
                    still_open.append(pos)

            open_positions = still_open

            # ---------------- 2) Yeni işlem koşulları ----------------
            # Günlük zarar limiti?
            daily_loss_pct = abs(daily_loss) / max(initial_balance, 1e-9)
            if daily_loss_pct >= p.max_daily_loss_pct:
                continue
            # Pause süresi geçmedi mi?
            if paused_until is not None and ts < paused_until:
                continue
            # Zaten max pozisyon?
            if len(open_positions) >= p.max_open_positions:
                continue

            # ---------------- 3) Tüm koinleri tara, sinyal topla ----------------
            candidates: List[Tuple[str, BakkalSignal, float]] = []
            open_symbols = {po.symbol for po in open_positions}
            open_directions = [po.side for po in open_positions]

            for sym, df in coin_data.items():
                if sym in open_symbols:
                    continue
                # Bu koinde bugün zaten işlem var mı?
                if sym in traded_today:
                    if pd.Timestamp(traded_today[sym].date()) == current_day:
                        continue

                idx = coin_iters[sym].get(ts)
                if idx is None or idx >= len(df) - 1:
                    continue  # geçerli satır yok veya sonraki mum yok (giriş için)

                row = df.iloc[idx]
                funding_rate = float(row.get("funding_rate", 0.0))
                cvd_pos = None
                if "cvd" in row and not pd.isna(row["cvd"]):
                    cvd_pos = row["cvd"] > 0 if row["cvd"] != 0 else None

                sig = evaluate_row(row, funding_rate=funding_rate, cvd_positive=cvd_pos)
                if sig.direction == 0 or sig.score < p.min_score:
                    continue

                # Korelasyon filtresi: 2 pozisyon aynı yönde açıksa aynı yöne yenisi yasak
                same_side_count = sum(1 for d in open_directions if d == sig.direction)
                if same_side_count >= 2:
                    continue

                # Giriş fiyatı: bir sonraki mumun açılışı
                next_open_price = df["open"].iloc[idx + 1]
                candidates.append((sym, sig, next_open_price))

            if not candidates:
                continue

            # En yüksek skorluları önce al
            candidates.sort(key=lambda x: x[1].score, reverse=True)

            # ---------------- 4) Pozisyon aç ----------------
            slots_left = p.max_open_positions - len(open_positions)
            for sym, sig, next_open_price in candidates[:slots_left]:
                # Korelasyon tekrar kontrol (aç aç derken değişmiş olabilir)
                same_side_count = sum(1 for d in [po.side for po in open_positions]
                                      if d == sig.direction)
                if same_side_count >= 2:
                    continue

                margin = p.margin_by_score.get(sig.score, 1.0)
                if margin > balance * 0.10:  # bakiyenin %10'unu asla tek işlemde riske atma
                    margin = balance * 0.10
                if margin < 0.5:  # çok küçükse atla
                    continue

                notional = margin * p.leverage
                qty = notional / next_open_price

                # TP/SL fiyatları - next_open_price'a göre tekrar hesapla
                if sig.direction == 1:
                    tp = next_open_price + 1.5 * sig.atr
                    sl = next_open_price - 1.0 * sig.atr
                else:
                    tp = next_open_price - 1.5 * sig.atr
                    sl = next_open_price + 1.0 * sig.atr

                open_positions.append(OpenPosition(
                    symbol=sym,
                    open_idx=coin_iters[sym][ts] + 1,
                    open_ts=ts,
                    side=sig.direction,
                    entry_price=next_open_price,
                    qty=qty,
                    margin=margin,
                    tp_price=tp,
                    sl_price=sl,
                    score=sig.score,
                    regime=sig.regime,
                ))
                traded_today[sym] = ts
                open_directions.append(sig.direction)

        # Sonuç özeti
        return {
            "trades": trades,
            "equity_curve": equity_curve,
            "final_balance": balance,
            "peak_balance": peak_balance,
            "initial_balance": initial_balance,
        }

    # ---------------- Walk-forward ----------------

    def walk_forward(
        self,
        coin_data: Dict[str, pd.DataFrame],
        initial_balance: float = 100.0,
        n_splits: int = 4,
        train_ratio: float = 0.70,
    ) -> Dict:
        """
        Walk-forward doğrulama.
        Her dilimde train kısmı sadece gözlem için, test kısmı gerçek OOS sonuç.
        Parametre optimizasyonu şimdilik sabit — min_score 3, margin mapping sabit.
        Gelecek versiyon: train'de min_score 2,3,4 optimize edilebilir.
        """
        # Master zaman ekseni
        all_indices = set()
        for df in coin_data.values():
            all_indices.update(df.index)
        master_index = sorted(all_indices)
        total_len = len(master_index)
        slice_size = total_len // n_splits

        all_oos_trades: List[BakkalTrade] = []
        oos_equity = [initial_balance]
        running_balance = initial_balance

        logger.info(f"Walk-forward başlıyor: {n_splits} dilim, {len(coin_data)} koin")

        for fold in range(n_splits):
            start = fold * slice_size
            end = start + slice_size if fold < n_splits - 1 else total_len
            ts_start = master_index[start]
            ts_end = master_index[end - 1]
            train_end_idx = start + int((end - start) * train_ratio)
            ts_train_end = master_index[train_end_idx]

            # Test dilimini her koin için ilgili aralığa kes
            test_data = {}
            for sym, df in coin_data.items():
                sub = df.loc[ts_train_end:ts_end]
                if len(sub) > 50:
                    test_data[sym] = sub

            logger.info(f"Fold {fold+1}/{n_splits} | OOS: {ts_train_end} → {ts_end} | {len(test_data)} koin")

            res = self.run(test_data, initial_balance=running_balance)
            all_oos_trades.extend(res["trades"])
            running_balance = res["final_balance"]
            oos_equity.extend(res["equity_curve"][1:])

            wins = sum(1 for t in res["trades"] if t.pnl_usd > 0)
            pnl = sum(t.pnl_usd for t in res["trades"])
            n = len(res["trades"])
            win_rate = wins / max(n, 1) * 100
            logger.info(
                f"Fold {fold+1} OOS: {n} işlem | Win=%{win_rate:.1f} | PnL=${pnl:.2f} | "
                f"Bakiye=${running_balance:.2f}"
            )

        return {
            "trades": all_oos_trades,
            "equity_curve": oos_equity,
            "final_balance": running_balance,
            "method": "walk_forward",
            "n_splits": n_splits,
        }
