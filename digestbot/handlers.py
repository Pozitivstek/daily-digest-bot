"""Диалог с пользователем: настройки через кнопки, город и время — текстом."""
import logging
import time
from dataclasses import asdict
from typing import Callable

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from . import keyboards as kb
from .choice import toggle
from .config import CURRENCIES, FEEDS, QUICK_TIMES
from .db import Database, UserSettings
from .digest import build_digest
from .errors import SourceError
from .schedule import local_now, parse_time
from .services import DataHub
from .sources.geo import Place
from .texts import HELP_TEXT, settings_text, welcome_text

log = logging.getLogger(__name__)
router = Router(name="digest")

MAX_CITY_QUERY = 60
MIN_CITY_QUERY = 2
MANUAL_COOLDOWN_SECONDS = 15


class CityInput(StatesGroup):
    waiting = State()


class TimeInput(StatesGroup):
    waiting = State()


class Cooldown:
    """Не даёт сыпать «Прислать сейчас» чаще, чем раз в N секунд."""

    def __init__(self, seconds: float = MANUAL_COOLDOWN_SECONDS, clock: Callable[[], float] = time.monotonic):
        self._seconds = seconds
        self._clock = clock
        self._last: dict[int, float] = {}

    def ready(self, key: int) -> bool:
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < self._seconds:
            return False
        self._last[key] = now
        return True


def part(data: str | None, index: int) -> str:
    pieces = (data or "").split(":")
    return pieces[index] if len(pieces) > index else ""


async def show(target: Message | CallbackQuery, text: str, markup: InlineKeyboardMarkup | None = None,
               toast: str | None = None) -> None:
    if isinstance(target, CallbackQuery):
        if isinstance(target.message, Message):
            try:
                await target.message.edit_text(text, reply_markup=markup)
            except TelegramBadRequest as exc:
                if "not modified" not in str(exc):
                    await target.message.answer(text, reply_markup=markup)
        else:
            await target.bot.send_message(target.from_user.id, text, reply_markup=markup)
        await target.answer(toast)
    else:
        await target.answer(text, reply_markup=markup)


async def current_user(target: Message | CallbackQuery, db: Database) -> UserSettings:
    tg = target.from_user
    return await db.ensure_user(tg.id, tg.first_name or tg.full_name)


async def show_settings(target: Message | CallbackQuery, db: Database, toast: str | None = None) -> None:
    user = await current_user(target, db)
    await show(target, settings_text(user), kb.settings_kb(user), toast)


async def send_digest(bot: Bot, chat_id: int, user: UserSettings, hub: DataHub) -> None:
    for text in await build_digest(user, hub, local_now(user.tz)):
        await bot.send_message(chat_id, text)


# ---------------------------------------------------------------- команды

@router.message(CommandStart())
async def start(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    user = await current_user(message, db)
    name = message.from_user.first_name or message.from_user.full_name
    await message.answer(welcome_text(name, user), reply_markup=kb.settings_kb(user))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("settings"))
async def cmd_settings(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    await show_settings(message, db)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, db: Database, state: FSMContext) -> None:
    await state.clear()
    await show_settings(message, db)


@router.message(Command("digest"))
async def cmd_digest(message: Message, bot: Bot, db: Database, hub: DataHub, cooldown: Cooldown) -> None:
    user = await current_user(message, db)
    if not cooldown.ready(user.user_id):
        await message.answer("Подождите несколько секунд и попробуйте снова ⏳")
        return
    await send_digest(bot, message.chat.id, user, hub)


# ---------------------------------------------------------------- настройки

@router.callback_query(F.data == "s:home")
async def cb_home(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    await state.clear()
    await show_settings(call, db)


@router.callback_query(F.data == "s:now")
async def cb_now(call: CallbackQuery, bot: Bot, db: Database, hub: DataHub, cooldown: Cooldown) -> None:
    user = await current_user(call, db)
    if not cooldown.ready(user.user_id):
        await call.answer("Подождите несколько секунд ⏳", show_alert=True)
        return
    await call.answer("Собираю дайджест…")
    await send_digest(bot, call.from_user.id, user, hub)


@router.callback_query(F.data == "s:toggle")
async def cb_toggle(call: CallbackQuery, db: Database) -> None:
    user = await current_user(call, db)
    await db.set_enabled(user.user_id, not user.enabled)
    await show_settings(call, db, toast="Рассылка включена" if not user.enabled else "Рассылка выключена")


# --- город

@router.callback_query(F.data == "s:city")
async def cb_city(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(CityInput.waiting)
    await show(call, "📍 Напишите название города, например <i>Казань</i>.\n\n/cancel — отмена",
               kb.back_kb())


@router.message(CityInput.waiting, F.text)
async def city_query(message: Message, db: Database, hub: DataHub, state: FSMContext) -> None:
    query = (message.text or "").strip()
    if not MIN_CITY_QUERY <= len(query) <= MAX_CITY_QUERY:
        await message.answer("Напишите название города (от 2 до 60 символов) или /cancel.")
        return
    try:
        places = await hub.places(query)
    except SourceError:
        log.warning("геокодер недоступен", exc_info=True)
        await message.answer("Поиск городов сейчас недоступен. Попробуйте чуть позже или /cancel.")
        return
    if not places:
        await message.answer("Такого города не нашёл 🤔 Проверьте написание или попробуйте ближайший крупный.")
        return
    await state.set_state(None)
    await state.update_data(places=[asdict(p) for p in places])
    await message.answer("Выберите ваш город:", reply_markup=kb.cities_kb(places))


@router.callback_query(F.data.startswith("s:pick:"))
async def cb_pick(call: CallbackQuery, db: Database, state: FSMContext) -> None:
    raw = (await state.get_data()).get("places") or []
    index = part(call.data, 2)
    if not index.isdigit() or int(index) >= len(raw):
        await call.answer("Список устарел — найдите город заново", show_alert=True)
        return
    place = Place(**raw[int(index)])
    await current_user(call, db)
    await db.set_city(call.from_user.id, place.label, place.lat, place.lon, place.tz)
    await state.clear()
    await show_settings(call, db, toast="Город сохранён")


# --- валюты и источники

@router.callback_query(F.data == "s:cur")
async def cb_currencies(call: CallbackQuery, db: Database) -> None:
    user = await current_user(call, db)
    await show(call, "💱 <b>Валюты</b>\nКурсы ЦБ РФ в дайджесте:", kb.currencies_kb(user.currencies))


@router.callback_query(F.data.startswith("s:cur:"))
async def cb_currency_toggle(call: CallbackQuery, db: Database) -> None:
    user = await current_user(call, db)
    chosen = toggle(user.currencies, part(call.data, 2), CURRENCIES)
    await db.set_currencies(user.user_id, chosen)
    await show(call, "💱 <b>Валюты</b>\nКурсы ЦБ РФ в дайджесте:", kb.currencies_kb(chosen))


@router.callback_query(F.data == "s:feeds")
async def cb_feeds(call: CallbackQuery, db: Database) -> None:
    user = await current_user(call, db)
    await show(call, "📰 <b>Источники новостей</b>\nПо 3 свежие новости из каждого:", kb.feeds_kb(user.feeds))


@router.callback_query(F.data.startswith("s:feed:"))
async def cb_feed_toggle(call: CallbackQuery, db: Database) -> None:
    user = await current_user(call, db)
    chosen = toggle(user.feeds, part(call.data, 2), FEEDS)
    await db.set_feeds(user.user_id, chosen)
    await show(call, "📰 <b>Источники новостей</b>\nПо 3 свежие новости из каждого:", kb.feeds_kb(chosen))


# --- время

@router.callback_query(F.data == "s:time")
async def cb_time(call: CallbackQuery, db: Database) -> None:
    user = await current_user(call, db)
    await show(call, "⏰ <b>Во сколько присылать?</b>\nВремя — по часовому поясу вашего города.",
               kb.time_kb(user.send_time))


@router.callback_query(F.data.startswith("s:t:"))
async def cb_time_set(call: CallbackQuery, db: Database) -> None:
    raw = part(call.data, 2)
    value = f"{raw[:2]}:{raw[2:]}"
    if value not in QUICK_TIMES:
        await call.answer("Выберите время из списка", show_alert=True)
        return
    user = await current_user(call, db)
    await db.set_send_time(user.user_id, value)
    await show_settings(call, db, toast=f"Буду присылать в {value}")


@router.callback_query(F.data == "s:tcustom")
async def cb_time_custom(call: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TimeInput.waiting)
    await show(call, "✏️ Напишите время в формате <b>ЧЧ:ММ</b>, например <i>07:45</i>.\n\n/cancel — отмена",
               kb.back_kb())


@router.message(TimeInput.waiting, F.text)
async def time_value(message: Message, db: Database, state: FSMContext) -> None:
    value = parse_time(message.text or "")
    if value is None:
        await message.answer("Не похоже на время. Формат — ЧЧ:ММ, например 07:45. Или /cancel.")
        return
    user = await current_user(message, db)
    await db.set_send_time(user.user_id, value)
    await state.clear()
    await show_settings(message, db)
