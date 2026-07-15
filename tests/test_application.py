from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiogram.fsm.storage.base import BaseEventIsolation
from aiogram.fsm.storage.memory import SimpleEventIsolation

from arbitrage_bot import application
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
    shutdown_event = asyncio.Event()
    try:
        with pytest.raises(RuntimeError, match="rate failed"):
            await _supervise_runtime(polling, rate, broadcast, shutdown_event)
    finally:
        await _cancel_tasks(polling, rate, broadcast)


async def test_supervisor_treats_normal_polling_stop_as_failure() -> None:
    async def completed() -> None:
        return None

    polling = asyncio.create_task(completed(), name="polling")
    rate = asyncio.create_task(_wait_forever(), name="rate")
    broadcast = asyncio.create_task(_wait_forever(), name="broadcast")
    shutdown_event = asyncio.Event()
    try:
        with pytest.raises(RuntimeError, match="polling stopped unexpectedly"):
            await _supervise_runtime(polling, rate, broadcast, shutdown_event)
    finally:
        await _cancel_tasks(polling, rate, broadcast)


async def test_supervisor_treats_normal_background_stop_as_failure() -> None:
    async def completed() -> None:
        return None

    polling = asyncio.create_task(_wait_forever(), name="polling")
    rate = asyncio.create_task(completed(), name="rate")
    broadcast = asyncio.create_task(_wait_forever(), name="broadcast")
    shutdown_event = asyncio.Event()
    try:
        with pytest.raises(RuntimeError, match="background task 'rate' stopped unexpectedly"):
            await _supervise_runtime(polling, rate, broadcast, shutdown_event)
    finally:
        await _cancel_tasks(polling, rate, broadcast)


async def test_supervisor_returns_only_after_explicit_shutdown_request() -> None:
    polling = asyncio.create_task(_wait_forever(), name="polling")
    rate = asyncio.create_task(_wait_forever(), name="rate")
    broadcast = asyncio.create_task(_wait_forever(), name="broadcast")
    shutdown_event = asyncio.Event()
    shutdown_event.set()
    try:
        await _supervise_runtime(polling, rate, broadcast, shutdown_event)
        assert not polling.done()
        assert not rate.done()
        assert not broadcast.done()
    finally:
        await _cancel_tasks(polling, rate, broadcast)


def test_dispatcher_uses_event_isolation() -> None:
    repository = SQLiteRepository(":memory:")

    dispatcher = build_dispatcher(repository, "https://t.me/manager_altyn_bot")

    isolation: BaseEventIsolation = dispatcher.fsm.events_isolation
    assert isinstance(isolation, SimpleEventIsolation)


async def test_run_passes_altyn_token_to_collector(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class ExpectedStop(Exception):
        pass

    class FakeRepository:
        def __init__(self, database_path: Path) -> None:
            assert database_path == tmp_path / "bot.sqlite3"

        async def connect(self) -> None:
            return None

        async def initialize(self) -> None:
            return None

        async def close(self) -> None:
            return None

    session = object()

    class FakeClientSession:
        def __init__(self, **kwargs: object) -> None:
            assert "timeout" in kwargs

        async def __aenter__(self) -> object:
            return session

        async def __aexit__(self, *args: object) -> None:
            return None

    captured: dict[str, object] = {}

    class FakeRateCollector:
        def __init__(
            self,
            received_session: object,
            *,
            altyn_arbitrage_token: str,
        ) -> None:
            captured.update(
                session=received_session,
                token=altyn_arbitrage_token,
            )

    async def stop_after_collector_creation(*args: object) -> None:
        raise ExpectedStop

    monkeypatch.setattr(application, "SQLiteRepository", FakeRepository)
    monkeypatch.setattr(application.aiohttp, "ClientSession", FakeClientSession)
    monkeypatch.setattr(application, "RateCollector", FakeRateCollector)
    monkeypatch.setattr(application, "collect_and_store_rates", stop_after_collector_creation)

    settings = Settings(
        telegram_bot_token="123456789:test-token",
        altyn_arbitrage_token="a" * 64,
        database_path=tmp_path / "bot.sqlite3",
        support_url="https://t.me/manager_altyn_bot",
    )

    with pytest.raises(ExpectedStop):
        await application.run(settings)

    assert captured == {
        "session": session,
        "token": "a" * 64,
    }
