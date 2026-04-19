import time
from dataclasses import dataclass, field
from typing import Optional
from loguru import logger

from config import CONFIG


@dataclass
class RiskState:
    daily_pnl: float = 0.0
    daily_trade_count: int = 0
    consecutive_losses: int = 0
    paused_until: float = 0.0
    account_balance: float = 100.0
    initial_daily_balance: float = 100.0
    day_start_ts: int = field(default_factory=lambda: int(time.time()))


class RiskManager:
    def __init__(self, initial_balance: float = 100.0):
        self.state = RiskState(
            account_balance=initial_balance,
            initial_daily_balance=initial_balance,
        )

    def reset_daily(self):
        self.state.daily_pnl = 0.0
        self.state.daily_trade_count = 0
        self.state.consecutive_losses = 0
        self.state.initial_daily_balance = self.state.account_balance
        self.state.day_start_ts = int(time.time())
        logger.info("Günlük risk sıfırlandı.")

    def can_trade(self) -> tuple[bool, str]:
        now = time.time()

        # Duraklama
        if now < self.state.paused_until:
            remaining = int(self.state.paused_until - now)
            return False, f"Duraklama aktif ({remaining}s kaldı)"

        # Günlük max zarar
        daily_loss_pct = -self.state.daily_pnl / self.state.initial_daily_balance
        if daily_loss_pct >= CONFIG.MAX_DAILY_LOSS_PCT:
            return False, f"Günlük max zarar aşıldı (%{daily_loss_pct*100:.2f})"

        # Max günlük işlem
        if self.state.daily_trade_count >= CONFIG.MAX_DAILY_TRADES:
            return False, f"Max günlük işlem ({CONFIG.MAX_DAILY_TRADES}) aşıldı"

        # Hesap stop
        total_loss_pct = (self.state.initial_daily_balance - self.state.account_balance) / self.state.initial_daily_balance
        if total_loss_pct >= CONFIG.ACCOUNT_STOP_LOSS_PCT:
            return False, f"Hesap stop (%{total_loss_pct*100:.1f} kayıp) — BOT DURDURULDU"

        return True, "OK"

    def get_position_size(self, balance: float, size_multiplier: float) -> float:
        """İşlem başına kullanılacak sermaye (USDT)."""
        base_pct = CONFIG.POSITION_SIZE_FULL if size_multiplier == 1.0 else CONFIG.POSITION_SIZE_HALF
        return balance * base_pct

    def record_trade(self, pnl: float):
        self.state.daily_pnl += pnl
        self.state.daily_trade_count += 1
        self.state.account_balance += pnl

        if pnl < 0:
            self.state.consecutive_losses += 1
            if self.state.consecutive_losses >= CONFIG.CONSECUTIVE_LOSS_PAUSE:
                self.state.paused_until = time.time() + CONFIG.PAUSE_MINUTES * 60
                logger.warning(
                    f"{CONFIG.CONSECUTIVE_LOSS_PAUSE} üst üste zarar → "
                    f"{CONFIG.PAUSE_MINUTES} dk duraklama"
                )
        else:
            self.state.consecutive_losses = 0

    def update_balance(self, new_balance: float):
        self.state.account_balance = new_balance
