"""Сквозные сценарии: настоящие обработчики и рассылка при подменённых Telegram и источниках."""
import json
import unittest
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendMessage

from digestbot.bot import build_dispatcher
from digestbot.db import Database
from digestbot.errors import FetchError
from digestbot.handlers import Cooldown
from digestbot.schedule import deliver_due
from digestbot.services import DataHub
from digestbot.sources.geo import GEO_URL
from tests.test_digest import FakeFetcher, routes
from tests.test_sources import GEO_JSON

TOKEN = "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
ANNA = 100
BORIS = 200

_DISPATCHER = None


def shared_dispatcher(db: Database, hub: DataHub, cooldown: Cooldown):
    """Роутер привязывается к диспетчеру один раз → на все тесты один диспетчер.
    Зависимости подменяются в workflow_data, FSM-хранилище чистится."""
    global _DISPATCHER
    if _DISPATCHER is None:
        _DISPATCHER = build_dispatcher(db, hub)
    _DISPATCHER.workflow_data.update(db=db, hub=hub, cooldown=cooldown)
    _DISPATCHER.storage.storage.clear()
    return _DISPATCHER


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.calls = []

    async def close(self):
        return None

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        return True

    async def stream_content(self, *args, **kwargs):
        yield b""

    def of(self, kind, **attrs):
        return [c for c in self.calls if isinstance(c, kind)
                and all(getattr(c, k, None) == v for k, v in attrs.items())]


def user_payload(uid: int, name: str = "Аня") -> dict:
    return {"id": uid, "is_bot": False, "first_name": name}


def message_update(n: int, uid: int, text: str) -> dict:
    msg = {"message_id": n, "date": 0, "chat": {"id": uid, "type": "private"},
           "from": user_payload(uid), "text": text}
    if text.startswith("/"):
        msg["entities"] = [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}]
    return {"update_id": n, "message": msg}


def callback_update(n: int, uid: int, data: str) -> dict:
    return {"update_id": n, "callback_query": {
        "id": str(n), "from": user_payload(uid), "chat_instance": "x", "data": data,
        "message": {"message_id": 1, "date": 0, "chat": {"id": uid, "type": "private"},
                    "from": {"id": 1, "is_bot": True, "first_name": "bot"}, "text": "старое"}}}


class FlowCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await Database.open(":memory:")
        self.fetcher = FakeFetcher(routes(**{GEO_URL: GEO_JSON}))
        self.hub = DataHub(self.fetcher)
        self.session = FakeSession()
        self.bot = Bot(TOKEN, session=self.session,
                       default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dp = shared_dispatcher(self.db, self.hub, Cooldown(seconds=15, clock=lambda: 0.0))
        self.n = 0

    async def asyncTearDown(self):
        await self.db.close()

    async def send(self, uid: int, text: str):
        self.n += 1
        await self.dp.feed_raw_update(self.bot, message_update(self.n, uid, text))

    async def press(self, uid: int, data: str):
        self.n += 1
        await self.dp.feed_raw_update(self.bot, callback_update(self.n, uid, data))

    def texts_to(self, chat_id: int) -> list[str]:
        return [c.text for c in self.session.of(SendMessage, chat_id=chat_id)]

    def last_screen(self) -> str:
        return self.session.of(EditMessageText)[-1].text

    def alerts(self) -> list[str]:
        return [c.text for c in self.session.of(AnswerCallbackQuery) if c.show_alert]


class OnboardingTest(FlowCase):
    async def test_start_creates_user_and_shows_settings_buttons(self):
        await self.send(ANNA, "/start")
        sent = self.session.of(SendMessage, chat_id=ANNA)[0]
        self.assertIn("Ежедневный дайджест", sent.text)
        self.assertIn("города", sent.text)   # подсказка начать с города
        callbacks = [b.callback_data for r in sent.reply_markup.inline_keyboard for b in r]
        self.assertIn("s:city", callbacks)
        self.assertIsNotNone(await self.db.get_user(ANNA))

    async def test_name_is_html_escaped(self):
        self.n += 1
        update = message_update(self.n, ANNA, "/start")
        update["message"]["from"]["first_name"] = "<b>Хак</b>"
        await self.dp.feed_raw_update(self.bot, update)
        self.assertNotIn("<b>Хак</b>", self.texts_to(ANNA)[0])

    async def test_help_command(self):
        await self.send(ANNA, "/help")
        self.assertIn("/digest", self.texts_to(ANNA)[0])


class CityTest(FlowCase):
    async def test_city_search_and_pick(self):
        await self.send(ANNA, "/start")
        await self.press(ANNA, "s:city")
        await self.send(ANNA, "Казань")
        options = self.session.of(SendMessage, chat_id=ANNA)[-1]
        labels = [b.text for r in options.reply_markup.inline_keyboard for b in r]
        self.assertIn("Казань, Татарстан, Россия", labels)

        await self.press(ANNA, "s:pick:0")
        user = await self.db.get_user(ANNA)
        self.assertEqual((user.city, user.tz), ("Казань, Татарстан, Россия", "Europe/Moscow"))
        self.assertTrue(user.has_city)
        self.assertIn("Казань", self.last_screen())

    async def test_unknown_city(self):
        self.fetcher.routes[GEO_URL] = json.dumps({"generationtime_ms": 1}).encode()
        await self.press(ANNA, "s:city")
        await self.send(ANNA, "Ъъъъъ")
        self.assertIn("не нашёл", self.texts_to(ANNA)[-1])
        self.assertFalse((await self.db.ensure_user(ANNA, "Аня")).has_city)

    async def test_geocoder_outage_keeps_dialog_alive(self):
        self.fetcher.routes[GEO_URL] = FetchError("down")
        await self.press(ANNA, "s:city")
        await self.send(ANNA, "Казань")
        self.assertIn("недоступен", self.texts_to(ANNA)[-1])
        self.fetcher.routes[GEO_URL] = GEO_JSON
        await self.send(ANNA, "Казань")   # состояние не потеряно
        self.assertIn("Выберите ваш город", self.texts_to(ANNA)[-1])

    async def test_too_short_query_is_refused_without_network(self):
        await self.press(ANNA, "s:city")
        await self.send(ANNA, "К")
        self.assertEqual(self.fetcher.count("geocoding"), 0)

    async def test_stale_pick_is_refused(self):
        await self.press(ANNA, "s:pick:3")
        self.assertTrue(self.alerts())
        await self.press(ANNA, "s:pick:abc")
        self.assertFalse((await self.db.ensure_user(ANNA, "Аня")).has_city)

    async def test_plain_text_outside_dialog_is_ignored(self):
        await self.send(ANNA, "просто текст")
        self.assertEqual(self.texts_to(ANNA), [])


class SelectionTest(FlowCase):
    async def test_currency_toggle_keeps_canonical_order(self):
        await self.send(ANNA, "/start")          # по умолчанию USD, EUR
        await self.press(ANNA, "s:cur:USD")
        self.assertEqual((await self.db.get_user(ANNA)).currencies, ("EUR",))
        await self.press(ANNA, "s:cur:USD")
        await self.press(ANNA, "s:cur:CNY")
        self.assertEqual((await self.db.get_user(ANNA)).currencies, ("USD", "EUR", "CNY"))

    async def test_unknown_currency_and_feed_are_ignored(self):
        await self.send(ANNA, "/start")
        before = await self.db.get_user(ANNA)
        await self.press(ANNA, "s:cur:XXX")
        await self.press(ANNA, "s:feed:nope")
        after = await self.db.get_user(ANNA)
        self.assertEqual((before.currencies, before.feeds), (after.currencies, after.feeds))

    async def test_feed_toggle(self):
        await self.send(ANNA, "/start")          # по умолчанию rbc, habr
        await self.press(ANNA, "s:feed:lenta")
        self.assertEqual((await self.db.get_user(ANNA)).feeds, ("rbc", "lenta", "habr"))

    async def test_quick_time(self):
        await self.send(ANNA, "/start")
        await self.press(ANNA, "s:t:2000")
        self.assertEqual((await self.db.get_user(ANNA)).send_time, "20:00")

    async def test_arbitrary_quick_time_is_refused(self):
        await self.send(ANNA, "/start")
        await self.press(ANNA, "s:t:0330")       # такой кнопки нет
        self.assertEqual((await self.db.get_user(ANNA)).send_time, "08:00")
        self.assertTrue(self.alerts())

    async def test_custom_time_dialog(self):
        await self.send(ANNA, "/start")
        await self.press(ANNA, "s:tcustom")
        await self.send(ANNA, "99:99")
        self.assertEqual((await self.db.get_user(ANNA)).send_time, "08:00")
        await self.send(ANNA, "7:45")
        self.assertEqual((await self.db.get_user(ANNA)).send_time, "07:45")

    async def test_enable_toggle(self):
        await self.send(ANNA, "/start")
        await self.press(ANNA, "s:toggle")
        self.assertFalse((await self.db.get_user(ANNA)).enabled)
        await self.press(ANNA, "s:toggle")
        self.assertTrue((await self.db.get_user(ANNA)).enabled)

    async def test_settings_are_per_user(self):
        await self.send(ANNA, "/start")
        await self.send(BORIS, "/start")
        await self.press(ANNA, "s:t:2000")
        self.assertEqual((await self.db.get_user(BORIS)).send_time, "08:00")


class DigestCommandTest(FlowCase):
    async def test_digest_now(self):
        await self.send(ANNA, "/start")
        await self.send(ANNA, "/digest")
        text = "\n".join(self.texts_to(ANNA))
        self.assertIn("Курсы ЦБ РФ", text)
        self.assertIn("Вторая", text)
        self.assertIn("город", text.lower())     # города ещё нет → подсказка вместо погоды

    async def test_repeated_digest_is_throttled(self):
        await self.send(ANNA, "/start")
        await self.send(ANNA, "/digest")
        before = len(self.session.of(SendMessage, chat_id=ANNA))
        await self.send(ANNA, "/digest")
        new = self.session.of(SendMessage, chat_id=ANNA)[before:]
        self.assertEqual(len(new), 1)
        self.assertIn("Подождите", new[0].text)

    async def test_button_now_is_throttled_too(self):
        await self.send(ANNA, "/start")
        await self.press(ANNA, "s:now")
        await self.press(ANNA, "s:now")
        self.assertTrue(any("Подождите" in a for a in self.alerts()))


class FakeBot:
    def __init__(self, forbidden: set[int] = frozenset()):
        self.sent: list[tuple[int, str]] = []
        self.forbidden = forbidden

    async def send_message(self, chat_id: int, text: str):
        if chat_id in self.forbidden:
            raise TelegramForbiddenError(
                method=SendMessage(chat_id=chat_id, text=text),
                message="Forbidden: bot was blocked by the user")
        self.sent.append((chat_id, text))


class SchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = await Database.open(":memory:")
        self.hub = DataHub(FakeFetcher(routes()))

    async def asyncTearDown(self):
        await self.db.close()

    async def test_due_user_gets_digest_once_per_day(self):
        await self.db.ensure_user(1, "Аня")   # 08:00 МСК
        bot = FakeBot()
        at_0805 = datetime(2026, 10, 12, 5, 5, tzinfo=timezone.utc)
        self.assertEqual(await deliver_due(bot, self.db, self.hub, at_0805), 1)
        self.assertEqual(await deliver_due(bot, self.db, self.hub, at_0805), 0)
        self.assertTrue(all(chat == 1 for chat, _ in bot.sent))
        self.assertEqual((await self.db.get_user(1)).last_sent_date, "2026-10-12")

    async def test_next_day_sends_again(self):
        await self.db.ensure_user(1, "Аня")
        bot = FakeBot()
        await deliver_due(bot, self.db, self.hub, datetime(2026, 10, 12, 5, 5, tzinfo=timezone.utc))
        self.assertEqual(
            await deliver_due(bot, self.db, self.hub, datetime(2026, 10, 13, 5, 5, tzinfo=timezone.utc)), 1)

    async def test_not_due_before_time(self):
        await self.db.ensure_user(1, "Аня")
        bot = FakeBot()
        at_0700 = datetime(2026, 10, 12, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(await deliver_due(bot, self.db, self.hub, at_0700), 0)
        self.assertEqual(bot.sent, [])

    async def test_uses_users_own_timezone(self):
        await self.db.ensure_user(1, "Аня")
        await self.db.set_city(1, "Екатеринбург", 56.84, 60.6, "Asia/Yekaterinburg")   # UTC+5
        bot = FakeBot()
        # 03:05 UTC = 08:05 в Екатеринбурге, но только 06:05 в Москве
        self.assertEqual(
            await deliver_due(bot, self.db, self.hub, datetime(2026, 10, 12, 3, 5, tzinfo=timezone.utc)), 1)

    async def test_disabled_user_is_skipped(self):
        await self.db.ensure_user(1, "Аня")
        await self.db.set_enabled(1, False)
        bot = FakeBot()
        self.assertEqual(
            await deliver_due(bot, self.db, self.hub, datetime(2026, 10, 12, 5, 5, tzinfo=timezone.utc)), 0)

    async def test_blocked_user_is_disabled_and_others_still_served(self):
        await self.db.ensure_user(1, "Аня")
        await self.db.ensure_user(2, "Боря")
        bot = FakeBot(forbidden={1})
        sent = await deliver_due(bot, self.db, self.hub, datetime(2026, 10, 12, 5, 5, tzinfo=timezone.utc))
        self.assertEqual(sent, 1)
        self.assertFalse((await self.db.get_user(1)).enabled)
        self.assertTrue((await self.db.get_user(2)).enabled)
        self.assertEqual({chat for chat, _ in bot.sent}, {2})

    async def test_failed_delivery_is_retried_next_tick(self):
        await self.db.ensure_user(1, "Аня")

        class Flaky(FakeBot):
            calls = 0

            async def send_message(self, chat_id, text):
                Flaky.calls += 1
                if Flaky.calls == 1:
                    raise RuntimeError("сеть моргнула")
                await super().send_message(chat_id, text)

        bot = Flaky()
        now = datetime(2026, 10, 12, 5, 5, tzinfo=timezone.utc)
        self.assertEqual(await deliver_due(bot, self.db, self.hub, now), 0)
        self.assertIsNone((await self.db.get_user(1)).last_sent_date)
        self.assertEqual(await deliver_due(bot, self.db, self.hub, now), 1)


if __name__ == "__main__":
    unittest.main()
