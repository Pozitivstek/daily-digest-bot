"""Сборка текста дайджеста. Каждая секция независима: отказ одного источника не ломает остальные."""
import asyncio
from datetime import datetime
from decimal import Decimal
from html import escape

from .config import FEEDS, NEWS_PER_FEED
from .db import UserSettings
from .errors import SourceError
from .services import DataHub
from .sources.weather import describe

TG_LIMIT = 4096
NBSP = " "
MINUS = "−"

MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
          "сентября", "октября", "ноября", "декабря")
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")


def ru_number(value: float | Decimal, digits: int = 2, *, signed: bool = False) -> str:
    """95.1234 → «95,12»; 1234.5 → «1 234,5»; со знаком: «+0,34» / «−0,30» (ноль — без знака)."""
    text = f"{abs(value):,.{digits}f}".replace(",", NBSP).replace(".", ",")
    if not signed or not any(ch in "123456789" for ch in text):
        return text
    return ("+" if value > 0 else MINUS) + text


def greeting(hour: int) -> str:
    if hour < 5:
        return "Доброй ночи"
    if hour < 12:
        return "Доброе утро"
    if hour < 18:
        return "Добрый день"
    return "Добрый вечер"


def _temp(value: float) -> str:
    rounded = round(value)
    if rounded == 0:
        return "0°"
    return f"{'+' if rounded > 0 else MINUS}{abs(rounded)}°"


def split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """Режет по строкам, чтобы не рвать ссылки; слишком длинную строку — жёстко."""
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current or not parts:
        parts.append(current)
    return parts


# ---------------------------------------------------------------- секции

def _header(user: UserSettings, now_local: datetime) -> str:
    name = f", {escape(user.name)}" if user.name else ""
    date_line = (f"{WEEKDAYS[now_local.weekday()].capitalize()}, "
                 f"{now_local.day} {MONTHS[now_local.month - 1]}")
    return f"☀️ <b>{greeting(now_local.hour)}{name}!</b>\n{date_line}"


async def _weather(user: UserSettings, hub: DataHub) -> str:
    if not user.has_city:
        return "📍 Укажите город в /settings, чтобы получать погоду."
    try:
        w = await hub.forecast(user.lat, user.lon)
    except SourceError:
        return f"🌡 <b>Погода · {escape(user.city)}</b>\nДанные временно недоступны."
    emoji, now_text = describe(w.code)
    _, day_text = describe(w.day_code)
    details = [f"{_temp(w.temp_min)}…{_temp(w.temp_max)}"]
    if w.precip_prob is not None:
        details.append(f"осадки {w.precip_prob}%")
    details.append(f"ветер до {ru_number(w.wind_max, 0)} м/с")
    return (f"{emoji} <b>Погода · {escape(user.city.split(',')[0])}</b>\n"
            f"Сейчас {_temp(w.temp_now)}, {now_text}\n"
            f"Сегодня: {day_text}, {', '.join(details)}")


async def _rates(user: UserSettings, hub: DataHub) -> str | None:
    if not user.currencies:
        return None
    try:
        latest, previous = await hub.rates()
    except SourceError:
        return "💱 <b>Курсы ЦБ РФ</b>\nДанные временно недоступны."
    rows = [f"💱 <b>Курсы ЦБ РФ на {latest.on_date:%d.%m}</b>"]
    for code in user.currencies:
        rate = latest.rates.get(code)
        if rate is None:
            continue
        label = f"{rate.nominal} {code}" if rate.nominal != 1 else code
        row = f"{label} — {ru_number(rate.value)} ₽"
        before = previous.rates.get(code) if previous else None
        if before is not None:
            row += f" ({ru_number(rate.value - before.value, signed=True)})"
        rows.append(row)
    return "\n".join(rows) if len(rows) > 1 else None


async def _feed_block(feed_id: str, hub: DataHub) -> str:
    feed = FEEDS[feed_id]
    try:
        items = await hub.news(feed, NEWS_PER_FEED)
    except SourceError:
        return f"<b>{escape(feed.name)}</b>: временно недоступно"
    if not items:
        return f"<b>{escape(feed.name)}</b>: свежих новостей нет"
    lines = [f'• <a href="{escape(item.link, quote=True)}">{escape(item.title)}</a>' for item in items]
    return f"<b>{escape(feed.name)}</b>\n" + "\n".join(lines)


async def _news(user: UserSettings, hub: DataHub) -> str | None:
    feed_ids = [f for f in user.feeds if f in FEEDS]
    if not feed_ids:
        return None
    blocks = await asyncio.gather(*(_feed_block(f, hub) for f in feed_ids))
    return "📰 <b>Новости</b>\n\n" + "\n\n".join(blocks)


async def build_digest(user: UserSettings, hub: DataHub, now_local: datetime) -> list[str]:
    """Готовые к отправке сообщения (каждое ≤ 4096 символов)."""
    weather, rates, news = await asyncio.gather(_weather(user, hub), _rates(user, hub), _news(user, hub))
    sections = [_header(user, now_local), weather, rates, news]
    return split_message("\n\n".join(s for s in sections if s))
