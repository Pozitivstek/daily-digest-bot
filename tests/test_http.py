"""HttpFetcher против настоящего локального HTTP-сервера: статусы, лимит размера, таймаут, обрыв."""
import asyncio
import unittest

from aiohttp import web

from digestbot.errors import FetchError
from digestbot.http import HttpFetcher


class HttpFetcherTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = web.Application()
        app.router.add_get("/ok", self.ok)
        app.router.add_get("/echo", self.echo)
        app.router.add_get("/missing", self.missing)
        app.router.add_get("/huge", self.huge)
        app.router.add_get("/slow", self.slow)
        app.router.add_get("/drop", self.drop)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base = f"http://127.0.0.1:{port}"

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def ok(self, request):
        return web.Response(body=b"hello")

    async def echo(self, request):
        return web.Response(text=request.query.get("name", ""))

    async def missing(self, request):
        return web.Response(status=404)

    async def huge(self, request):
        return web.Response(body=b"x" * 500_000)

    async def slow(self, request):
        await asyncio.sleep(1)
        return web.Response(body=b"late")

    async def drop(self, request):
        request.transport.close()
        return web.Response()

    async def test_returns_body(self):
        fetcher = HttpFetcher()
        try:
            self.assertEqual(await fetcher.get(f"{self.base}/ok"), b"hello")
        finally:
            await fetcher.close()

    async def test_query_params_are_url_encoded(self):
        fetcher = HttpFetcher()
        try:
            body = await fetcher.get(f"{self.base}/echo", {"name": "Казань"})
            self.assertEqual(body.decode(), "Казань")
        finally:
            await fetcher.close()

    async def test_non_200_raises_fetch_error(self):
        fetcher = HttpFetcher()
        try:
            with self.assertRaises(FetchError):
                await fetcher.get(f"{self.base}/missing")
        finally:
            await fetcher.close()

    async def test_oversized_body_raises_fetch_error(self):
        fetcher = HttpFetcher(max_bytes=100_000)
        try:
            with self.assertRaises(FetchError):
                await fetcher.get(f"{self.base}/huge")
        finally:
            await fetcher.close()

    async def test_timeout_raises_fetch_error(self):
        fetcher = HttpFetcher(timeout=0.3)
        try:
            with self.assertRaises(FetchError):
                await fetcher.get(f"{self.base}/slow")
        finally:
            await fetcher.close()

    async def test_connection_drop_raises_fetch_error(self):
        fetcher = HttpFetcher()
        try:
            with self.assertRaises(FetchError):
                await fetcher.get(f"{self.base}/drop")
        finally:
            await fetcher.close()

    async def test_refused_connection_raises_fetch_error(self):
        fetcher = HttpFetcher(timeout=2)
        try:
            with self.assertRaises(FetchError):
                await fetcher.get("http://127.0.0.1:9/nothing")
        finally:
            await fetcher.close()


if __name__ == "__main__":
    unittest.main()
