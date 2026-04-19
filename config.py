import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()

@dataclass
class Config:
    # Binance
    BINANCE_API_KEY: str = os.getenv("BINANCE_API_KEY", "")
    BINANCE_API_SECRET: str = os.getenv("BINANCE_API_SECRET", "")
    BINANCE_TESTNET: bool = os.getenv("BINANCE_TESTNET", "true").lower() == "true"

    # Twelve Data (cross-asset)
    TWELVE_DATA_API_KEY: str = os.getenv("TWELVE_DATA_API_KEY", "")

    # CryptoPanic (haber sentiment)
    CRYPTOPANIC_API_KEY: str = os.getenv("CRYPTOPANIC_API_KEY", "")

    # Telegram
    TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # İşlem parametreleri
    SYMBOL: str = "BTCUSDT"
    LEVERAGE: int = 5
    TIMEFRAME: str = "1m"

    # Pozisyon boyutu (hesap yüzdesi)
    POSITION_SIZE_FULL: float = 0.10   # confluence 5/5
    POSITION_SIZE_HALF: float = 0.05   # confluence 3-4/5

    # TP / SL (yüzde)
    TAKE_PROFIT_PCT: float = 0.0015    # %0.15
    STOP_LOSS_PCT: float = 0.0010      # %0.10
    MAX_CANDLES_IN_TRADE: int = 10     # zaman çıkışı

    # Risk korumaları
    MAX_DAILY_LOSS_PCT: float = 0.05   # günlük max zarar %5
    MAX_DAILY_TRADES: int = 500
    CONSECUTIVE_LOSS_PAUSE: int = 5    # üst üste zarar → 30dk dur
    PAUSE_MINUTES: int = 30
    ACCOUNT_STOP_LOSS_PCT: float = 0.20  # hesap %20 düşerse bot durur

    # Confluence eşiği
    MIN_CONFLUENCE_SCORE: int = 3      # 5 üzerinden minimum

    # Cross-asset güncelleme aralığı (saniye)
    CROSS_ASSET_UPDATE_INTERVAL: int = 300   # 5 dakika

    # OI güncelleme aralığı (saniye)
    OI_UPDATE_INTERVAL: int = 300

    # Strateji parametreleri
    VOLUME_SPIKE_MULTIPLIER: float = 1.5   # hacim spike eşiği
    VOLUME_LOOKBACK: int = 20
    LIQUIDITY_SWEEP_LOOKBACK: int = 5
    CVD_LOOKBACK: int = 10
    OI_CHANGE_THRESHOLD: float = 0.02      # %2
    FUNDING_RATE_HIGH: float = 0.001       # +%0.1
    FUNDING_RATE_LOW: float = -0.0005      # -%0.05
    VIX_PAUSE_THRESHOLD: float = 35.0
    FEAR_GREED_EXTREME_FEAR: int = 20
    FEAR_GREED_EXTREME_GREED: int = 80
    DXY_STRONG_MOVE_PCT: float = 0.005     # %0.5 / 4 saat
    NASDAQ_EMA_PERIOD: int = 50

    # Likidasyon eşiği (USDT)
    LIQUIDATION_THRESHOLD_USD: float = 500_000

    # Paper trading modu
    PAPER_TRADING: bool = os.getenv("PAPER_TRADING", "true").lower() == "true"


CONFIG = Config()
