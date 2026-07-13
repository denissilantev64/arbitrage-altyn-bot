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
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    Update,
    User,
)

from arbitrage_bot.application import build_dispatcher
from arbitrage_bot.calculations import calculate_amount, calculate_best_spread
from arbitrage_bot.config import Settings
from arbitrage_bot.domain import BuyFeeMode, Exchange, ExchangeQuote, RateSnapshot
from arbitrage_bot.formatting import format_spread_message
from arbitrage_bot.keyboards import (
    CALCULATE_BUTTON,
    SHOW_SPREAD_BUTTON,
    SUBSCRIBE_BUTTON,
    SUPPORT_BUTTON,
    UNSUBSCRIBE_BUTTON,
)
from arbitrage_bot.repository import SQLiteRepository
from arbitrage_bot.texts import (
    AMOUNT_PROMPT,
    HELP_TEXT,
    START_TEXT,
    SUBSCRIBED_TEXT,
    SUPPORT_TEXT,
    UNSUBSCRIBED_TEXT,
)

_CHAT_ID = 1001
_SUPPORT_URL = "https://t.me/vardumyans"


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
    return RateSnapshot(
        altyn=ExchangeQuote(
            exchange=Exchange.ALTYN,
            bid=Decimal("77.20"),
            ask=Decimal("77.39"),
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
        fetched_at=datetime.now(UTC),
    )


def _reply_button_texts(message: SendMessage) -> set[str]:
    markup = message.reply_markup
    assert isinstance(markup, ReplyKeyboardMarkup)
    return {button.text for row in markup.keyboard for button in row}


def _assert_support_link(message: SendMessage) -> None:
    assert message.text == SUPPORT_TEXT
    markup = message.reply_markup
    assert isinstance(markup, InlineKeyboardMarkup)
    assert len(markup.inline_keyboard) == 1
    assert len(markup.inline_keyboard[0]) == 1
    assert markup.inline_keyboard[0][0].url == _SUPPORT_URL


def test_command_texts_match_the_public_menu_copy() -> None:
    assert (
        "В расчете по сумме учитываются настроенные комиссии Altyn и актуальная комиссия Rapira."
    ) in START_TEXT
    assert START_TEXT.endswith("Можно также нажать «Показать спред» или «Поддержка».")
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
    settings = Settings(
        telegram_bot_token=bot.token,
        database_path=tmp_path / "handlers.sqlite3",
        support_url=_SUPPORT_URL,
        altyn_buy_fee_rate=Decimal("0"),
        altyn_sell_fee_rate=Decimal("0"),
    )
    dispatcher = build_dispatcher(repository, settings)
    harness = HandlerHarness(bot, dispatcher, repository, session)

    try:
        replies = await harness.feed("/start")
        assert len(replies) == 1
        assert replies[0].text == START_TEXT
        assert UNSUBSCRIBE_BUTTON in _reply_button_texts(replies[0])
        assert await repository.is_subscribed(_CHAT_ID) is True

        spread = calculate_best_spread(snapshot)
        expected_spread = format_spread_message(spread)
        replies = await harness.feed("/spread")
        assert [reply.text for reply in replies] == [expected_spread]

        replies = await harness.feed(SHOW_SPREAD_BUTTON)
        assert [reply.text for reply in replies] == [expected_spread]

        expected_amount = format_spread_message(
            spread,
            calculate_amount(spread, Decimal("1000000")),
        )
        replies = await harness.feed("/spread 1000000")
        assert [reply.text for reply in replies] == [expected_amount]

        replies = await harness.feed(CALCULATE_BUTTON)
        assert [reply.text for reply in replies] == [AMOUNT_PROMPT]
        replies = await harness.feed("1000000")
        assert [reply.text for reply in replies] == [expected_amount]

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

        replies = await harness.feed(SUPPORT_BUTTON)
        assert len(replies) == 1
        _assert_support_link(replies[0])
    finally:
        await dispatcher.storage.close()
        await bot.session.close()
        await repository.close()
