from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from arbitrage_bot.domain import (
    AltynBuyQuote,
    BuyFeeMode,
    Exchange,
    ExchangeQuote,
    RateSnapshot,
)


def _altyn_quote() -> AltynBuyQuote:
    return AltynBuyQuote(
        amount_rub=Decimal("1000000"),
        rate=Decimal("79.88"),
        network_fee_usdt=Decimal("3"),
        indicative=True,
        as_of=datetime.now(UTC),
    )


def _rapira_quote() -> ExchangeQuote:
    return ExchangeQuote(
        exchange=Exchange.RAPIRA,
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
            exchange=Exchange.RAPIRA,
            bid=Decimal("79"),
            ask=Decimal("80"),
            buy_fee_rate=Decimal("0"),
            sell_fee_rate=Decimal("0"),
            buy_fee_mode=cast(BuyFeeMode, "unknown"),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amount_rub", Decimal("0")),
        ("amount_rub", Decimal("NaN")),
        ("rate", Decimal("0")),
        ("rate", Decimal("Infinity")),
        ("network_fee_usdt", Decimal("-0.01")),
        ("network_fee_usdt", Decimal("NaN")),
    ],
)
def test_altyn_buy_quote_rejects_invalid_decimals(field: str, value: Decimal) -> None:
    values = {
        "amount_rub": Decimal("1000000"),
        "rate": Decimal("79.88"),
        "network_fee_usdt": Decimal("3"),
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        AltynBuyQuote(
            **values,
            indicative=True,
            as_of=datetime.now(UTC),
        )


def test_altyn_buy_quote_accepts_zero_network_fee() -> None:
    quote = AltynBuyQuote(
        amount_rub=Decimal("1000000"),
        rate=Decimal("79.88"),
        network_fee_usdt=Decimal("0"),
        indicative=True,
        as_of=datetime.now(UTC),
    )

    assert quote.network_fee_usdt == 0


def test_altyn_buy_quote_requires_a_boolean_indicative_flag() -> None:
    with pytest.raises(TypeError, match="indicative"):
        AltynBuyQuote(
            amount_rub=Decimal("1000000"),
            rate=Decimal("79.88"),
            network_fee_usdt=Decimal("3"),
            indicative=cast(bool, 1),
            as_of=datetime.now(UTC),
        )


def test_altyn_buy_quote_requires_timezone_aware_as_of() -> None:
    with pytest.raises(ValueError, match="as_of must be timezone-aware"):
        AltynBuyQuote(
            amount_rub=Decimal("1000000"),
            rate=Decimal("79.88"),
            network_fee_usdt=Decimal("3"),
            indicative=True,
            as_of=datetime(2026, 7, 15, 9, 0),
        )


def test_rate_snapshot_rejects_non_datetime_timestamp() -> None:
    with pytest.raises(TypeError, match="fetched_at"):
        RateSnapshot(
            altyn=_altyn_quote(),
            rapira=_rapira_quote(),
            fetched_at=cast(datetime, "2026-07-15T09:00:00+03:00"),
        )


def test_rate_snapshot_rejects_a_non_rapira_market_quote() -> None:
    wrong_quote = ExchangeQuote(
        exchange=Exchange.ALTYN,
        bid=Decimal("79"),
        ask=Decimal("80"),
        buy_fee_rate=Decimal("0"),
        sell_fee_rate=Decimal("0"),
        buy_fee_mode=BuyFeeMode.ADDED_TO_QUOTE,
    )

    with pytest.raises(ValueError, match="wrong exchange"):
        RateSnapshot(
            altyn=_altyn_quote(),
            rapira=wrong_quote,
            fetched_at=datetime.now(UTC),
        )


def test_valid_rate_snapshot_is_accepted() -> None:
    snapshot = RateSnapshot(
        altyn=_altyn_quote(),
        rapira=_rapira_quote(),
        fetched_at=datetime.now(UTC),
    )

    assert snapshot.altyn.rate == Decimal("79.88")
    assert snapshot.rapira.exchange is Exchange.RAPIRA
