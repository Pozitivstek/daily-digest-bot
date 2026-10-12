"""Сборка дайджеста: форматирование, устойчивость к отказам источников, кэш, разбиение."""
import json
import unittest
from datetime import datetime
from decimal import Decimal

from digestbot.config import FEEDS
from digestbot.db import UserSettings
from digestbot.digest import build_digest, greeting, ru_number, split_message
from digestbot.errors import FetchError
from digestbot.services import DataHub
from digestbot.sources.cbr import CBR_URL

from tests.test_sources import CBR_XML, FORECAST_JSON, RSS2

PREV_XML = CBR_XML.replace("95,1234".encode("cp1251"), "94,7800".encode("cp1251")) \
                  .replace(b'Date="10.10.2026"', b'Date="09.10.2026"')

LATEST_XML = CBR_XML.replace(b'Date="10.10.2026"', b'Date="12.10.2026"')


class FakeFetcher:
    """Отдаёт заготовленные ответы по url; считает обращения."""

    def __init__(self, routes: dict[str, bytes | Exception]):
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    async def get(self, url: str, params: dict | None = None) -> bytes:
        self.calls.append((url, params))
        key = url + ("#prev" if params and "date_req" in params else "")
        result = self.routes.get(key)
        if result is None:
            raise FetchError(f"нет маршрута {key}")
        if isinstance(result, Exception):
            raise result
        return result

    def count(self, url_part: str) -> int:
        return sum(1 for url, _ in self.calls if url_part in url)


def routes(**overrides) -> dict[str, bytes | Exception]:
    base = {
        CBR_URL: LATEST_XML,
        CBR_URL + "#prev": PREV_XML,
        "https://api.open-meteo.com/v1/forecast": FORECAST_JSON,
        FEEDS["rbc"].url: RSS2,
        FEEDS["habr"].url: RSS2,
    }
    base.update(overrides)
    return base


def user(**kw) -> UserSettings:
    base = dict(user_id=1, name="Аня", city="Казань", lat=55.79, lon=49.12, tz="Europe/Moscow",
                currencies=("USD", "KZT"), feeds=("rbc",), send_time="08:00", enabled=True,
                last_sent_date=None)
    base.update(kw)
    return UserSettings(**base)


NOW = datetime(2026, 10, 12, 8, 5)


async def digest_text(fetcher, **kw) -> str:
    parts = await build_digest(user(**kw), DataHub(fetcher), NOW)
    return "\n".join(parts)


class FormatTest(unittest.TestCase):
    def test_ru_number(self):
        self.assertEqual(ru_number(Decimal("95.1234")), "95,12")
        self.assertEqual(ru_number(1234.5, 1), "1 234,5")
        self.assertEqual(ru_number(Decimal("-0.3"), 2, signed=True), "−0,30")
        self.assertEqual(ru_number(Decimal("0.34"), 2, signed=True), "+0,34")

    def test_greeting_by_hour(self):
        self.assertIn("утро", greeting(7))
        self.assertIn("день", greeting(13))
        self.assertIn("вечер", greeting(20))

    def test_split_respects_limit_and_keeps_content(self):
        lines = [f"строка {i} " + "x" * 90 for i in range(100)]
        parts = split_message("\n".join(lines), limit=4096)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p) <= 4096 for p in parts))
        self.assertEqual("\n".join(parts).split("\n"), lines)

    def test_split_hard_cuts_a_single_huge_line(self):
        parts = split_message("y" * 9000, limit=4096)
        self.assertTrue(all(len(p) <= 4096 for p in parts))
        self.assertEqual("".join(parts), "y" * 9000)


class DigestTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_digest_has_all_sections(self):
        text = await digest_text(FakeFetcher(routes()))
        self.assertIn("Доброе утро", text)
        self.assertIn("Казань", text)
        self.assertIn("+15", text)                  # температура сейчас
        self.assertIn("USD", text)
        self.assertIn("95,12", text)
        self.assertIn("+0,34", text)                # 95,1234 − 94,78
        self.assertIn("100 KZT", text)              # номинал показан
        self.assertIn("Вторая", text)
        self.assertIn('<a href="https://example.com/2">', text)

    async def test_dates_in_rates_header(self):
        text = await digest_text(FakeFetcher(routes()))
        self.assertIn("12.10", text)

    async def test_weather_failure_does_not_break_other_sections(self):
        fetcher = FakeFetcher(routes(**{"https://api.open-meteo.com/v1/forecast": FetchError("down")}))
        text = await digest_text(fetcher)
        self.assertIn("USD", text)
        self.assertIn("Вторая", text)
        self.assertIn("Погода", text)
        self.assertIn("недоступн", text)

    async def test_news_failure_is_isolated(self):
        fetcher = FakeFetcher(routes(**{FEEDS["rbc"].url: FetchError("timeout")}))
        text = await digest_text(fetcher)
        self.assertIn("USD", text)
        self.assertIn("недоступн", text)

    async def test_previous_rates_failure_only_drops_delta(self):
        fetcher = FakeFetcher(routes(**{CBR_URL + "#prev": FetchError("nope")}))
        text = await digest_text(fetcher)
        self.assertIn("95,12", text)
        self.assertNotIn("+0,34", text)

    async def test_no_city_hints_instead_of_weather(self):
        fetcher = FakeFetcher(routes())
        text = await digest_text(fetcher, city="", lat=None, lon=None)
        self.assertIn("город", text.lower())
        self.assertEqual(fetcher.count("open-meteo"), 0)

    async def test_empty_selection_sections_are_omitted(self):
        text = await digest_text(FakeFetcher(routes()), currencies=(), feeds=())
        self.assertNotIn("Курсы", text)
        self.assertNotIn("Новости", text)

    async def test_html_in_titles_is_escaped(self):
        rss = b'<rss version="2.0"><channel><item><title>&lt;script&gt;x&lt;/script&gt;</title>' \
              b"<link>https://e.com/a</link></item></channel></rss>"
        text = await digest_text(FakeFetcher(routes(**{FEEDS["rbc"].url: rss})))
        self.assertNotIn("<script>", text)

    async def test_hub_caches_between_users(self):
        fetcher = FakeFetcher(routes())
        hub = DataHub(fetcher)
        await build_digest(user(user_id=1), hub, NOW)
        await build_digest(user(user_id=2), hub, NOW)
        self.assertEqual(fetcher.count("XML_daily"), 2)   # сегодня + предыдущий день, один раз на всех
        self.assertEqual(fetcher.count("open-meteo"), 1)
        self.assertEqual(fetcher.count(FEEDS["rbc"].url), 1)

    async def test_cache_expires(self):
        fetcher = FakeFetcher(routes())
        clock = [0.0]
        hub = DataHub(fetcher, clock=lambda: clock[0])
        await build_digest(user(), hub, NOW)
        clock[0] += 3 * 3600
        await build_digest(user(), hub, NOW)
        self.assertEqual(fetcher.count("open-meteo"), 2)

    async def test_failures_are_not_cached(self):
        fetcher = FakeFetcher(routes(**{"https://api.open-meteo.com/v1/forecast": FetchError("down")}))
        hub = DataHub(fetcher)
        await build_digest(user(), hub, NOW)
        fetcher.routes["https://api.open-meteo.com/v1/forecast"] = FORECAST_JSON
        parts = await build_digest(user(), hub, NOW)
        self.assertIn("+15", "\n".join(parts))


if __name__ == "__main__":
    unittest.main()
