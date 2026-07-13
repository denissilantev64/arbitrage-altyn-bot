from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ErrorEvent, Message

from .amounts import parse_amount_rub
from .calculations import calculate_amount, calculate_best_spread
from .config import Settings
from .constants import RATE_MAX_AGE_SECONDS
from .errors import InvalidAmountError, RatesUnavailableError
from .formatting import format_spread_message
from .keyboards import (
    CALCULATE_BUTTON,
    FEEDBACK_BUTTON,
    HELP_BUTTON,
    SHOW_SPREAD_BUTTON,
    SUBSCRIBE_BUTTON,
    UNSUBSCRIBE_BUTTON,
    main_keyboard,
    support_keyboard,
)
from .repository import SQLiteRepository
from .texts import (
    AMOUNT_PROMPT,
    FEEDBACK_TEXT,
    GENERIC_ERROR_TEXT,
    HELP_TEXT,
    INVALID_AMOUNT_TEXT,
    RATES_UNAVAILABLE_TEXT,
    START_TEXT,
    SUBSCRIBED_TEXT,
    UNSUBSCRIBED_TEXT,
)

logger = logging.getLogger(__name__)


class ProfitInput(StatesGroup):
    waiting_for_amount = State()


def create_router(repository: SQLiteRepository, settings: Settings) -> Router:
    router = Router(name="telegram-handlers")
    router.message.filter(F.chat.type == ChatType.PRIVATE)

    async def ensure_user(message: Message) -> bool:
        return await repository.ensure_user(message.chat.id)

    async def send_spread(message: Message, raw_amount: str | None = None) -> None:
        subscribed = await ensure_user(message)
        try:
            snapshot = await repository.latest_snapshot(RATE_MAX_AGE_SECONDS)
            spread = calculate_best_spread(snapshot)
            amount = None
            if raw_amount is not None:
                amount_rub = parse_amount_rub(raw_amount)
                amount = calculate_amount(spread, amount_rub)
        except InvalidAmountError:
            await message.answer(INVALID_AMOUNT_TEXT, reply_markup=main_keyboard(subscribed))
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
        await message.answer(FEEDBACK_TEXT, reply_markup=support_keyboard(settings.support_url))

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

    @router.message(F.text == HELP_BUTTON)
    async def help_button_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await ensure_user(message)
        await message.answer(FEEDBACK_TEXT, reply_markup=support_keyboard(settings.support_url))

    @router.message(F.text == FEEDBACK_BUTTON)
    async def feedback_button_handler(message: Message, state: FSMContext) -> None:
        await state.clear()
        await ensure_user(message)
        await message.answer(FEEDBACK_TEXT, reply_markup=support_keyboard(settings.support_url))

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
