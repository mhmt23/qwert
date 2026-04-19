from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple
import numpy as np
import pandas as pd
from loguru import logger

from config import CONFIG


@dataclass
class TradeRecord:
    open_idx: int
    close_idx: int
    side: int
    entry_price: float
    close_price: float
    qty: float
    pnl_usd: float
    pnl_pct: float
    close_reason: str
    confluence_score: int
    duration_candles: int


@dataclass
class BacktestParams:
    tp_pct: float = CONFIG.TAKE_PROFIT_PCT
    sl_pct: float = CONFIG.STOP_LOSS_PCT
    leverage: int = CONFIG.LEVERAGE
    position_size_pct: float = CONFIG.POSITION_SIZE_FULL
    min_confluence: int = CONFIG.MIN_CONFLUENCE_SCORE
    volume_spike_mult: float = CONFIG.VOLUME_SPIKE_MULTIPLIER
    volume_lookback: int = CONFIG.VOLUME_LOOKBACK
    max_candles: int = CONFIG.MAX_CANDLES_IN_TRADE
    maker_fee: float = 0.0002
    vix_max: float = CONFIG.VIX_PAUSE_THRESHOLD
    funding_high: float = CONFIG.FUNDING_RATE_HIGH
    funding_low: float = CONFIG.FUNDING_RATE_LOW


class BacktestEngine:
    def __init__(self, params: BacktestParams = None):
        self.params = params or BacktestParams()

    @staticmethod
    def _rsi(close: pd.Series, period: int = 6) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        dm_pos = (high - high.shift(1)).clip(lower=0)
        dm_neg = (low.shift(1) - low).clip(lower=0)
        dm_pos = dm_pos.where(dm_pos > dm_neg, 0)
        dm_neg = dm_neg.where(dm_neg > dm_pos, 0)
        atr   = tr.rolling(period).mean()
        di_pos = 100 * dm_pos.rolling(period).mean() / atr.replace(0, np.nan)
        di_neg = 100 * dm_neg.rolling(period).mean() / atr.replace(0, np.nan)
        dx  = 100 * (di_pos - di_neg).abs() / (di_pos + di_neg).replace(0, np.nan)
        return dx.rolling(period).mean()  # ADX

    def _compute_signals(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Sinyal hesaplama — v3: Bollinger Band + RSI Mean Reversion.

        Mantık: Fiyat aşırıya gittiğinde (BB dışına çıktığında) ve RSI
        aşırı bölgedeyse orta noktaya dönmeyi bekle.
        Kısa vadede trend takibinden çok daha yüksek win rate verir.

        KURAL: Her indikatör yalnızca KAPANMIŞ mumların verisini kullanır (.shift(1)).
        """
        p = self.params
        close = df["close"]

        # --- ADX: Trend gücü ---
        # ADX > 20 → trend var → trend following çalışır
        adx = self._adx(df, period=14).shift(1)
        trending_market = adx > 20

        # --- EMA Crossover (trend yönü) ---
        ema9  = close.ewm(span=9,  adjust=False).mean().shift(1)
        ema21 = close.ewm(span=21, adjust=False).mean().shift(1)
        ema_bull = ema9 > ema21
        ema_bear = ema9 < ema21

        # --- RSI(14) momentum filtresi ---
        rsi = self._rsi(close, period=14).shift(1)
        rsi_bull = rsi > 50   # momentum yukarı
        rsi_bear = rsi < 50   # momentum aşağı

        # --- RSI aşırı bölge filtresi (karşı trendle girme) ---
        not_overbought = rsi < 70  # long girişte aşırı alımda olma
        not_oversold   = rsi > 30  # short girişte aşırı satımda olma

        # --- Volume Spike ---
        vol_avg   = df["volume"].rolling(p.volume_lookback).mean().shift(1)
        vol_spike = df["volume"] >= vol_avg * p.volume_spike_mult

        # --- CVD ---
        cvd_pos = df["cvd"] > 0
        cvd_neg = df["cvd"] < 0

        # --- EMA crossover geçiş tespiti (sinyal üretici) ---
        # Yeni kesişme: önceki mum farklı taraftaydı
        prev_ema_bull = ema9.shift(1) > ema21.shift(1)
        fresh_bull_cross = ema_bull & ~prev_ema_bull   # yeni yukarı kesişme
        fresh_bear_cross = ema_bear & prev_ema_bull    # yeni aşağı kesişme

        # --- Makro / Funding ---
        nasdaq_ok = df.get("NDX_ABOVE_EMA50", pd.Series(1, index=df.index)).astype(bool)
        vix_ok    = df.get("VIX", pd.Series(20, index=df.index)) < p.vix_max
        funding   = df.get("funding_rate", pd.Series(0.0, index=df.index))
        funding_long_ok  = funding < p.funding_high
        funding_short_ok = funding > p.funding_low

        # --- Confluence (5 puan) ---
        long_score = (
            ema_bull.astype(int) +              # 1: EMA yönü
            fresh_bull_cross.astype(int) +      # 1: Taze kesişme (bonus)
            rsi_bull.astype(int) +              # 1: RSI momentum
            vol_spike.astype(int) +             # 1: Hacim onayı
            cvd_pos.astype(int)                 # 1: Alım baskısı
        )
        short_score = (
            ema_bear.astype(int) +
            fresh_bear_cross.astype(int) +
            (~rsi_bull).astype(int) +
            vol_spike.astype(int) +
            cvd_neg.astype(int)
        )

        # Trend piyasada, aşırı bölgede değilken gir
        long_ok  = (long_score >= p.min_confluence) & trending_market & not_overbought & vix_ok & funding_long_ok
        short_ok = (short_score >= p.min_confluence) & trending_market & not_oversold  & vix_ok & funding_short_ok

        signals = pd.Series(0, index=df.index)
        signals[long_ok  & (long_score  > short_score)] = 1
        signals[short_ok & (short_score > long_score)]  = -1

        return signals, long_score, short_score

    def run(self, df: pd.DataFrame, initial_balance: float = 100.0) -> Dict[str, Any]:
        """
        Backtest döngüsü.

        Look-ahead bias önlemleri:
        1. Sinyal i. mumda üretilir, giriş i+1. mumun AÇILIŞ fiyatından yapılır.
        2. Volume ortalaması .shift(1) ile önceki mumlara dayanır.
        3. TP ve SL aynı mumda birden tetiklenirse SL kazanır (kötümser yaklaşım).
        """
        p = self.params
        signals, long_scores, short_scores = self._compute_signals(df)

        opens = df["open"].values
        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        sig_arr = signals.values
        n = len(df)

        trades: List[TradeRecord] = []
        balance = initial_balance
        equity_curve = [balance]

        in_trade = False
        entry_idx = 0
        entry_price = 0.0
        trade_side = 0
        tp_price = 0.0
        sl_price = 0.0
        trade_qty = 0.0
        candles_in_trade = 0
        score = 0

        daily_loss = 0.0
        daily_trades = 0
        last_day = -1

        # i = sinyal mumu, giriş i+1'de olduğu için n-1'e kadar git
        i = p.volume_lookback + 5
        while i < n - 1:
            day_of_year = i // 1440
            if day_of_year != last_day:
                daily_loss = 0.0
                daily_trades = 0
                last_day = day_of_year

            if in_trade:
                # i. mumda TP/SL kontrolü
                candles_in_trade += 1
                close_reason = None
                exit_price = closes[i]

                if trade_side == 1:
                    tp_hit = highs[i] >= tp_price
                    sl_hit = lows[i] <= sl_price
                else:
                    tp_hit = lows[i] <= tp_price
                    sl_hit = highs[i] >= sl_price

                if tp_hit and sl_hit:
                    # Aynı mumda her ikisi de tetiklendi → SL varsay (kötümser)
                    close_reason = "SL"
                    exit_price = sl_price
                elif tp_hit:
                    close_reason = "TP"
                    exit_price = tp_price
                elif sl_hit:
                    close_reason = "SL"
                    exit_price = sl_price
                elif candles_in_trade >= p.max_candles:
                    close_reason = "TIMEOUT"
                    exit_price = closes[i]

                if close_reason:
                    gross = (exit_price - entry_price) * trade_qty * trade_side
                    fee = entry_price * trade_qty * p.maker_fee * 2
                    net = gross - fee
                    pnl_pct = net / (entry_price * trade_qty / p.leverage)

                    balance += net
                    daily_loss += min(0.0, net)

                    trades.append(TradeRecord(
                        open_idx=entry_idx,
                        close_idx=i,
                        side=trade_side,
                        entry_price=entry_price,
                        close_price=exit_price,
                        qty=trade_qty,
                        pnl_usd=net,
                        pnl_pct=pnl_pct,
                        close_reason=close_reason,
                        confluence_score=score,
                        duration_candles=candles_in_trade,
                    ))
                    in_trade = False
                    equity_curve.append(balance)

            else:
                daily_loss_pct = abs(daily_loss) / max(initial_balance, 1e-9)
                if (daily_loss_pct >= CONFIG.MAX_DAILY_LOSS_PCT or
                        daily_trades >= CONFIG.MAX_DAILY_TRADES):
                    i += 1
                    continue

                direction = sig_arr[i]
                if direction != 0:
                    # Giriş: bir sonraki mumun AÇILIŞ fiyatı (look-ahead yok)
                    next_open = opens[i + 1]
                    score = int(long_scores.iloc[i] if direction == 1 else short_scores.iloc[i])
                    size_pct = p.position_size_pct if score >= 5 else p.position_size_pct / 2
                    usdt_margin = balance * size_pct
                    trade_qty = (usdt_margin * p.leverage) / next_open
                    entry_price = next_open
                    trade_side = direction

                    if trade_side == 1:
                        tp_price = entry_price * (1 + p.tp_pct)
                        sl_price = entry_price * (1 - p.sl_pct)
                    else:
                        tp_price = entry_price * (1 - p.tp_pct)
                        sl_price = entry_price * (1 + p.sl_pct)

                    entry_idx = i + 1
                    candles_in_trade = 0
                    in_trade = True
                    daily_trades += 1
                    i += 1  # sinyal mumunu atla, giriş i+1'de zaten yapıldı

            i += 1

        return {
            "trades": trades,
            "equity_curve": equity_curve,
            "final_balance": balance,
            "params": p,
        }

    def walk_forward(
        self,
        df: pd.DataFrame,
        initial_balance: float = 100.0,
        n_splits: int = 5,
        train_ratio: float = 0.70,
    ) -> Dict[str, Any]:
        """
        Walk-forward test:
        - Veriyi N dilime böl
        - Her dilimde: %70 train (optimizasyon), %30 test (gerçek doğrulama)
        - Bir önceki dilimin en iyi parametreleri bir sonraki dilime uygulanır
        - Sonuç: sadece OUT-OF-SAMPLE performans raporlanır

        Bu yöntemle gelecek veriyi görmeden test edilmiş olur.
        """
        total_len = len(df)
        slice_size = total_len // n_splits
        all_oos_trades: List[TradeRecord] = []
        oos_equity = [initial_balance]
        running_balance = initial_balance

        logger.info(f"Walk-forward başlıyor: {n_splits} dilim, train=%{train_ratio*100:.0f}")

        best_params = self.params  # ilk dilim için varsayılan

        for fold in range(n_splits):
            start = fold * slice_size
            end = start + slice_size if fold < n_splits - 1 else total_len
            fold_df = df.iloc[start:end].copy()

            train_end = int(len(fold_df) * train_ratio)
            train_df = fold_df.iloc[:train_end]
            test_df = fold_df.iloc[train_end:]

            logger.info(
                f"Fold {fold+1}/{n_splits} | "
                f"Train: {len(train_df)} mum | Test: {len(test_df)} mum"
            )

            # Eğitim: en iyi parametreleri bul
            opt_results = self.optimize(train_df, initial_balance=running_balance)
            if opt_results:
                best = opt_results[0]
                best_params = BacktestParams(
                    tp_pct=best["tp_pct"],
                    sl_pct=best["sl_pct"],
                    min_confluence=best["min_confluence"],
                )
                logger.info(
                    f"Fold {fold+1} en iyi: "
                    f"TP={best['tp_pct']*100:.2f}% SL={best['sl_pct']*100:.2f}% "
                    f"Conf={best['min_confluence']} (train PnL=${best['total_pnl']:.2f})"
                )

            # Test: eğitim dışı veriye uygula
            self.params = best_params
            oos_result = self.run(test_df, initial_balance=running_balance)
            oos_trades = oos_result["trades"]
            all_oos_trades.extend(oos_trades)
            running_balance = oos_result["final_balance"]
            oos_equity.extend(oos_result["equity_curve"][1:])

            oos_win = sum(1 for t in oos_trades if t.pnl_usd > 0)
            oos_pnl = sum(t.pnl_usd for t in oos_trades)
            logger.info(
                f"Fold {fold+1} OOS: {len(oos_trades)} işlem | "
                f"Win=%{oos_win/max(len(oos_trades),1)*100:.1f} | PnL=${oos_pnl:.2f}"
            )

        return {
            "trades": all_oos_trades,
            "equity_curve": oos_equity,
            "final_balance": running_balance,
            "params": best_params,
            "method": "walk_forward",
            "n_splits": n_splits,
        }

    def optimize(self, df: pd.DataFrame, initial_balance: float = 100.0) -> List[Dict]:
        results = []
        tp_range   = [0.015, 0.020, 0.030]   # 1h için daha geniş hedef
        sl_range   = [0.008, 0.010, 0.015]
        conf_range = [3, 4]

        for tp in tp_range:
            for sl in sl_range:
                for conf in conf_range:
                    self.params = BacktestParams(tp_pct=tp, sl_pct=sl, min_confluence=conf)
                    res = self.run(df, initial_balance)
                    trades = res["trades"]
                    if not trades:
                        continue
                    winners = [t for t in trades if t.pnl_usd > 0]
                    results.append({
                        "tp_pct": tp,
                        "sl_pct": sl,
                        "min_confluence": conf,
                        "total_trades": len(trades),
                        "win_rate": len(winners) / len(trades),
                        "total_pnl": sum(t.pnl_usd for t in trades),
                        "final_balance": res["final_balance"],
                    })

        results.sort(key=lambda x: x["total_pnl"], reverse=True)
        return results
