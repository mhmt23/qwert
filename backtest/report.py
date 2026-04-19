import json
from pathlib import Path
from typing import List, Dict, Any
import numpy as np
import pandas as pd
from loguru import logger

from backtest.engine import TradeRecord

RESULTS_DIR = Path("backtest/results")


class BacktestReport:
    def __init__(self):
        RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        trades: List[TradeRecord],
        equity_curve: List[float],
        params: dict,
        initial_balance: float = 100.0,
    ) -> Dict[str, Any]:
        if not trades:
            logger.warning("İşlem yok, rapor oluşturulamadı.")
            return {}

        pnls = [t.pnl_usd for t in trades]
        winners = [p for p in pnls if p > 0]
        losers = [p for p in pnls if p <= 0]

        win_rate = len(winners) / len(trades)
        avg_win = np.mean(winners) if winners else 0
        avg_loss = np.mean(losers) if losers else 0
        profit_factor = abs(sum(winners) / sum(losers)) if losers else float("inf")

        # Sharpe (günlük PnL serisi)
        daily_pnl = pd.Series(pnls)
        sharpe = (daily_pnl.mean() / daily_pnl.std() * np.sqrt(252)) if daily_pnl.std() > 0 else 0

        # Max Drawdown
        eq = np.array(equity_curve)
        peak = np.maximum.accumulate(eq)
        drawdown = (eq - peak) / peak
        max_dd = drawdown.min()

        # İşlem başına ortalama süre
        avg_duration = np.mean([t.duration_candles for t in trades])

        total_pnl = sum(pnls)
        final_balance = initial_balance + total_pnl
        total_days = len(trades) / max(1, len(trades) / max(1, len(equity_curve) / 1440))

        tp_count = sum(1 for t in trades if t.close_reason == "TP")
        sl_count = sum(1 for t in trades if t.close_reason == "SL")
        timeout_count = sum(1 for t in trades if t.close_reason == "TIMEOUT")

        report = {
            "toplam_islem": len(trades),
            "kazanan": len(winners),
            "kaybeden": len(losers),
            "win_rate_pct": round(win_rate * 100, 2),
            "profit_factor": round(profit_factor, 3),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "toplam_pnl_usd": round(total_pnl, 4),
            "baslangic_bakiye": initial_balance,
            "final_bakiye": round(final_balance, 4),
            "getiri_pct": round((final_balance - initial_balance) / initial_balance * 100, 2),
            "ortalama_kazanc": round(avg_win, 5),
            "ortalama_kayip": round(avg_loss, 5),
            "ortalama_sure_mum": round(avg_duration, 1),
            "tp_sayisi": tp_count,
            "sl_sayisi": sl_count,
            "timeout_sayisi": timeout_count,
            "parametreler": params,
        }

        self._print(report)
        self._save(report, trades)
        return report

    def _print(self, r: dict):
        print("\n" + "=" * 50)
        print("BACKTEST RAPORU")
        print("=" * 50)
        print(f"Toplam İşlem    : {r['toplam_islem']}")
        print(f"Win Rate        : %{r['win_rate_pct']}")
        print(f"Profit Factor   : {r['profit_factor']}")
        print(f"Sharpe Ratio    : {r['sharpe_ratio']}")
        print(f"Max Drawdown    : %{r['max_drawdown_pct']}")
        print(f"Net PnL         : ${r['toplam_pnl_usd']}")
        print(f"Final Bakiye    : ${r['final_bakiye']}")
        print(f"Getiri          : %{r['getiri_pct']}")
        print(f"TP/SL/Timeout   : {r['tp_sayisi']}/{r['sl_sayisi']}/{r['timeout_sayisi']}")
        print("=" * 50 + "\n")

    def _save(self, report: dict, trades: List[TradeRecord]):
        with open(RESULTS_DIR / "summary.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        trade_data = [
            {
                "open_idx": t.open_idx,
                "close_idx": t.close_idx,
                "side": "LONG" if t.side == 1 else "SHORT",
                "entry_price": t.entry_price,
                "close_price": t.close_price,
                "qty": t.qty,
                "pnl_usd": t.pnl_usd,
                "pnl_pct": t.pnl_pct,
                "close_reason": t.close_reason,
                "confluence_score": t.confluence_score,
                "duration_candles": t.duration_candles,
            }
            for t in trades
        ]
        pd.DataFrame(trade_data).to_csv(RESULTS_DIR / "trade_log.csv", index=False)
        logger.info(f"Rapor kaydedildi: {RESULTS_DIR}")

    def save_best_params(self, optimization_results: List[dict]):
        if not optimization_results:
            return
        best = optimization_results[0]
        with open(RESULTS_DIR / "best_params.json", "w") as f:
            json.dump(best, f, indent=2)
        logger.info(f"En iyi parametreler: {best}")
