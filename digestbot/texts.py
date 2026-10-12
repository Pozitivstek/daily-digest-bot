"""Тексты бота (HTML parse mode — всё пользовательское экранируется)."""
from html import escape

from .config import FEEDS
from .db import UserSettings

HELP_TEXT = (
    "📬 <b>Ежедневный дайджест</b>\n\n"
    "Раз в день присылаю одним сообщением:\n"
    "• погоду в вашем городе,\n"
    "• курсы валют ЦБ РФ с изменением за день,\n"
    "• свежие новости из выбранных источников.\n\n"
    "Команды:\n"
    "/digest — получить дайджест сейчас\n"
    "/settings — город, валюты, источники и время\n"
    "/help — эта справка"
)


def welcome_text(name: str, user: UserSettings) -> str:
    text = f"Привет, {escape(name)}! 👋\n\n" + HELP_TEXT
    if not user.has_city:
        text += "\n\n📍 Начните с города: нажмите «Город» ниже — тогда добавлю погоду."
    return text


def settings_text(user: UserSettings) -> str:
    feeds = ", ".join(FEEDS[f].name for f in user.feeds if f in FEEDS) or "не выбраны"
    currencies = ", ".join(user.currencies) or "не выбраны"
    status = "включена" if user.enabled else "выключена"
    return (
        "⚙️ <b>Настройки дайджеста</b>\n\n"
        f"📍 Город: {escape(user.city) if user.has_city else 'не выбран'}\n"
        f"💱 Валюты: {currencies}\n"
        f"📰 Источники: {escape(feeds)}\n"
        f"⏰ Время: {user.send_time} (по времени вашего города)\n"
        f"🔔 Рассылка: {status}"
    )
