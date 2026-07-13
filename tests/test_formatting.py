# ruff: noqa: RUF001 -- assertions contain the required Russian UI copy.

from decimal import Decimal

from arbitrage_bot.domain import AmountResult, Exchange, SpreadResult
from arbitrage_bot.formatting import format_spread_message


def example_spread() -> SpreadResult:
    effective_buy = Decimal("77.39") * Decimal("1.015")
    effective_sell = Decimal("80.02")
    net_spread = effective_sell - effective_buy
    return SpreadResult(
        buy_exchange=Exchange.ALTYN,
        sell_exchange=Exchange.RAPIRA,
        raw_buy=Decimal("77.39"),
        raw_sell=Decimal("80.02"),
        effective_buy=effective_buy,
        effective_sell=effective_sell,
        buy_fee_rate=Decimal("0.015"),
        sell_fee_rate=Decimal("0"),
        gross_spread_rub=Decimal("2.63"),
        gross_spread_percent=Decimal("2.63") / Decimal("77.39") * 100,
        net_spread_rub=net_spread,
        net_spread_percent=net_spread / effective_buy * 100,
    )


def test_format_spread_message_matches_requested_example() -> None:
    assert format_spread_message(example_spread()) == (
        "<b>Altyn → Rapira | USDT/RUB</b>\n"
        "\n"
        "<b>Спред без комиссии: +2.63 RUB/USDT (+3.40%)</b>\n"
        "Покупка на Altyn: 77.39 | Продажа на Rapira: 80.02\n"
        "Комиссия за покупку USDT на Altyn 1.5%\n"
        "<b>Спред с комиссией: +1.47 RUB/USDT (+1.87%)</b>"
    )


def test_format_amount_uses_fee_adjusted_prices_and_space_grouping() -> None:
    spread = example_spread()
    budget = Decimal("1000000")
    usdt = budget / spread.effective_buy
    final_rub = usdt * spread.effective_sell
    amount = AmountResult(
        amount_rub=budget,
        usdt_to_sell=usdt,
        final_rub=final_rub,
        profit_rub=final_rub - budget,
        profit_percent=(final_rub - budget) / budget * 100,
    )

    assert format_spread_message(spread, amount) == (
        "<b>Altyn → Rapira | USDT/RUB</b>\n"
        "\n"
        "<b>Спред без комиссии: +2.63 RUB/USDT (+3.40%)</b>\n"
        "Покупка на Altyn: 77.39 | Продажа на Rapira: 80.02\n"
        "Комиссия за покупку USDT на Altyn 1.5%\n"
        "<b>Спред с комиссией: +1.47 RUB/USDT (+1.87%)</b>\n"
        "\n"
        "<b>Расчет на 1 000 000 RUB</b>\n"
        "USDT к продаже: 12 730.61\n"
        "Прибыль с учетом комиссии: +18 703.17 RUB (+1.87%)\n"
        "Расчет индикативный и не учитывает глубину стакана и переводы."
    )


def test_formatting_lists_nonzero_buy_and_sell_fees() -> None:
    spread = SpreadResult(
        buy_exchange=Exchange.RAPIRA,
        sell_exchange=Exchange.ALTYN,
        raw_buy=Decimal("80"),
        raw_sell=Decimal("82"),
        effective_buy=Decimal("80.08"),
        effective_sell=Decimal("81.836"),
        buy_fee_rate=Decimal("0.001"),
        sell_fee_rate=Decimal("0.002"),
        gross_spread_rub=Decimal("2"),
        gross_spread_percent=Decimal("2.5"),
        net_spread_rub=Decimal("1.756"),
        net_spread_percent=Decimal("2.192806"),
    )

    message = format_spread_message(spread)

    assert "Комиссия за покупку USDT на Rapira 0.1%" in message
    assert "Комиссия за продажу USDT на Altyn 0.2%" in message


def test_round_half_up_and_negative_zero_are_applied_only_for_output() -> None:
    spread = SpreadResult(
        buy_exchange=Exchange.ALTYN,
        sell_exchange=Exchange.RAPIRA,
        raw_buy=Decimal("77.385"),
        raw_sell=Decimal("77.384"),
        effective_buy=Decimal("77.385"),
        effective_sell=Decimal("77.384"),
        buy_fee_rate=Decimal("0"),
        sell_fee_rate=Decimal("0"),
        gross_spread_rub=Decimal("-0.001"),
        gross_spread_percent=Decimal("-0.004"),
        net_spread_rub=Decimal("-0.001"),
        net_spread_percent=Decimal("-0.004"),
    )

    message = format_spread_message(spread)

    assert "Покупка на Altyn: 77.39" in message
    assert "Спред без комиссии: 0.00 RUB/USDT (0.00%)" in message
    assert "Спред с комиссией: 0.00 RUB/USDT (0.00%)" in message
    assert "-0.00" not in message
