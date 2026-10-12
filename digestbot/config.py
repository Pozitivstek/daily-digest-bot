"""Настройки из .env и справочники: новостные источники, валюты, значения по умолчанию."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Feed:
    id: str
    name: str
    url: str


# порядок = порядок кнопок и разделов в дайджесте
FEEDS: dict[str, Feed] = {f.id: f for f in (
    Feed("rbc", "РБК", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
    Feed("lenta", "Лента.ру", "https://lenta.ru/rss/news"),
    Feed("habr", "Хабр", "https://habr.com/ru/rss/news/?fl=ru"),
    Feed("opennet", "OpenNet", "https://www.opennet.ru/opennews/opennews_all.rss"),
    Feed("realpython", "Real Python", "https://realpython.com/atom.xml"),
)}

CURRENCIES = ("USD", "EUR", "CNY", "GBP", "TRY", "KZT", "JPY", "CHF")

DEFAULT_CURRENCIES = ("USD", "EUR")
DEFAULT_FEEDS = ("rbc", "habr")
DEFAULT_SEND_TIME = "08:00"
DEFAULT_TZ = "Europe/Moscow"

NEWS_PER_FEED = 3
QUICK_TIMES = ("07:00", "08:00", "09:00", "10:00", "12:00", "18:00", "20:00", "21:00")

# сколько секунд живут данные в общем кэше
RATES_TTL = 30 * 60
WEATHER_TTL = 20 * 60
NEWS_TTL = 10 * 60


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: Path
    http_timeout: float


def load_settings() -> Settings:
    load_dotenv(ROOT / ".env")
    return Settings(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        db_path=Path(os.getenv("DB_PATH") or ROOT / "data" / "digest.db"),
        http_timeout=float(os.getenv("HTTP_TIMEOUT", "10")),
    )
