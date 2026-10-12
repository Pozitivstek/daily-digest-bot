"""Общий для всех пользователей слой данных: источники + кэш с TTL.

Сто подписчиков одного города — один запрос погоды, не сто. Ошибки в кэш не попадают."""
import asyncio
import time
from datetime import timedelta
from typing import Awaitable, Callable

from .config import NEWS_TTL, RATES_TTL, WEATHER_TTL, Feed
from .errors import SourceError
from .http import Fetcher
from .sources.cbr import RatesSnapshot, fetch_rates
from .sources.geo import Place, search_places
from .sources.rss import NewsItem, fetch_feed
from .sources.weather import Weather, fetch_forecast

COORD_PRECISION = 2   # ~1 км: соседние точки одного города делят один прогноз


class DataHub:
    def __init__(self, fetcher: Fetcher, *, clock: Callable[[], float] = time.monotonic):
        self._fetcher = fetcher
        self._clock = clock
        self._cache: dict[tuple, tuple[float, object]] = {}
        self._locks: dict[tuple, asyncio.Lock] = {}

    async def _cached(self, key: tuple, ttl: float, load: Callable[[], Awaitable]):
        def fresh():
            hit = self._cache.get(key)
            return hit[1] if hit and self._clock() - hit[0] < ttl else None

        value = fresh()
        if value is not None:
            return value
        async with self._locks.setdefault(key, asyncio.Lock()):
            value = fresh()   # пока ждали замок, другой запрос мог уже всё загрузить
            if value is None:
                value = await load()
                self._cache[key] = (self._clock(), value)
            return value

    async def rates(self) -> tuple[RatesSnapshot, RatesSnapshot | None]:
        """Свежие курсы и курсы предыдущей даты (для изменения за день). Предыдущие необязательны."""
        async def load():
            latest = await fetch_rates(self._fetcher)
            try:
                previous = await fetch_rates(self._fetcher, latest.on_date - timedelta(days=1))
            except SourceError:
                previous = None
            return latest, previous

        return await self._cached(("rates",), RATES_TTL, load)

    async def forecast(self, lat: float, lon: float) -> Weather:
        key = ("weather", round(lat, COORD_PRECISION), round(lon, COORD_PRECISION))
        return await self._cached(key, WEATHER_TTL, lambda: fetch_forecast(self._fetcher, lat, lon))

    async def news(self, feed: Feed, limit: int) -> list[NewsItem]:
        key = ("news", feed.id, limit)
        return await self._cached(key, NEWS_TTL, lambda: fetch_feed(self._fetcher, feed.url, limit))

    async def places(self, query: str) -> list[Place]:
        return await search_places(self._fetcher, query)
