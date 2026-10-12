"""Расписание: когда пора слать дайджест и сам цикл рассылки."""
import asyncio
import logging
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from .config import DEFAULT_TZ
from .db import Database
from .digest import build_digest
from .services import DataHub

log = logging.getLogger(__name__)

GRACE = timedelta(hours=3)        # после простоя бот ещё догонит рассылку в пределах этого окна
TICK_SECONDS = 30
SEND_PAUSE = 0.05                 # лимит Telegram — около 30 сообщений в секунду

_TIME_RE = re.compile(r"^([0-9]{1,2}):([0-9]{2})$")


def parse_time(text: str) -> str | None:
    """«8:30» → «08:30»; None, если это не время суток."""
    match = _TIME_RE.match((text or "").strip())
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def local_now(tz_name: str, now_utc: datetime | None = None) -> datetime:
    """Текущее местное время (naive). Неизвестный пояс → московский."""
    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        zone = ZoneInfo(tz_name)
    except Exception:   # ZoneInfoNotFoundError, ValueError, OSError
        zone = ZoneInfo(DEFAULT_TZ)
    return now_utc.astimezone(zone).replace(tzinfo=None)


def is_due(now_local: datetime, send_time: str, last_sent_date: str | None,
           grace: timedelta = GRACE) -> bool:
    parsed = parse_time(send_time)
    if parsed is None or last_sent_date == now_local.date().isoformat():
        return False
    hour, minute = map(int, parsed.split(":"))
    scheduled = datetime.combine(now_local.date(), time(hour, minute))
    return timedelta(0) <= now_local - scheduled < grace


async def deliver_due(bot: Bot, db: Database, hub: DataHub, now_utc: datetime | None = None) -> int:
    """Один проход: шлёт дайджест всем, кому пора. Возвращает число отправленных."""
    sent = 0
    for user in await db.enabled_users():
        now_local = local_now(user.tz, now_utc)
        if not is_due(now_local, user.send_time, user.last_sent_date):
            continue
        try:
            for part in await build_digest(user, hub, now_local):
                await bot.send_message(user.user_id, part)
        except TelegramForbiddenError:
            log.info("пользователь %s заблокировал бота — рассылка отключена", user.user_id)
            await db.set_enabled(user.user_id, False)
            continue
        except TelegramRetryAfter as exc:
            log.warning("лимит Telegram, пауза %s с", exc.retry_after)
            await asyncio.sleep(exc.retry_after)
            continue   # отметки нет → попробуем на следующем проходе
        except Exception:
            log.exception("не удалось отправить дайджест пользователю %s", user.user_id)
            continue
        await db.mark_sent(user.user_id, now_local.date().isoformat())
        sent += 1
        await asyncio.sleep(SEND_PAUSE)
    return sent


async def run_scheduler(bot: Bot, db: Database, hub: DataHub, tick: float = TICK_SECONDS) -> None:
    while True:
        try:
            await deliver_due(bot, db, hub)
        except Exception:
            log.exception("сбой прохода рассылки")
        await asyncio.sleep(tick)
