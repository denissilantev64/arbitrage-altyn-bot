from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta, timezone
from enum import StrEnum

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
)

from .calculations import calculate_best_spread
from .constants import (
    MORNING_HOUR_MSK,
    MORNING_MINUTE_MSK,
    MOSCOW_UTC_OFFSET_HOURS,
    RATE_MAX_AGE_SECONDS,
    RATE_REFRESH_SECONDS,
)
from .errors import MarketDataError, RatesUnavailableError
from .formatting import format_spread_message
from .keyboards import main_keyboard
from .rates import RateCollector
from .repository import SQLiteRepository

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=MOSCOW_UTC_OFFSET_HOURS), name="MSK")


class DeliveryOutcome(StrEnum):
    SENT = "sent"
    SKIPPED = "skipped"
    RETRY = "retry"


async def collect_and_store_rates(
    collector: RateCollector,
    repository: SQLiteRepository,
) -> bool:
    try:
        snapshot = await collector.collect()
    except asyncio.CancelledError:
        raise
    except MarketDataError as exc:
        await repository.record_refresh_failure(
            service=exc.service,
            error_code=exc.code,
            attempted_at=datetime.now(UTC),
        )
        logger.error(
            "Market data refresh failed: service=%s code=%s",
            exc.service,
            exc.code,
            exc_info=True,
        )
        return False

    await repository.save_snapshot(snapshot)

    logger.info(
        "Rates stored: altyn_bid=%s altyn_ask=%s rapira_bid=%s rapira_ask=%s",
        snapshot.altyn.bid,
        snapshot.altyn.ask,
        snapshot.rapira.bid,
        snapshot.rapira.ask,
    )
    return True


async def rate_refresh_loop(
    collector: RateCollector,
    repository: SQLiteRepository,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        if await _wait_or_stop(stop_event, RATE_REFRESH_SECONDS):
            return
        await collect_and_store_rates(collector, repository)


def next_weekday_morning(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(MSK)
    for days_ahead in range(8):
        candidate_date = local_now.date() + timedelta(days=days_ahead)
        if candidate_date.weekday() >= 5:
            continue
        candidate = datetime.combine(
            candidate_date,
            time(MORNING_HOUR_MSK, MORNING_MINUTE_MSK),
            tzinfo=MSK,
        )
        if candidate >= local_now:
            return candidate
    raise RuntimeError("could not calculate the next weekday morning")


async def morning_broadcast_loop(
    bot: Bot,
    repository: SQLiteRepository,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        now = datetime.now(UTC)
        local_now = now.astimezone(MSK)
        morning_started = (local_now.hour, local_now.minute) >= (
            MORNING_HOUR_MSK,
            MORNING_MINUTE_MSK,
        )
        if local_now.weekday() < 5 and morning_started:
            run_date = local_now.date()
            if not await repository.is_morning_broadcast_complete(run_date):
                completed = await send_morning_broadcast(bot, repository, run_date)
                if not completed:
                    if await _wait_or_stop(stop_event, RATE_REFRESH_SECONDS):
                        return
                    continue

        run_at = next_weekday_morning(datetime.now(UTC))
        delay = max(0.0, (run_at - datetime.now(UTC)).total_seconds())
        if await _wait_or_stop(stop_event, delay):
            return


async def send_morning_broadcast(
    bot: Bot,
    repository: SQLiteRepository,
    run_date: date,
) -> bool:
    batch = await repository.get_morning_broadcast(run_date)
    if batch is None:
        try:
            snapshot = await repository.latest_snapshot(RATE_MAX_AGE_SECONDS)
        except RatesUnavailableError:
            logger.warning("Morning broadcast is waiting for current rates", exc_info=True)
            return False

        text = format_spread_message(calculate_best_spread(snapshot))
        batch = await repository.prepare_morning_broadcast(run_date, text)
    if batch.complete:
        return True

    sent = 0
    skipped = 0
    failed = 0
    for chat_id in batch.pending_chat_ids:
        if not await repository.is_subscribed(chat_id):
            await repository.finish_morning_delivery(run_date, chat_id, "skipped")
            skipped += 1
            continue

        outcome = await _send_to_subscriber(bot, repository, chat_id, batch.message)
        if outcome is DeliveryOutcome.SENT:
            await repository.finish_morning_delivery(run_date, chat_id, "sent")
            sent += 1
        elif outcome is DeliveryOutcome.SKIPPED:
            await repository.finish_morning_delivery(run_date, chat_id, "skipped")
            skipped += 1
        else:
            failed += 1
        await asyncio.sleep(0.05)

    complete = await repository.is_morning_broadcast_complete(run_date)
    logger.info(
        "Morning broadcast attempt: date=%s sent=%d skipped=%d pending=%d complete=%s",
        run_date.isoformat(),
        sent,
        skipped,
        failed,
        complete,
    )
    return complete


async def _send_to_subscriber(
    bot: Bot,
    repository: SQLiteRepository,
    chat_id: int,
    text: str,
) -> DeliveryOutcome:
    for attempt in range(2):
        try:
            await bot.send_message(chat_id, text, reply_markup=main_keyboard(True))
            return DeliveryOutcome.SENT
        except TelegramRetryAfter as exc:
            if attempt == 0:
                logger.warning("Telegram rate limit for chat_id=%s; retrying once", chat_id)
                await asyncio.sleep(float(exc.retry_after))
                continue
            logger.exception("Telegram rate limit persisted for chat_id=%s", chat_id)
            return DeliveryOutcome.RETRY
        except TelegramForbiddenError:
            logger.info("Telegram user blocked the bot; unsubscribing chat_id=%s", chat_id)
            await repository.set_subscription(chat_id, False)
            return DeliveryOutcome.SKIPPED
        except TelegramBadRequest as exc:
            message = str(exc).lower()
            if "chat not found" in message or "user is deactivated" in message:
                logger.info(
                    "Telegram chat is permanently unavailable; unsubscribing chat_id=%s",
                    chat_id,
                )
                await repository.set_subscription(chat_id, False)
                return DeliveryOutcome.SKIPPED
            logger.exception(
                "Telegram rejected the morning message for chat_id=%s; keeping it pending",
                chat_id,
            )
            return DeliveryOutcome.RETRY
        except TelegramNetworkError:
            logger.exception("Telegram network failure for chat_id=%s", chat_id)
            return DeliveryOutcome.RETRY
        except TelegramAPIError:
            logger.exception("Telegram API failure for chat_id=%s", chat_id)
            return DeliveryOutcome.RETRY

    raise RuntimeError("Telegram delivery retry loop exhausted unexpectedly")


async def _wait_or_stop(stop_event: asyncio.Event, delay: float) -> bool:
    if delay <= 0:
        return stop_event.is_set()
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except TimeoutError:
        return False
    return True
