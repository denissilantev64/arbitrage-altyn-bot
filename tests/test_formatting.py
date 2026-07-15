# ruff: noqa: RUF001 -- assertions contain the required Russian UI copy.

from decimal import Decimal

from arbitrage_bot.domain import AmountResult, SpreadResult
from arbitrage_bot.formatting import format_spread_message


def example_spread() -> SpreadResult:
    altyn_rate = Decimal("77.39")
    rapira_bid = Decimal("80.02")
    rapira_effective_bid = rapira_bid * Decimal("0.999")
    gross_spread = rapira_bid - altyn_rate
    net_spread = rapira_effective_bid - altyn_rate
    return SpreadResult(
        altyn_rate=altyn_rate,
        rapira_bid=rapira_bid,
        rapira_effective_bid=rapira_effective_bid,
        rapira_sell_fee_rate=Decimal("0.001"),
        gross_spread_rub=gross_spread,
        gross_spread_percent=gross_spread / altyn_rate * 100,
        net_spread_rub=net_spread,
        net_spread_percent=net_spread / altyn_rate * 100,
    )


def test_format_spread_message_has_only_the_supported_direction_and_no_network_fee() -> None:
    message = format_spread_message(example_spread())

    assert message == (
        "<b>Altyn → Rapira | USDT/RUB</b>\n"
        "\n"
        "<b>Спред без комиссии: +2.63 RUB/USDT (+3.40%)</b>\n"
        "Покупка на Altyn: 77.39 | Продажа на Rapira: 80.02\n"
        "Комиссия Altyn включена в курс\n"
        "Комиссия за продажу USDT на Rapira 0.1%\n"
        "<b>Спред с комиссией: +2.55 RUB/USDT (+3.29%)</b>"
    )
    assert "Сетевая комиссия" not in message


def test_format_amount_shows_network_fee_and_fee_adjusted_profit() -> None:
    spread = example_spread()
    budget = Decimal("1000000")
    network_fee = Decimal("3")
    usdt_bought = budget / spread.altyn_rate
    usdt_to_sell = usdt_bought - network_fee
    final_rub = usdt_to_sell * spread.rapira_effective_bid
    amount = AmountResult(
        amount_rub=budget,
        usdt_bought=usdt_bought,
        network_fee_usdt=network_fee,
        usdt_to_sell=usdt_to_sell,
        final_rub=final_rub,
        profit_rub=final_rub - budget,
        profit_percent=(final_rub - budget) / budget * 100,
    )

    assert format_spread_message(spread, amount) == (
        "<b>Altyn → Rapira | USDT/RUB</b>\n"
        "\n"
        "<b>Спред без комиссии: +2.63 RUB/USDT (+3.40%)</b>\n"
        "Покупка на Altyn: 77.39 | Продажа на Rapira: 80.02\n"
        "Комиссия Altyn включена в курс\n"
        "Комиссия за продажу USDT на Rapira 0.1%\n"
        "<b>Спред с комиссией: +2.55 RUB/USDT (+3.29%)</b>\n"
        "\n"
        "<b>Расчет на 1 000 000 RUB</b>\n"
        "Сетевая комиссия: 3.00 USDT\n"
        "USDT к продаже: 12 918.57\n"
        "Прибыль с учетом комиссии: +32 709.92 RUB (+3.27%)\n"
        "Расчет индикативный и не учитывает глубину стакана."
    )


def test_formatting_omits_zero_rapira_fee_but_keeps_altyn_disclosure() -> None:
    spread = SpreadResult(
        altyn_rate=Decimal("80"),
        rapira_bid=Decimal("82"),
        rapira_effective_bid=Decimal("82"),
        rapira_sell_fee_rate=Decimal("0"),
        gross_spread_rub=Decimal("2"),
        gross_spread_percent=Decimal("2.5"),
        net_spread_rub=Decimal("2"),
        net_spread_percent=Decimal("2.5"),
    )

    message = format_spread_message(spread)

    assert "Комиссия Altyn включена в курс" in message
    assert "Комиссия за продажу USDT на Rapira" not in message


def test_round_half_up_and_negative_zero_are_applied_only_for_output() -> None:
    spread = SpreadResult(
        altyn_rate=Decimal("77.385"),
        rapira_bid=Decimal("77.384"),
        rapira_effective_bid=Decimal("77.384"),
        rapira_sell_fee_rate=Decimal("0"),
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
