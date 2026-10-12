"""Inline-клавиатуры. callback_data: s:home | s:now | s:toggle | s:city | s:pick:<i> |
s:cur | s:cur:<CODE> | s:feeds | s:feed:<id> | s:time | s:t:<HHMM> | s:tcustom"""
from typing import Sequence

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import CURRENCIES, FEEDS, QUICK_TIMES
from .db import UserSettings
from .sources.geo import Place


def _markup(builder: InlineKeyboardBuilder, *sizes: int) -> InlineKeyboardMarkup:
    builder.adjust(*sizes)
    return builder.as_markup()


def _mark(on: bool) -> str:
    return "✅" if on else "▫️"


def settings_kb(user: UserSettings) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📍 Город", callback_data="s:city")
    b.button(text="💱 Валюты", callback_data="s:cur")
    b.button(text="📰 Источники", callback_data="s:feeds")
    b.button(text="⏰ Время", callback_data="s:time")
    b.button(text="🔕 Выключить" if user.enabled else "🔔 Включить", callback_data="s:toggle")
    b.button(text="📨 Прислать сейчас", callback_data="s:now")
    return _markup(b, 2, 2, 1, 1)


def back_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="← Настройки", callback_data="s:home")
    return _markup(b, 1)


def currencies_kb(selected: Sequence[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for code in CURRENCIES:
        b.button(text=f"{_mark(code in selected)} {code}", callback_data=f"s:cur:{code}")
    b.button(text="← Настройки", callback_data="s:home")
    return _markup(b, 4, 4, 1)


def feeds_kb(selected: Sequence[str]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for feed in FEEDS.values():
        b.button(text=f"{_mark(feed.id in selected)} {feed.name}", callback_data=f"s:feed:{feed.id}")
    b.button(text="← Настройки", callback_data="s:home")
    return _markup(b, *([1] * len(FEEDS)), 1)


def time_kb(current: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for value in QUICK_TIMES:
        label = f"✅ {value}" if value == current else value
        b.button(text=label, callback_data=f"s:t:{value.replace(':', '')}")
    b.button(text="✏️ Своё время", callback_data="s:tcustom")
    b.button(text="← Настройки", callback_data="s:home")
    return _markup(b, 4, 4, 1, 1)


def cities_kb(places: Sequence[Place]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for index, place in enumerate(places):
        b.button(text=place.label[:60], callback_data=f"s:pick:{index}")
    b.button(text="← Настройки", callback_data="s:home")
    return _markup(b, *([1] * len(places)), 1)
