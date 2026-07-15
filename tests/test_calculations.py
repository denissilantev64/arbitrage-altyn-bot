from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arbitrage_bot.calculations import calculate_amount, calculate_spread
from arbitrage_bot.domain import (
    AltynBuyQuote,
    BuyFeeMode,
    Exchange,
    ExchangeQuote,
    RateSnapshot,
)
from arbitrage_bot.errors import InsufficientAmountError, InvalidAmountError


def snapshot(
    *,
    amount: str = "1000000",
    altyn_rate: str = "79.88",
    network_fee: str = "3",
    rapira_bid: str = "80.02",
    rapira_ask: str = "80.10",
    rapira_fee: str = "0.001",
) -> RateSnapshot:
    now = datetime.now(UTC)
    return RateSnapshot(
        altyn=AltynBuyQuote(
            amount_rub=Decimal(amount),
            rate=Decimal(altyn_rate),
            network_fee_usdt=Decimal(network_fee),
            indicative=True,
            as_of=now,
        ),
        rapira=ExchangeQuote(
            exchange=Exchange.RAPIRA,
            bid=Decimal(rapira_bid),
            ask=Decimal(rapira_ask),
            buy_fee_rate=Decimal(rapira_fee),
            sell_fee_rate=Decimal(rapira_fee),
            buy_fee_mode=BuyFeeMode.DEDUCTED_FROM_BASE,
        ),
        fetched_at=now,
    )


def test_spread_uses_only_the_altyn_to_rapira_direction_at_full_precision() -> None:
    result = calculate_spread(snapshot(altyn_rate="77.39", rapira_bid="80.02", rapira_fee="0.001"))

    assert result.altyn_rate == Decimal("77.39")
    assert result.rapira_bid == Decimal("80.02")
    assert result.rapira_effective_bid == Decimal("80.02") * Decimal("0.999")
    assert result.rapira_sell_fee_rate == Decimal("0.001")
    assert result.gross_spread_rub == Decimal("2.63")
    assert result.gross_spread_percent == Decimal("2.63") / Decimal("77.39") * 100
    assert result.net_spread_rub == result.rapira_effective_bid - Decimal("77.39")
    assert result.net_spread_percent == (result.net_spread_rub / Decimal("77.39") * Decimal(100))


def test_standard_spread_does_not_use_the_fixed_network_fee() -> None:
    without_fee = calculate_spread(snapshot(network_fee="0"))
    with_fee = calculate_spread(snapshot(network_fee="100"))

    assert with_fee == without_fee


def test_negative_supported_direction_is_returned_without_reverse_selection() -> None:
    result = calculate_spread(snapshot(altyn_rate="83", rapira_bid="79", rapira_ask="80"))

    assert result.altyn_rate == Decimal("83")
    assert result.rapira_bid == Decimal("79")
    assert result.gross_spread_rub == Decimal("-4")
    assert result.net_spread_percent < 0


def test_calculate_amount_uses_exact_quote_and_deducts_network_fee_once() -> None:
    market = snapshot(
        amount="1000000",
        altyn_rate="79.88",
        network_fee="3",
        rapira_bid="80.02",
        rapira_fee="0.001",
    )

    result = calculate_amount(market)

    expected_bought = Decimal("1000000") / Decimal("79.88")
    expected_to_sell = expected_bought - Decimal("3")
    expected_final = expected_to_sell * Decimal("80.02") * Decimal("0.999")
    assert result.amount_rub == Decimal("1000000")
    assert result.usdt_bought == expected_bought
    assert result.network_fee_usdt == Decimal("3")
    assert result.usdt_to_sell == expected_to_sell
    assert result.final_rub == expected_final
    assert result.profit_rub == expected_final - Decimal("1000000")
    assert result.profit_percent == result.profit_rub / Decimal("1000000") * 100


@pytest.mark.parametrize("network_fee", ["10", "10.01"])
def test_calculate_amount_rejects_fee_that_leaves_no_usdt(network_fee: str) -> None:
    market = snapshot(
        amount="100",
        altyn_rate="10",
        network_fee=network_fee,
        rapira_bid="11",
        rapira_ask="12",
    )

    with pytest.raises(InsufficientAmountError, match="network fee"):
        calculate_amount(market)


@pytest.mark.parametrize("amount", ["0.001", "1000000000000.01"])
def test_calculate_amount_rejects_out_of_range_quote_amount(amount: str) -> None:
    with pytest.raises(InvalidAmountError):
        calculate_amount(snapshot(amount=amount))
