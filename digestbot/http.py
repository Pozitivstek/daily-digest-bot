"""HTTP-клиент с таймаутом и лимитом размера ответа. Тесты подменяют его своим Fetcher."""
import asyncio
from typing import Protocol

import aiohttp

from .errors import FetchError

MAX_BYTES = 3_000_000
USER_AGENT = "Mozilla/5.0 (compatible; DailyDigestBot/1.0)"


class Fetcher(Protocol):
    async def get(self, url: str, params: dict | None = None) -> bytes: ...


class HttpFetcher:
    def __init__(self, timeout: float = 10.0, max_bytes: int = MAX_BYTES):
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._max_bytes = max_bytes
        self._session: aiohttp.ClientSession | None = None

    def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout, headers={"User-Agent": USER_AGENT})
        return self._session

    async def get(self, url: str, params: dict | None = None) -> bytes:
        try:
            async with self._client().get(url, params=params) as response:
                if response.status != 200:
                    raise FetchError(f"{url}: HTTP {response.status}")
                body = bytearray()
                async for chunk in response.content.iter_chunked(64 * 1024):
                    body.extend(chunk)
                    if len(body) > self._max_bytes:
                        raise FetchError(f"{url}: ответ больше {self._max_bytes} байт")
                return bytes(body)
        except FetchError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError) as exc:
            raise FetchError(f"{url}: {type(exc).__name__}: {exc}") from exc

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
