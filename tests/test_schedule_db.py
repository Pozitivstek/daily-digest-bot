"""Расписание рассылки, выбор опций и хранилище пользователей."""
import unittest
from datetime import datetime, time, timedelta, timezone

from digestbot.choice import toggle
from digestbot.config import CURRENCIES, DEFAULT_CURRENCIES, DEFAULT_FEEDS, DEFAULT_SEND_TIME
from digestbot.db import Database
from digestbot.schedule import is_due, local_now, parse_time


class ParseTimeTest(unittest.TestCase):
    def test_valid_values_are_normalised(self):
        self.assertEqual(parse_time("8:30"), "08:30")
        self.assertEqual(parse_time(" 21:05 "), "21:05")
        self.assertEqual(parse_time("0:00"), "00:00")

    def test_invalid_values(self):
        for bad in ("24:00", "12:60", "abc", "", "8", "8-30", "12:5", "-1:00", "１２:００"):
            self.assertIsNone(parse_time(bad), bad)


class IsDueTest(unittest.TestCase):
    def due(self, now, send="08:00", last=None, grace_h=3):
        return is_due(now, send, last, grace=timedelta(hours=grace_h))

    def test_before_time_is_not_due(self):
        self.assertFalse(self.due(datetime(2026, 10, 12, 7, 59)))

    def test_exactly_at_time_is_due(self):
        self.assertTrue(self.due(datetime(2026, 10, 12, 8, 0)))

    def test_within_grace_after_restart_is_due(self):
        self.assertTrue(self.due(datetime(2026, 10, 12, 10, 30)))

    def test_after_grace_is_skipped(self):
        self.assertFalse(self.due(datetime(2026, 10, 12, 11, 1)))

    def test_already_sent_today(self):
        self.assertFalse(self.due(datetime(2026, 10, 12, 8, 1), last="2026-10-12"))

    def test_sent_yesterday_is_due_today(self):
        self.assertTrue(self.due(datetime(2026, 10, 12, 8, 1), last="2026-10-11"))

    def test_late_evening_time_with_grace_crossing_midnight(self):
        # 23:30 + 3 ч: в 00:10 следующих суток это уже «вчерашняя» рассылка, а не сегодняшняя
        self.assertFalse(self.due(datetime(2026, 10, 13, 0, 10), send="23:30", last="2026-10-12"))


class LocalNowTest(unittest.TestCase):
    def test_converts_to_user_timezone(self):
        utc = datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)
        self.assertEqual(local_now("Asia/Yekaterinburg", utc).hour, 10)

    def test_unknown_timezone_falls_back_to_moscow(self):
        utc = datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)
        self.assertEqual(local_now("Mars/Olympus", utc).hour, 8)


class ToggleTest(unittest.TestCase):
    def test_adds_and_keeps_canonical_order(self):
        self.assertEqual(toggle(("EUR",), "USD", CURRENCIES), ("USD", "EUR"))

    def test_removes(self):
        self.assertEqual(toggle(("USD", "EUR"), "USD", CURRENCIES), ("EUR",))

    def test_unknown_item_is_ignored(self):
        self.assertEqual(toggle(("USD",), "XXX", CURRENCIES), ("USD",))

    def test_can_become_empty(self):
        self.assertEqual(toggle(("USD",), "USD", CURRENCIES), ())


class DbTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await Database.open(":memory:")

    async def asyncTearDown(self):
        await self.db.close()

    async def test_new_user_gets_defaults(self):
        user = await self.db.ensure_user(7, "Аня")
        self.assertEqual(user.currencies, DEFAULT_CURRENCIES)
        self.assertEqual(user.feeds, DEFAULT_FEEDS)
        self.assertEqual(user.send_time, DEFAULT_SEND_TIME)
        self.assertTrue(user.enabled)
        self.assertFalse(user.has_city)

    async def test_ensure_user_is_idempotent_and_keeps_settings(self):
        await self.db.ensure_user(7, "Аня")
        await self.db.set_send_time(7, "09:30")
        again = await self.db.ensure_user(7, "Аня Новая")
        self.assertEqual(again.send_time, "09:30")

    async def test_city_roundtrip(self):
        await self.db.ensure_user(7, "Аня")
        await self.db.set_city(7, "Казань", 55.78874, 49.12214, "Europe/Moscow")
        user = await self.db.get_user(7)
        self.assertEqual((user.city, user.tz), ("Казань", "Europe/Moscow"))
        self.assertAlmostEqual(user.lat, 55.78874)
        self.assertTrue(user.has_city)

    async def test_lists_roundtrip(self):
        await self.db.ensure_user(7, "Аня")
        await self.db.set_currencies(7, ("GBP", "JPY"))
        await self.db.set_feeds(7, ())
        user = await self.db.get_user(7)
        self.assertEqual(user.currencies, ("GBP", "JPY"))
        self.assertEqual(user.feeds, ())

    async def test_enabled_users_excludes_disabled(self):
        await self.db.ensure_user(1, "A")
        await self.db.ensure_user(2, "B")
        await self.db.set_enabled(2, False)
        self.assertEqual([u.user_id for u in await self.db.enabled_users()], [1])

    async def test_mark_sent(self):
        await self.db.ensure_user(1, "A")
        await self.db.mark_sent(1, "2026-10-12")
        self.assertEqual((await self.db.get_user(1)).last_sent_date, "2026-10-12")

    async def test_unknown_user_is_none(self):
        self.assertIsNone(await self.db.get_user(404))

    async def test_changing_time_resets_today_mark_so_new_time_still_fires(self):
        await self.db.ensure_user(1, "A")
        await self.db.mark_sent(1, "2026-10-12")
        await self.db.set_send_time(1, "20:00")
        self.assertIsNone((await self.db.get_user(1)).last_sent_date)


if __name__ == "__main__":
    unittest.main()
