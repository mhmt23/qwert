import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional
import aiohttp
from loguru import logger


@dataclass
class SentimentData:
    fear_greed_value: int = 50          # 0-100
    fear_greed_label: str = "Neutral"
    news_sentiment: float = 0.0         # -1 (çok negatif) .. +1 (çok pozitif)
    news_count_1h: int = 0
    timestamp: int = field(default_factory=lambda: int(time.time()))


class SentimentFeed:
    FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
    CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"

    def __init__(self, cryptopanic_key: str = "", update_interval: int = 3600):
        self.cryptopanic_key = cryptopanic_key
        self.update_interval = update_interval
        self._data = SentimentData()
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _fetch_fear_greed(self) -> tuple[int, str]:
        session = await self._get_session()
        try:
            async with session.get(self.FEAR_GREED_URL) as resp:
                data = await resp.json()
                entry = data["data"][0]
                return int(entry["value"]), entry["value_classification"]
        except Exception as e:
            logger.warning(f"Fear & Greed alınamadı: {e}")
            return 50, "Neutral"

    async def _fetch_news_sentiment(self) -> tuple[float, int]:
        if not self.cryptopanic_key:
            return 0.0, 0

        session = await self._get_session()
        try:
            async with session.get(
                self.CRYPTOPANIC_URL,
                params={
                    "auth_token": self.cryptopanic_key,
                    "currencies": "BTC",
                    "filter": "hot",
                    "public": "true",
                },
            ) as resp:
                data = await resp.json()
                results = data.get("results", [])

                cutoff = int(time.time()) - 3600  # son 1 saat
                score = 0.0
                count = 0

                for item in results:
                    created = item.get("created_at", "")
                    votes = item.get("votes", {})
                    positive = votes.get("positive", 0)
                    negative = votes.get("negative", 0)
                    total = positive + negative
                    if total > 0:
                        score += (positive - negative) / total
                        count += 1

                avg_score = score / count if count > 0 else 0.0
                return avg_score, count
        except Exception as e:
            logger.warning(f"Haber sentiment alınamadı: {e}")
            return 0.0, 0

    async def update(self):
        (fg_value, fg_label), (news_score, news_count) = await asyncio.gather(
            self._fetch_fear_greed(),
            self._fetch_news_sentiment(),
        )
        self._data = SentimentData(
            fear_greed_value=fg_value,
            fear_greed_label=fg_label,
            news_sentiment=news_score,
            news_count_1h=news_count,
            timestamp=int(time.time()),
        )
        logger.debug(
            f"Sentiment güncellendi | F&G={fg_value} ({fg_label}) "
            f"Haber={news_score:.2f} ({news_count} haber)"
        )

    @property
    def data(self) -> SentimentData:
        return self._data

    async def start(self):
        self._running = True
        while self._running:
            try:
                await self.update()
            except Exception as e:
                logger.error(f"Sentiment güncelleme hatası: {e}")
            await asyncio.sleep(self.update_interval)

    def stop(self):
        self._running = False

    async def close(self):
        self.stop()
        if self._session and not self._session.closed:
            await self._session.close()
