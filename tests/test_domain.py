from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from arbitrage_bot.domain import BuyFeeMode, Exchange, ExchangeQuote, RateSnapshot


def _quote(exchange: Exchange) -> ExchangeQuote:
    return ExchangeQuote(
        exchange=exchange,
        bid=Decimal("79"),
        ask=Decimal("80"),
        buy_fee_rate=Decimal("0.001"),
        sell_fee_rate=Decimal("0.001"),
        buy_fee_mode=BuyFeeMode.DEDUCTED_FROM_BASE,
    )


def test_exchange_quote_rejects_unknown_exchange_at_runtime() -> None:
    with pytest.raises(TypeError, match="exchange"):
        ExchangeQuote(
            exchange=cast(Exchange, "Unknown"),
            bid=Decimal("79"),
            ask=Decimal("80"),
            buy_fee_rate=Decimal("0"),
            sell_fee_rate=Decimal("0"),
            buy_fee_mode=BuyFeeMode.ADDED_TO_QUOTE,
        )


def test_exchange_quote_rejects_unknown_fee_mode_at_runtime() -> None:
    with pytest.raises(TypeError, match="buy_fee_mode"):
        ExchangeQuote(
            exchange=Exchange.ALTYN,
            bid=Decimal("79"),
            ask=Decimal("80"),
            buy_fee_rate=Decimal("0"),
            sell_fee_rate=Decimal("0"),
            buy_fee_mode=cast(BuyFeeMode, "unknown"),
        )


def test_rate_snapshot_rejects_non_datetime_timestamp() -> None:
    with pytest.raises(TypeError, match="fetched_at"):
        RateSnapshot(
            altyn=_quote(Exchange.ALTYN),
            rapira=_quote(Exchange.RAPIRA),
            fetched_at=cast(datetime, "2026-07-13T09:00:00+03:00"),
        )


def test_valid_rate_snapshot_is_accepted() -> None:
    snapshot = RateSnapshot(
        altyn=_quote(Exchange.ALTYN),
        rapira=_quote(Exchange.RAPIRA),
        fetched_at=datetime.now(UTC),
    )

    assert snapshot.altyn.exchange is Exchange.ALTYN
