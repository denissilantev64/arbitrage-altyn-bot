from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage, SimpleEventIsolation
from aiogram.types import BotCommand

from .config import Settings
from .constants import HTTP_TIMEOUT_SECONDS
from .handlers import create_router
from .rates import RateCollector
from .repository import SQLiteRepository
from .scheduler import collect_and_store_rates, morning_broadcast_loop, rate_refresh_loop

logger = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def run(settings: Settings) -> None:
    _ensure_database_directory(settings.database_path)
    repository = SQLiteRepository(settings.database_path)
    await repository.connect()
    try:
        await repository.initialize()
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(
            timeout=timeout,
            headers={"User-Agent": "arbitrage-altyn-bot/0.1"},
        ) as http_session:
            collector = RateCollector(
                http_session,
                altyn_buy_fee_rate=settings.altyn_buy_fee_rate,
                altyn_sell_fee_rate=settings.altyn_sell_fee_rate,
            )
            await collect_and_store_rates(collector, repository)

            bot = Bot(
                settings.telegram_bot_token,
                default=DefaultBotProperties(
                    parse_mode=ParseMode.HTML,
                    link_preview_is_disabled=True,
                ),
            )
            dispatcher = build_dispatcher(repository, settings)
            stop_event = asyncio.Event()
            polling_task: asyncio.Task[None] | None = None
            rate_task: asyncio.Task[None] | None = None
            broadcast_task: asyncio.Task[None] | None = None
            try:
                await bot.delete_webhook(drop_pending_updates=False)
                await bot.set_my_commands(
                    [
                        BotCommand(command="start", description="открыть меню"),
                        BotCommand(command="spread", description="показать спред USDT/RUB"),
                        BotCommand(command="subscribe", description="включить уведомления"),
                        BotCommand(command="unsubscribe", description="отключить уведомления"),
                        BotCommand(command="help", description="команды бота"),
                    ]
                )
                rate_task = asyncio.create_task(
                    rate_refresh_loop(collector, repository, stop_event),
                    name="rate-refresh",
                )
                broadcast_task = asyncio.create_task(
                    morning_broadcast_loop(bot, repository, stop_event),
                    name="morning-broadcast",
                )
                logger.info("Starting Telegram long polling")
                polling_task = asyncio.create_task(
                    dispatcher.start_polling(
                        bot,
                        close_bot_session=False,
                        allowed_updates=dispatcher.resolve_used_update_types(),
                        handle_as_tasks=False,
                    ),
                    name="telegram-polling",
                )
                await _supervise_runtime(
                    polling_task,
                    rate_task,
                    broadcast_task,
                )
            finally:
                stop_event.set()
                await _cancel_tasks(polling_task, rate_task, broadcast_task)
                await dispatcher.storage.close()
                await bot.session.close()
    finally:
        await repository.close()


def _ensure_database_directory(database_path: Path) -> None:
    parent = database_path.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    if not parent.is_dir():
        raise RuntimeError(f"database parent path is not a directory: {parent}")


def build_dispatcher(repository: SQLiteRepository, settings: Settings) -> Dispatcher:
    dispatcher = Dispatcher(
        storage=MemoryStorage(),
        events_isolation=SimpleEventIsolation(),
    )
    dispatcher.include_router(create_router(repository, settings))
    return dispatcher


async def _cancel_tasks(*tasks: asyncio.Task[None] | None) -> None:
    active = [task for task in tasks if task is not None]
    for task in active:
        task.cancel()
    if active:
        await asyncio.gather(*active, return_exceptions=True)


async def _supervise_runtime(
    polling_task: asyncio.Task[None],
    rate_task: asyncio.Task[None],
    broadcast_task: asyncio.Task[None],
) -> None:
    tasks = {polling_task, rate_task, broadcast_task}
    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

    for task in done:
        if task is polling_task:
            continue
        try:
            await task
        except asyncio.CancelledError:
            raise
        raise RuntimeError(f"background task {task.get_name()!r} stopped unexpectedly")

    if polling_task in done:
        await polling_task
        raise RuntimeError("Telegram polling stopped unexpectedly")

    raise RuntimeError("runtime supervisor reached an invalid state")


def main() -> None:
    configure_logging()
    settings = Settings.from_environment()
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        logger.info("Bot stopped by operator")


if __name__ == "__main__":
    main()
