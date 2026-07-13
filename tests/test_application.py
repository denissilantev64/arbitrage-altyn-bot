from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiogram.fsm.storage.base import BaseEventIsolation
from aiogram.fsm.storage.memory import SimpleEventIsolation

from arbitrage_bot.application import _cancel_tasks, _supervise_runtime, build_dispatcher
from arbitrage_bot.config import Settings
from arbitrage_bot.repository import SQLiteRepository


async def _wait_forever() -> None:
    await asyncio.Event().wait()


async def _fail(message: str) -> None:
    await asyncio.sleep(0)
    raise RuntimeError(message)


async def test_supervisor_propagates_background_failure() -> None:
    polling = asyncio.create_task(_wait_forever(), name="polling")
    rate = asyncio.create_task(_fail("rate failed"), name="rate")
    broadcast = asyncio.create_task(_wait_forever(), name="broadcast")
    try:
        with pytest.raises(RuntimeError, match="rate failed"):
            await _supervise_runtime(polling, rate, broadcast)
    finally:
        await _cancel_tasks(polling, rate, broadcast)


async def test_supervisor_treats_normal_polling_stop_as_failure() -> None:
    async def completed() -> None:
        return None

    polling = asyncio.create_task(completed(), name="polling")
    rate = asyncio.create_task(_wait_forever(), name="rate")
    broadcast = asyncio.create_task(_wait_forever(), name="broadcast")
    try:
        with pytest.raises(RuntimeError, match="polling stopped unexpectedly"):
            await _supervise_runtime(polling, rate, broadcast)
    finally:
        await _cancel_tasks(polling, rate, broadcast)


def test_dispatcher_uses_event_isolation() -> None:
    repository = SQLiteRepository(":memory:")
    settings = Settings(
        telegram_bot_token="123456789:test-token",
        database_path=Path(":memory:"),
        support_url="https://t.me/darkvasyak",
    )

    dispatcher = build_dispatcher(repository, settings)

    isolation: BaseEventIsolation = dispatcher.fsm.events_isolation
    assert isinstance(isolation, SimpleEventIsolation)
