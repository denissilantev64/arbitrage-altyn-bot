from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage

from arbitrage_bot.domain import BuyFeeMode, Exchange, ExchangeQuote, RateSnapshot
from arbitrage_bot.errors import MarketDataError, RatesUnavailableError
from arbitrage_bot.repository import MorningBroadcastBatch
from arbitrage_bot.scheduler import (
    MSK,
    DeliveryOutcome,
    _send_to_subscriber,
    collect_and_store_rates,
    next_weekday_morning,
    send_morning_broadcast,
)


def _snapshot() -> RateSnapshot:
    return RateSnapshot(
        altyn=ExchangeQuote(
            exchange=Exchange.ALTYN,
            bid=Decimal("77.25"),
            ask=Decimal("80.46"),
            buy_fee_rate=Decimal("0.015"),
            sell_fee_rate=Decimal("0"),
            buy_fee_mode=BuyFeeMode.ADDED_TO_QUOTE,
        ),
        rapira=ExchangeQuote(
            exchange=Exchange.RAPIRA,
            bid=Decimal("80.02"),
            ask=Decimal("80.03"),
            buy_fee_rate=Decimal("0.001"),
            sell_fee_rate=Decimal("0.001"),
            buy_fee_mode=BuyFeeMode.DEDUCTED_FROM_BASE,
        ),
        fetched_at=datetime(2026, 7, 13, 5, 59, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (
            datetime(2026, 7, 10, 8, 59, tzinfo=MSK),
            datetime(2026, 7, 10, 9, 0, tzinfo=MSK),
        ),
        (
            datetime(2026, 7, 10, 9, 0, tzinfo=MSK),
            datetime(2026, 7, 10, 9, 0, tzinfo=MSK),
        ),
        (
            datetime(2026, 7, 10, 18, 0, tzinfo=MSK),
            datetime(2026, 7, 13, 9, 0, tzinfo=MSK),
        ),
        (
            datetime(2026, 7, 11, 8, 0, tzinfo=MSK),
            datetime(2026, 7, 13, 9, 0, tzinfo=MSK),
        ),
        (
            datetime(2026, 7, 12, 22, 0, tzinfo=MSK),
            datetime(2026, 7, 13, 9, 0, tzinfo=MSK),
        ),
        (
            datetime(2026, 7, 13, 5, 59, tzinfo=UTC),
            datetime(2026, 7, 13, 9, 0, tzinfo=MSK),
        ),
        (
            datetime(2026, 7, 13, 6, 0, tzinfo=UTC),
            datetime(2026, 7, 13, 9, 0, tzinfo=MSK),
        ),
    ],
)
def test_next_weekday_morning_uses_strictly_future_0900_moscow(
    now: datetime,
    expected: datetime,
) -> None:
    result = next_weekday_morning(now)

    assert result == expected
    assert result.tzinfo == MSK
    assert (result.hour, result.minute, result.second, result.microsecond) == (9, 0, 0, 0)


def test_next_weekday_morning_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        next_weekday_morning(datetime(2026, 7, 13, 8, 0))


async def test_collect_and_store_rates_saves_successful_snapshot() -> None:
    snapshot = _snapshot()
    collector = AsyncMock()
    collector.collect.return_value = snapshot
    repository = AsyncMock()

    result = await collect_and_store_rates(collector, repository)

    assert result is True
    collector.collect.assert_awaited_once_with()
    repository.save_snapshot.assert_awaited_once_with(snapshot)


async def test_collect_and_store_rates_handles_market_data_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    collector = AsyncMock()
    collector.collect.side_effect = MarketDataError(
        service="Rapira",
        code="invalid_depth",
        detail="order book is incomplete",
    )
    repository = AsyncMock()

    with caplog.at_level(logging.ERROR, logger="arbitrage_bot.scheduler"):
        result = await collect_and_store_rates(collector, repository)

    assert result is False
    repository.save_snapshot.assert_not_awaited()
    repository.record_refresh_failure.assert_awaited_once()
    assert "service=Rapira code=invalid_depth" in caplog.text


class _BroadcastRepository:
    def __init__(self, snapshot: RateSnapshot, subscribers: list[int]) -> None:
        self.snapshot = snapshot
        self.subscribers = subscribers
        self._lock = asyncio.Lock()
        self._messages: dict[date, str] = {}
        self._pending: dict[date, set[int]] = {}
        self.finished: list[tuple[date, int, str]] = []
        self.latest_snapshot_calls = 0
        self.snapshot_error: Exception | None = None

    async def latest_snapshot(self, _max_age_seconds: int) -> RateSnapshot:
        self.latest_snapshot_calls += 1
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return self.snapshot

    async def get_morning_broadcast(
        self,
        run_date: date,
    ) -> MorningBroadcastBatch | None:
        async with self._lock:
            if run_date not in self._messages:
                return None
            pending = tuple(sorted(self._pending[run_date]))
            return MorningBroadcastBatch(self._messages[run_date], pending, not pending)

    async def prepare_morning_broadcast(
        self,
        run_date: date,
        message: str,
    ) -> MorningBroadcastBatch:
        async with self._lock:
            self._messages.setdefault(run_date, message)
            self._pending.setdefault(run_date, set(self.subscribers))
            pending = tuple(sorted(self._pending[run_date]))
            return MorningBroadcastBatch(self._messages[run_date], pending, not pending)

    async def finish_morning_delivery(
        self,
        run_date: date,
        chat_id: int,
        state: str,
    ) -> bool:
        async with self._lock:
            if chat_id not in self._pending[run_date]:
                return False
            self._pending[run_date].remove(chat_id)
            self.finished.append((run_date, chat_id, state))
            return True

    async def is_morning_broadcast_complete(self, run_date: date) -> bool:
        return run_date in self._pending and not self._pending[run_date]

    async def is_subscribed(self, chat_id: int) -> bool:
        return chat_id in self.subscribers

    async def set_subscription(self, chat_id: int, subscribed: bool) -> bool:
        if not subscribed and chat_id in self.subscribers:
            self.subscribers.remove(chat_id)
            return True
        return False


class _RecordingBot:
    def __init__(self, fail_once: set[int] | None = None) -> None:
        self.sent_messages: list[tuple[int, str, Any]] = []
        self.fail_once = set(fail_once or set())

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        if chat_id in self.fail_once:
            self.fail_once.remove(chat_id)
            raise TelegramNetworkError(
                SendMessage(chat_id=chat_id, text=text),
                "temporary network failure",
            )
        self.sent_messages.append((chat_id, text, kwargs.get("reply_markup")))


async def test_morning_broadcast_retries_only_pending_recipients() -> None:
    repository = _BroadcastRepository(_snapshot(), subscribers=[1001, 1002])
    bot = _RecordingBot(fail_once={1001})
    run_date = date(2026, 7, 13)

    first_result = await send_morning_broadcast(bot, repository, run_date)  # type: ignore[arg-type]
    repository.snapshot_error = RatesUnavailableError("new rates are unavailable")
    second_result = await send_morning_broadcast(bot, repository, run_date)  # type: ignore[arg-type]

    assert first_result is False
    assert second_result is True
    assert repository.latest_snapshot_calls == 1
    assert [chat_id for chat_id, _text, _markup in bot.sent_messages] == [1002, 1001]
    assert repository.finished == [
        (run_date, 1002, "sent"),
        (run_date, 1001, "sent"),
    ]
    assert all(text for _chat_id, text, _markup in bot.sent_messages)
    assert all(markup is not None for _chat_id, _text, markup in bot.sent_messages)


async def test_permanently_unavailable_chat_is_unsubscribed() -> None:
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramBadRequest(
        SendMessage(chat_id=1001, text="morning spread"),
        "Bad Request: chat not found",
    )
    repository = AsyncMock()

    outcome = await _send_to_subscriber(bot, repository, 1001, "morning spread")

    assert outcome is DeliveryOutcome.SKIPPED
    repository.set_subscription.assert_awaited_once_with(1001, False)


async def test_unknown_bad_request_stays_pending_without_crashing() -> None:
    bot = AsyncMock()
    bot.send_message.side_effect = TelegramBadRequest(
        SendMessage(chat_id=1001, text="morning spread"),
        "Bad Request: unexpected validation error",
    )
    repository = AsyncMock()

    outcome = await _send_to_subscriber(bot, repository, 1001, "morning spread")

    assert outcome is DeliveryOutcome.RETRY
    repository.set_subscription.assert_not_awaited()
