from __future__ import annotations

import logging
from decimal import Decimal
from typing import Protocol

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ErrorEvent, Message

from .amounts import parse_amount_rub
from .calculations import calculate_amount, calculate_spread
from .config import Settings
from .constants import RATE_MAX_AGE_SECONDS
from .domain import AltynBuyQuote, RateSnapshot
from .errors import (
    InsufficientAmountError,
    InvalidAmountError,
    MarketDataError,
    RatesUnavailableError,
)
from .formatting import format_spread_message
from .keyboards import (
    CALCULATE_BUTTON,
    SHOW_SPREAD_BUTTON,
    SUBSCRIBE_BUTTON,
    SUPPORT_BUTTON,
    UNSUBSCRIBE_BUTTON,
    main_keyboard,
    support_keyboard,
)
from .repository import SQLiteRepository
from .texts import (
    AMOUNT_PROMPT,
    AMOUNT_TOO_SMALL_TEXT,
    GENERIC_ERROR_TEXT,
    HELP_TEXT,
    INVALID_AMOUNT_TEXT,
    RATES_UNAVAILABLE_TEXT,
    START_TEXT,
    SUBSCRIBED_TEXT,
    SUPPORT_TEXT,
    TOO_MANY_REQUESTS_TEXT,
    UNSUBSCRIBED_TEXT,
)

logger = logging.getLogger(__name__)


class AltynQuoteProvider(Protocol):
    async def fetch_altyn_quote(self, amount_rub: Decimal) -> AltynBuyQuote: ...


class ProfitInput(StatesGroup):
    waiting_for_amount = State()


def create_router(
    repository: SQLiteRepository,
    settings: Settings,
    quote_provider: AltynQuoteProvider,
) -> Router:
    router = Router(name="telegram-handlers")
    router.message.filter(F.chat.type == ChatType.PRIVATE)

    async def ensure_user(message: Message) -> bool:
        return await repository.ensure_user(message.chat.id)

    async def send_spread(message: Message, raw_amount: str | None = None) -> None:
        subscribed = await ensure_user(message)
        try:
            amount_rub = parse_amount_rub(raw_amount) if raw_amount is not None else None
            stored_snapshot = await repository.latest_snapshot(RATE_MAX_AGE_SECONDS)
            snapshot = stored_snapshot
            amount = None
            if amount_rub is not None:
                exact_altyn_quote = await quote_provider.fetch_altyn_quote(amount_rub)
                stored_snapshot = await repository.latest_snapshot(RATE_MAX_AGE_SECONDS)
                snapshot = RateSnapshot(
                    altyn=exact_altyn_quote,
                    rapira=stored_snapshot.rapira,
                    fetched_at=min(exact_altyn_quote.as_of, stored_snapshot.fetched_at),
                )
                amount = calculate_amount(snapshot)
            spread = calculate_spread(snapshot)
        except InvalidAmountError:
            await message.answer(INVALID_AMOUNT_TEXT, reply_markup=main_keyboard(subscribed))
            return
        except InsufficientAmountError:
            await message.answer(AMOUNT_TOO_SMALL_TEXT, reply_markup=main_keyboard(subscribed))
            return
        except MarketDataError as exc:
            if exc.code == "client_rate_limit":
                logger.info("On-demand Altyn quote was locally rate-limited")
                text = TOO_MANY_REQUESTS_TEXT
            else:
                logger.warning(
                    "On-demand Altyn quote failed: service=%s code=%s",
                    exc.service,
                    exc.code,
                )
                text = RATES_UNAVAILABLE_TEXT
            await message.answer(text, reply_markup=main_keyboard(subscribed))
            return
        except RatesUnavailableError:
            await message.answer(RATES_UNAVAILABLE_TEXT, reply_markup=main_keyboard(subscribed))
            return

        await message.answer(
            format_spread_message(spread, amount),
            reply_markup=main_keyboard(subscribed),
        )

    async def set_subscription(message: Message, subscribed: bool) -> None:
        await repository.set_subscription(message.chat.id, subscribed)
        text = SUBSCRIBED_TEXT if subscribed else UNSUBSCRIBED_TEXT
        await message.answer(text, reply_markup=main_keyboard(subscribed))

    @router.message(Command("start"))
    async def start_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        subscribed = await ensure_user(message)
        await message.answer(START_TEXT, reply_markup=main_keyboard(subscribed))

    @router.message(Command("help"))
    async def help_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        subscribed = await ensure_user(message)
        await message.answer(HELP_TEXT, reply_markup=main_keyboard(subscribed))

    @router.message(Command("spread"))
    async def spread_handler(
        message: Message,
        command: CommandObject,
        state: FSMContext,
    ) -> None:
        await state.clear()
        await send_spread(message, command.args)

    @router.message(Command("subscribe"))
    async def subscribe_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await set_subscription(message, True)

    @router.message(Command("unsubscribe"))
    async def unsubscribe_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await set_subscription(message, False)

    @router.message(F.text == SHOW_SPREAD_BUTTON)
    async def spread_button_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await send_spread(message)

    @router.message(F.text == CALCULATE_BUTTON)
    async def calculate_button_handler(message: Message, state: FSMContext) -> None:
        subscribed = await ensure_user(message)
        await state.set_state(ProfitInput.waiting_for_amount)
        await message.answer(AMOUNT_PROMPT, reply_markup=main_keyboard(subscribed))

    @router.message(F.text == SUBSCRIBE_BUTTON)
    async def subscribe_button_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await set_subscription(message, True)

    @router.message(F.text == UNSUBSCRIBE_BUTTON)
    async def unsubscribe_button_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await set_subscription(message, False)

    @router.message(F.text == SUPPORT_BUTTON)
    async def support_button_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await ensure_user(message)
        await message.answer(SUPPORT_TEXT, reply_markup=support_keyboard(settings.support_url))

    @router.message(ProfitInput.waiting_for_amount)
    async def amount_input_handler(message: Message, state: FSMContext) -> None:
        if message.text is None:
            subscribed = await ensure_user(message)
            await message.answer(INVALID_AMOUNT_TEXT, reply_markup=main_keyboard(subscribed))
            return
        try:
            parse_amount_rub(message.text)
        except InvalidAmountError:
            subscribed = await ensure_user(message)
            await message.answer(INVALID_AMOUNT_TEXT, reply_markup=main_keyboard(subscribed))
            return
        await state.clear()
        await send_spread(message, message.text)

    @router.message()
    async def unknown_message_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        subscribed = await ensure_user(message)
        await message.answer(HELP_TEXT, reply_markup=main_keyboard(subscribed))

    @router.error()
    async def error_handler(event: ErrorEvent) -> bool:
        exception = event.exception
        logger.error(
            "Unhandled Telegram update error",
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        if event.update.message is not None:
            try:
                await event.update.message.answer(GENERIC_ERROR_TEXT)
            except Exception:
                logger.exception("Failed to send safe Telegram error response")
        return True

    return router
