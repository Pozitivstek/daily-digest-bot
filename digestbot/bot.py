"""Сборка диспетчера и запуск: polling + фоновая рассылка."""
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, ErrorEvent, LinkPreviewOptions

from . import handlers
from .config import load_settings
from .db import Database
from .http import HttpFetcher
from .schedule import run_scheduler
from .services import DataHub

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="digest", description="Получить дайджест сейчас"),
    BotCommand(command="settings", description="Город, валюты, источники, время"),
    BotCommand(command="help", description="Справка"),
]


def build_dispatcher(db: Database, hub: DataHub) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage(), db=db, hub=hub, cooldown=handlers.Cooldown())
    dp.include_router(handlers.router)

    @dp.error()
    async def on_error(event: ErrorEvent) -> bool:
        log.error("необработанная ошибка в обработчике", exc_info=event.exception)
        call = event.update.callback_query
        if call is not None:
            try:
                await call.answer("Что-то пошло не так. Попробуйте ещё раз.", show_alert=True)
            except Exception:
                log.debug("не удалось ответить на callback после ошибки", exc_info=True)
        return True

    return dp


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    if not settings.bot_token:
        raise SystemExit("Не задан BOT_TOKEN: скопируйте .env.example в .env и впишите токен от @BotFather")

    db = await Database.open(settings.db_path)
    fetcher = HttpFetcher(timeout=settings.http_timeout)
    hub = DataHub(fetcher)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(
        parse_mode=ParseMode.HTML, link_preview=LinkPreviewOptions(is_disabled=True)))
    dp = build_dispatcher(db, hub)
    scheduler = asyncio.create_task(run_scheduler(bot, db, hub))
    try:
        await bot.set_my_commands(COMMANDS)
        await dp.start_polling(bot)
    finally:
        scheduler.cancel()
        await asyncio.gather(scheduler, return_exceptions=True)
        await fetcher.close()
        await db.close()
        await bot.session.close()


def run() -> None:
    asyncio.run(main())
