import csv
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from loguru import logger

from config import CONFIG


@dataclass
class Position:
    symbol: str
    side: str               # "BUY" / "SELL"
    entry_price: float
    qty: float
    tp_price: float
    sl_price: float
    open_time: float = field(default_factory=time.time)
    candles_open: int = 0
    confluence_score: int = 0
    paper: bool = True

    @property
    def notional(self) -> float:
        return self.entry_price * self.qty

    def calc_pnl(self, current_price: float) -> float:
        if self.side == "BUY":
            return (current_price - self.entry_price) * self.qty
        else:
            return (self.entry_price - current_price) * self.qty

    def pnl_pct(self, current_price: float) -> float:
        cost = self.entry_price * self.qty / CONFIG.LEVERAGE
        pnl = self.calc_pnl(current_price)
        return pnl / cost if cost > 0 else 0.0


class PositionTracker:
    LOG_PATH = Path("logs/trades.csv")

    def __init__(self):
        self.current: Optional[Position] = None
        self._ensure_log()

    def _ensure_log(self):
        self.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if not self.LOG_PATH.exists():
            with open(self.LOG_PATH, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "open_time", "symbol", "side", "entry_price",
                    "close_price", "qty", "pnl_usd", "pnl_pct",
                    "duration_sec", "close_reason", "confluence_score", "paper"
                ])

    def open(self, pos: Position):
        self.current = pos
        logger.info(
            f"Pozisyon açıldı: {pos.side} {pos.qty} {pos.symbol} "
            f"@ {pos.entry_price:.2f} | TP={pos.tp_price:.2f} SL={pos.sl_price:.2f}"
        )

    def check_exit(self, current_price: float) -> Optional[str]:
        """TP/SL/zaman çıkışı kontrolü. Çıkış sebebi döner, yoksa None."""
        if not self.current:
            return None

        pos = self.current

        if pos.side == "BUY":
            if current_price >= pos.tp_price:
                return "TP"
            if current_price <= pos.sl_price:
                return "SL"
        else:
            if current_price <= pos.tp_price:
                return "TP"
            if current_price >= pos.sl_price:
                return "SL"

        pos.candles_open += 1
        if pos.candles_open >= CONFIG.MAX_CANDLES_IN_TRADE:
            return "TIMEOUT"

        return None

    def close(self, close_price: float, reason: str) -> dict:
        pos = self.current
        if not pos:
            return {}

        duration = time.time() - pos.open_time
        pnl = pos.calc_pnl(close_price)
        pnl_pct = pos.pnl_pct(close_price)

        record = {
            "open_time": pos.open_time,
            "symbol": pos.symbol,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "close_price": close_price,
            "qty": pos.qty,
            "pnl_usd": pnl,
            "pnl_pct": pnl_pct,
            "duration_sec": duration,
            "close_reason": reason,
            "confluence_score": pos.confluence_score,
            "paper": pos.paper,
        }

        with open(self.LOG_PATH, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=record.keys())
            writer.writerow(record)

        logger.info(
            f"Pozisyon kapandı [{reason}]: {pos.side} @ {close_price:.2f} | "
            f"PnL={pnl:+.4f} USDT ({pnl_pct*100:+.3f}%) | "
            f"Süre={duration:.0f}s"
        )
        self.current = None
        return record

    def is_open(self) -> bool:
        return self.current is not None
