from datetime import UTC, datetime
from decimal import Decimal

import pytest

from arbitrage_bot.calculations import calculate_amount, calculate_best_spread
from arbitrage_bot.domain import BuyFeeMode, Exchange, ExchangeQuote, RateSnapshot
from arbitrage_bot.errors import InvalidAmountError


def quote(
    exchange: Exchange,
    *,
    bid: str,
    ask: str,
    buy_fee: str = "0",
    sell_fee: str = "0",
    buy_fee_mode: BuyFeeMode = BuyFeeMode.ADDED_TO_QUOTE,
) -> ExchangeQuote:
    return ExchangeQuote(
        exchange=exchange,
        bid=Decimal(bid),
        ask=Decimal(ask),
        buy_fee_rate=Decimal(buy_fee),
        sell_fee_rate=Decimal(sell_fee),
        buy_fee_mode=buy_fee_mode,
    )


def snapshot(altyn: ExchangeQuote, rapira: ExchangeQuote) -> RateSnapshot:
    return RateSnapshot(altyn=altyn, rapira=rapira, fetched_at=datetime.now(UTC))


def test_altyn_to_rapira_uses_unrounded_effective_prices() -> None:
    result = calculate_best_spread(
        snapshot(
            quote(Exchange.ALTYN, bid="77", ask="77.39", buy_fee="0.015"),
            quote(Exchange.RAPIRA, bid="80.02", ask="80.10"),
        )
    )

    assert result.buy_exchange is Exchange.ALTYN
    assert result.sell_exchange is Exchange.RAPIRA
    assert result.gross_spread_rub == Decimal("2.63")
    assert result.gross_spread_percent == Decimal("2.63") / Decimal("77.39") * 100
    assert result.effective_buy == Decimal("77.39") * Decimal("1.015")
    assert result.effective_sell == Decimal("80.02")
    assert result.net_spread_rub == Decimal("80.02") - Decimal("77.39") * Decimal("1.015")
    assert result.net_spread_percent == (
        result.net_spread_rub / result.effective_buy * Decimal(100)
    )


def test_calculate_amount_matches_full_precision_example() -> None:
    spread = calculate_best_spread(
        snapshot(
            quote(Exchange.ALTYN, bid="77", ask="77.39", buy_fee="0.015"),
            quote(Exchange.RAPIRA, bid="80.02", ask="80.10"),
        )
    )

    result = calculate_amount(spread, Decimal("1000000"))

    expected_usdt = Decimal("1000000") / Decimal("78.55085")
    expected_final = expected_usdt * Decimal("80.02")
    assert result.usdt_to_sell == expected_usdt
    assert result.final_rub == expected_final
    assert result.profit_rub == expected_final - Decimal("1000000")
    assert result.profit_percent == result.profit_rub / Decimal("1000000") * 100


def test_reverse_direction_can_win() -> None:
    result = calculate_best_spread(
        snapshot(
            quote(Exchange.ALTYN, bid="82", ask="83"),
            quote(Exchange.RAPIRA, bid="79", ask="80", buy_fee="0.001"),
        )
    )

    assert result.buy_exchange is Exchange.RAPIRA
    assert result.sell_exchange is Exchange.ALTYN
    assert result.effective_buy == Decimal("80.080")
    assert result.effective_sell == Decimal("82")


def test_fees_use_deducted_base_ask_and_net_bid() -> None:
    result = calculate_best_spread(
        snapshot(
            quote(Exchange.ALTYN, bid="101", ask="102", sell_fee="0.002"),
            quote(
                Exchange.RAPIRA,
                bid="99",
                ask="100",
                buy_fee="0.001",
                buy_fee_mode=BuyFeeMode.DEDUCTED_FROM_BASE,
            ),
        )
    )

    assert result.buy_exchange is Exchange.RAPIRA
    assert result.effective_buy == Decimal("100") / Decimal("0.999")
    assert result.effective_sell == Decimal("101") * Decimal("0.998")


def test_best_of_two_negative_directions_is_returned() -> None:
    result = calculate_best_spread(
        snapshot(
            quote(Exchange.ALTYN, bid="79", ask="80"),
            quote(Exchange.RAPIRA, bid="78", ask="81"),
        )
    )

    assert result.buy_exchange is Exchange.RAPIRA
    assert result.sell_exchange is Exchange.ALTYN
    assert result.net_spread_percent < 0


def test_exact_roi_tie_prefers_altyn_to_rapira() -> None:
    result = calculate_best_spread(
        snapshot(
            quote(Exchange.ALTYN, bid="80", ask="80"),
            quote(Exchange.RAPIRA, bid="80", ask="80"),
        )
    )

    assert result.buy_exchange is Exchange.ALTYN
    assert result.sell_exchange is Exchange.RAPIRA


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("1000000000000.01")])
def test_calculate_amount_rejects_out_of_range_amount(amount: Decimal) -> None:
    spread = calculate_best_spread(
        snapshot(
            quote(Exchange.ALTYN, bid="80", ask="80"),
            quote(Exchange.RAPIRA, bid="80", ask="80"),
        )
    )

    with pytest.raises(InvalidAmountError):
        calculate_amount(spread, amount)
