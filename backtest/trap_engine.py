"""
Trap Strategy Backtest Motoru.

Strateji bileşenleri:
  A) Funding Rate Extreme   → squeeze yönü belirler
  B) OI Spike               → kaldıraçlı pozisyon birikimi
  C) Likidasyon Zonu Avı    → pivot sweep + geri dönüş
  D) 1h EMA Trend Filtresi  → ema9 > ema21 → LONG, < → SHORT

Kural:
  A + B + D uyuşuyorsa giriş (3 puan minimum).
  C bonus puan verir (4. bileşen).
  Giriş bir sonraki mumun AÇILIŞ fiyatından yapılır (look-ahead yok).
  TP/SL aynı mumda tetiklenirse SL kazanır (kötümser).
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Tuple
import numpy as np
import pandas as pd
from loguru import logger

from config import CONFIG


# Funding thresholds
FUNDING_HIGH  =  0.001    # > +0.10% → longs over-leveraged → SHORT
FUNDING_LOW   = -0.0005   # < -0.05% → shorts over-leveraged → LONG
OI_SPIKE_THR  =  0.01     # OI % değişim eşiği
PIVOT_N       =  20        # Pivot high/low hesaplaması için kaç mum


@dataclass
class TrapTradeRecord:
    open_idx: int
    close_idx: int
    symbol: str
    side: int
    entry_price: float
    close_price: float
    qty: float
    pnl_usd: float
    pnl_pct: float
    close_reason: str
    trap_score: int
    duration_candles: int


@dataclass
class TrapBacktestParams:
    tp_pct: float = 0.020
    sl_pct: float = 0.010
    leverage: int = CONFIG.LEVERAGE
    position_size_pct: float = CONFIG.POSITION_SIZE_FULL
    maker_fee: float = 0.0002
    max_candles: int = 48    # 1h verisi → 48 saat = 2 gün max hold
    min_score: int = 3


class TrapBacktestEngine:
    def __init__(self, params: TrapBacktestParams = None):
        self.params = params or TrapBacktestParams()

    @staticmethod
    def _ema(series: pd.Series, span: int) -> pd.Series:
        return series.ewm(span=span, adjust=False).mean()

    def _compute_signals(self, df: pd.DataFrame, symbol: str = "BTCUSDT") -> pd.Series:
        """
        Trap Strategy sinyal hesaplaması.
        Tüm göstergeler .shift(1) ile kapanmış muma dayalı.
        """
        close = df["close"]
        high  = df["high"]
        low   = df["low"]

        # --- A) EMA Trend Filtresi ---
        ema9  = self._ema(close, 9).shift(1)
        ema21 = self._ema(close, 21).shift(1)
        ema_bull = ema9 > ema21
        ema_bear = ema9 < ema21

        # --- B) Funding Rate Extreme ---
        funding = df.get("funding_rate", pd.Series(0.0, index=df.index))
        fund_long  = funding < FUNDING_LOW   # shorts over-leveraged → LONG
        fund_short = funding > FUNDING_HIGH  # longs over-leveraged → SHORT

        # --- C) OI Spike ---
        if "oi" in df.columns:
            oi_chg = df["oi"].pct_change(1).shift(1)
        else:
            # OI yoksa sıfır kabul et
            oi_chg = pd.Series(0.0, index=df.index)
        oi_spike = oi_chg.abs() >= OI_SPIKE_THR

        # --- D) Likidasyon Zonu (pivot sweep) ---
        pivot_high = high.shift(1).rolling(PIVOT_N).max().shift(1)
        pivot_low  = low.shift(1).rolling(PIVOT_N).min().shift(1)

        # Önceki mum pivot'u kırdı ve şu an'ki mum geri döndü
        prev_low    = low.shift(1)
        prev_high   = high.shift(1)
        prev_close  = close.shift(1)
        curr_close  = close.shift(1)   # giriş mumunda sinyal: kapalı mum

        swept_low_and_back  = (prev_low < pivot_low)  & (prev_close > pivot_low)
        swept_high_and_back = (prev_high > pivot_high) & (prev_close < pivot_high)

        # --- Confluence Skoru ---
        long_score = (
            ema_bull.astype(int) +        # 1: trend yukarı
            fund_long.astype(int) +       # 1: funding extreme → long squeeze
            oi_spike.astype(int) +        # 1: OI spike
            swept_low_and_back.astype(int)  # 1: liq zone avı
        )
        short_score = (
            ema_bear.astype(int) +
            fund_short.astype(int) +
            oi_spike.astype(int) +
            swept_high_and_back.astype(int)
        )

        p = self.params
        long_ok  = long_score  >= p.min_score
        short_ok = short_score >= p.min_score

        signals = pd.Series(0, index=df.index)
        signals[long_ok  & (long_score  > short_score)] = 1
        signals[short_ok & (short_score > long_score)]  = -1

        return signals, long_score, short_score

    def run(
        self,
        df: pd.DataFrame,
        symbol: str = "BTCUSDT",
        initial_balance: float = 100.0,
    ) -> Dict[str, Any]:
        p = self.params
        signals, long_scores, short_scores = self._compute_signals(df, symbol)

        opens  = df["open"].values
        closes = df["close"].values
        highs  = df["high"].values
        lows   = df["low"].values
        sig_arr = signals.values
        n = len(df)

        trades: List[TrapTradeRecord] = []
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

        i = PIVOT_N + 5
        while i < n - 1:
            day_of_year = i // 24  # 1h mum → 24 mum/gün
            if day_of_year != last_day:
                daily_loss = 0.0
                daily_trades = 0
                last_day = day_of_year

            if in_trade:
                candles_in_trade += 1
                close_reason = None
                exit_price = closes[i]

                if trade_side == 1:
                    tp_hit = highs[i] >= tp_price
                    sl_hit = lows[i]  <= sl_price
                else:
                    tp_hit = lows[i]  <= tp_price
                    sl_hit = highs[i] >= sl_price

                if tp_hit and sl_hit:
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
                    gross   = (exit_price - entry_price) * trade_qty * trade_side
                    fee     = entry_price * trade_qty * p.maker_fee * 2
                    net     = gross - fee
                    pnl_pct = net / (entry_price * trade_qty / p.leverage)

                    balance    += net
                    daily_loss += min(0.0, net)

                    trades.append(TrapTradeRecord(
                        open_idx=entry_idx,
                        close_idx=i,
                        symbol=symbol,
                        side=trade_side,
                        entry_price=entry_price,
                        close_price=exit_price,
                        qty=trade_qty,
                        pnl_usd=net,
                        pnl_pct=pnl_pct,
                        close_reason=close_reason,
                        trap_score=score,
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
                    next_open = opens[i + 1]
                    score = int(long_scores.iloc[i] if direction == 1 else short_scores.iloc[i])
                    size_pct   = p.position_size_pct if score >= 4 else p.position_size_pct / 2
                    usdt_margin = balance * size_pct
                    trade_qty   = (usdt_margin * p.leverage) / next_open
                    entry_price = next_open
                    trade_side  = direction

                    if trade_side == 1:
                        tp_price = entry_price * (1 + p.tp_pct)
                        sl_price = entry_price * (1 - p.sl_pct)
                    else:
                        tp_price = entry_price * (1 - p.tp_pct)
                        sl_price = entry_price * (1 + p.sl_pct)

                    entry_idx       = i + 1
                    candles_in_trade = 0
                    in_trade        = True
                    daily_trades   += 1
                    i += 1

            i += 1

        return {
            "trades": trades,
            "equity_curve": equity_curve,
            "final_balance": balance,
            "params": p,
            "symbol": symbol,
        }

    def optimize(self, df: pd.DataFrame, symbol: str = "BTCUSDT", initial_balance: float = 100.0) -> List[Dict]:
        results = []
        tp_range   = [0.015, 0.020, 0.030]
        sl_range   = [0.008, 0.010, 0.015]
        score_range = [2, 3]

        for tp in tp_range:
            for sl in sl_range:
                for min_s in score_range:
                    self.params = TrapBacktestParams(tp_pct=tp, sl_pct=sl, min_score=min_s)
                    res = self.run(df, symbol=symbol, initial_balance=initial_balance)
                    trades = res["trades"]
                    if not trades:
                        continue
                    winners = [t for t in trades if t.pnl_usd > 0]
                    results.append({
                        "tp_pct": tp,
                        "sl_pct": sl,
                        "min_score": min_s,
                        "total_trades": len(trades),
                        "win_rate": len(winners) / len(trades),
                        "total_pnl": sum(t.pnl_usd for t in trades),
                        "final_balance": res["final_balance"],
                    })

        results.sort(key=lambda x: x["total_pnl"], reverse=True)
        return results

    def walk_forward(
        self,
        df: pd.DataFrame,
        symbol: str = "BTCUSDT",
        initial_balance: float = 100.0,
        n_splits: int = 5,
        train_ratio: float = 0.70,
    ) -> Dict[str, Any]:
        total_len = len(df)
        slice_size = total_len // n_splits
        all_oos_trades: List[TrapTradeRecord] = []
        oos_equity = [initial_balance]
        running_balance = initial_balance

        logger.info(f"[{symbol}] Trap walk-forward: {n_splits} dilim")

        best_params = self.params

        for fold in range(n_splits):
            start = fold * slice_size
            end   = start + slice_size if fold < n_splits - 1 else total_len
            fold_df = df.iloc[start:end].copy()

            train_end = int(len(fold_df) * train_ratio)
            train_df  = fold_df.iloc[:train_end]
            test_df   = fold_df.iloc[train_end:]

            logger.info(f"[{symbol}] Fold {fold+1}/{n_splits} | Train: {len(train_df)} | Test: {len(test_df)}")

            opt = self.optimize(train_df, symbol=symbol, initial_balance=running_balance)
            if opt:
                best = opt[0]
                best_params = TrapBacktestParams(
                    tp_pct=best["tp_pct"],
                    sl_pct=best["sl_pct"],
                    min_score=best["min_score"],
                )
                logger.info(
                    f"[{symbol}] Fold {fold+1} en iyi: "
                    f"TP={best['tp_pct']*100:.1f}% SL={best['sl_pct']*100:.1f}% "
                    f"Score>={best['min_score']} PnL=${best['total_pnl']:.2f}"
                )

            self.params = best_params
            oos = self.run(test_df, symbol=symbol, initial_balance=running_balance)
            all_oos_trades.extend(oos["trades"])
            running_balance = oos["final_balance"]
            oos_equity.extend(oos["equity_curve"][1:])

            oos_win = sum(1 for t in oos["trades"] if t.pnl_usd > 0)
            oos_pnl = sum(t.pnl_usd for t in oos["trades"])
            logger.info(
                f"[{symbol}] Fold {fold+1} OOS: {len(oos['trades'])} işlem | "
                f"Win=%{oos_win/max(len(oos['trades']),1)*100:.1f} | PnL=${oos_pnl:.2f}"
            )

        return {
            "trades": all_oos_trades,
            "equity_curve": oos_equity,
            "final_balance": running_balance,
            "params": best_params,
            "symbol": symbol,
            "method": "walk_forward",
        }
