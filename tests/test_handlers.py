# ruff: noqa: ASYNC109, RUF001 -- framework signature and intentional Russian UI text.

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.enums import ChatType, ParseMode
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import (
    Chat,
    Message,
    ReplyKeyboardMarkup,
    Update,
    User,
)

from arbitrage_bot.application import build_dispatcher
from arbitrage_bot.calculations import calculate_amount, calculate_spread
from arbitrage_bot.domain import AltynBuyQuote, BuyFeeMode, Exchange, ExchangeQuote, RateSnapshot
from arbitrage_bot.formatting import format_spread_message
from arbitrage_bot.keyboards import (
    CALCULATE_BUTTON,
    SHOW_SPREAD_BUTTON,
    SUBSCRIBE_BUTTON,
    UNSUBSCRIBE_BUTTON,
)
from arbitrage_bot.repository import SQLiteRepository
from arbitrage_bot.texts import (
    AMOUNT_PROMPT,
    AMOUNT_TOO_SMALL_TEXT,
    HELP_TEXT,
    INVALID_AMOUNT_TEXT,
    RATES_UNAVAILABLE_TEXT,
    START_TEXT,
    SUBSCRIBED_TEXT,
    UNSUBSCRIBED_TEXT,
)

_CHAT_ID = 1001


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[SendMessage] = []
        self._message_id = 10_000

    async def close(self) -> None:
        return None

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,
    ) -> Any:
        del timeout
        if not isinstance(method, SendMessage):
            raise AssertionError(f"unexpected Telegram method: {type(method).__name__}")
        self.sent.append(method)
        self._message_id += 1
        return Message(
            message_id=self._message_id,
            date=datetime.now(UTC),
            chat=Chat(id=int(method.chat_id), type=ChatType.PRIVATE),
            from_user=User(id=bot.id, is_bot=True, first_name="Spread bot"),
            text=method.text,
        )

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65_536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        del url, headers, timeout, chunk_size, raise_for_status
        raise AssertionError("file downloads are not expected")
        yield b""  # pragma: no cover


@dataclass(slots=True)
class HandlerHarness:
    bot: Bot
    dispatcher: Dispatcher
    repository: SQLiteRepository
    session: RecordingSession
    next_update_id: int = 1

    async def feed(self, text: str) -> list[SendMessage]:
        before = len(self.session.sent)
        update_id = self.next_update_id
        self.next_update_id += 1
        incoming = Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=Chat(id=_CHAT_ID, type=ChatType.PRIVATE),
            from_user=User(id=_CHAT_ID, is_bot=False, first_name="Denis"),
            text=text,
        )
        await self.dispatcher.feed_update(
            self.bot,
            Update(update_id=update_id, message=incoming),
        )
        return self.session.sent[before:]


def _snapshot() -> RateSnapshot:
    now = datetime.now(UTC)
    return RateSnapshot(
        altyn=AltynBuyQuote(
            amount_rub=Decimal("1000000"),
            rate=Decimal("79.88"),
            network_fee_usdt=Decimal("3"),
            indicative=True,
            as_of=now,
        ),
        rapira=ExchangeQuote(
            exchange=Exchange.RAPIRA,
            bid=Decimal("80.02"),
            ask=Decimal("80.03"),
            buy_fee_rate=Decimal("0.001"),
            sell_fee_rate=Decimal("0.001"),
            buy_fee_mode=BuyFeeMode.DEDUCTED_FROM_BASE,
        ),
        fetched_at=now,
    )


def _reply_button_texts(message: SendMessage) -> set[str]:
    markup = message.reply_markup
    assert isinstance(markup, ReplyKeyboardMarkup)
    return {button.text for row in markup.keyboard for button in row}


def test_command_texts_match_the_public_menu_copy() -> None:
    assert ("В расчете по сумме персональная комиссия Altyn уже включена в курс.") in START_TEXT
    assert "Поддержка" not in START_TEXT
    assert "Exchange" not in START_TEXT
    assert "🇧🇾" not in START_TEXT
    assert HELP_TEXT.endswith("/help - команды бота")


async def test_all_requested_private_chat_flows(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "handlers.sqlite3")
    await repository.connect()
    await repository.initialize()
    snapshot = _snapshot()
    await repository.save_snapshot(snapshot)

    session = RecordingSession()
    bot = Bot(
        "123456789:" + "A" * 35,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = build_dispatcher(repository)
    harness = HandlerHarness(bot, dispatcher, repository, session)

    try:
        replies = await harness.feed("/start")
        assert len(replies) == 1
        assert replies[0].text == START_TEXT
        assert UNSUBSCRIBE_BUTTON in _reply_button_texts(replies[0])
        assert "Поддержка" not in _reply_button_texts(replies[0])
        assert await repository.is_subscribed(_CHAT_ID) is True

        spread = calculate_spread(snapshot)
        expected_spread = format_spread_message(spread)
        replies = await harness.feed("/spread")
        assert [reply.text for reply in replies] == [expected_spread]

        replies = await harness.feed(SHOW_SPREAD_BUTTON)
        assert [reply.text for reply in replies] == [expected_spread]

        expected_amount = format_spread_message(
            spread,
            calculate_amount(snapshot, Decimal("1000000")),
        )
        replies = await harness.feed("/spread 1000000")
        assert [reply.text for reply in replies] == [expected_amount]

        replies = await harness.feed(CALCULATE_BUTTON)
        assert [reply.text for reply in replies] == [AMOUNT_PROMPT]
        replies = await harness.feed("1000000")
        assert [reply.text for reply in replies] == [expected_amount]

        second_amount = format_spread_message(
            spread,
            calculate_amount(snapshot, Decimal("2000000")),
        )
        replies = await harness.feed("/spread 2000000")
        assert [reply.text for reply in replies] == [second_amount]

        replies = await harness.feed("/unsubscribe")
        assert [reply.text for reply in replies] == [UNSUBSCRIBED_TEXT]
        assert SUBSCRIBE_BUTTON in _reply_button_texts(replies[0])
        assert await repository.is_subscribed(_CHAT_ID) is False

        replies = await harness.feed(SUBSCRIBE_BUTTON)
        assert [reply.text for reply in replies] == [SUBSCRIBED_TEXT]
        assert UNSUBSCRIBE_BUTTON in _reply_button_texts(replies[0])
        assert await repository.is_subscribed(_CHAT_ID) is True

        replies = await harness.feed(UNSUBSCRIBE_BUTTON)
        assert [reply.text for reply in replies] == [UNSUBSCRIBED_TEXT]
        assert await repository.is_subscribed(_CHAT_ID) is False

        replies = await harness.feed("/subscribe")
        assert [reply.text for reply in replies] == [SUBSCRIBED_TEXT]
        assert await repository.is_subscribed(_CHAT_ID) is True

        replies = await harness.feed("/help")
        assert [reply.text for reply in replies] == [HELP_TEXT]

        replies = await harness.feed("Поддержка")
        assert [reply.text for reply in replies] == [HELP_TEXT]
        assert "Поддержка" not in _reply_button_texts(replies[0])
    finally:
        await dispatcher.storage.close()
        await bot.session.close()
        await repository.close()


async def test_amount_request_validates_input_and_requires_fresh_snapshot(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "handler-errors.sqlite3")
    await repository.connect()
    await repository.initialize()
    await repository.save_snapshot(_snapshot())

    session = RecordingSession()
    bot = Bot("123456789:" + "A" * 35, session=session)
    dispatcher = build_dispatcher(repository)
    harness = HandlerHarness(bot, dispatcher, repository, session)

    try:
        invalid_replies = await harness.feed("/spread not-a-number")
        assert [reply.text for reply in invalid_replies] == [INVALID_AMOUNT_TEXT]

        too_small_replies = await harness.feed("/spread 100")
        assert [reply.text for reply in too_small_replies] == [AMOUNT_TOO_SMALL_TEXT]

        await repository.record_refresh_failure("altyn", "http_status")
        unavailable_replies = await harness.feed("/spread 1000000")
        assert [reply.text for reply in unavailable_replies] == [RATES_UNAVAILABLE_TEXT]
    finally:
        await dispatcher.storage.close()
        await bot.session.close()
        await repository.close()
