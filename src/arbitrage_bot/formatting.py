# ruff: noqa: RUF001 -- user-facing text is intentionally written in Russian.

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from arbitrage_bot.domain import AmountResult, SpreadResult


def _format_fixed(
    value: Decimal,
    decimal_places: int,
    *,
    show_positive_sign: bool = False,
) -> str:
    quantum = Decimal(1).scaleb(-decimal_places)
    rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = rounded.copy_abs()

    rendered = f"{rounded:,.{decimal_places}f}".replace(",", " ")
    if show_positive_sign and rounded > 0:
        return f"+{rendered}"
    return rendered


def _format_fee_percent(rate: Decimal) -> str:
    rendered = format(rate * Decimal(100), "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    integer, separator, fraction = rendered.partition(".")
    grouped_integer = f"{int(integer):,}".replace(",", " ")
    return f"{grouped_integer}{separator}{fraction}"


def _format_budget(amount: Decimal) -> str:
    if amount == amount.to_integral_value():
        return _format_fixed(amount, 0)
    return _format_fixed(amount, 2)


def format_spread_message(
    spread: SpreadResult,
    amount: AmountResult | None = None,
) -> str:
    lines = [
        "<b>Altyn → Rapira | USDT/RUB</b>",
        "",
        (
            "<b>Спред без комиссии: "
            f"{_format_fixed(spread.gross_spread_rub, 2, show_positive_sign=True)} "
            "RUB/USDT ("
            f"{_format_fixed(spread.gross_spread_percent, 2, show_positive_sign=True)}%)</b>"
        ),
        (
            f"Покупка на Altyn: {_format_fixed(spread.altyn_rate, 2)}"
            f" | Продажа на Rapira: {_format_fixed(spread.rapira_bid, 2)}"
        ),
        "Комиссия Altyn включена в курс",
    ]

    if spread.rapira_sell_fee_rate != 0:
        lines.append(
            "Комиссия за продажу USDT на Rapira "
            f"{_format_fee_percent(spread.rapira_sell_fee_rate)}%"
        )

    lines.append(
        "<b>Спред с комиссией: "
        f"{_format_fixed(spread.net_spread_rub, 2, show_positive_sign=True)} RUB/USDT ("
        f"{_format_fixed(spread.net_spread_percent, 2, show_positive_sign=True)}%)</b>"
    )

    if amount is not None:
        lines.extend(
            [
                "",
                f"<b>Расчет на {_format_budget(amount.amount_rub)} RUB</b>",
                f"Сетевая комиссия: {_format_fixed(amount.network_fee_usdt, 2)} USDT",
                f"USDT к продаже: {_format_fixed(amount.usdt_to_sell, 2)}",
                (
                    "Прибыль с учетом комиссии: "
                    f"{_format_fixed(amount.profit_rub, 2, show_positive_sign=True)} RUB ("
                    f"{_format_fixed(amount.profit_percent, 2, show_positive_sign=True)}%)"
                ),
                "Расчет индикативный и не учитывает глубину стакана.",
            ]
        )

    return "\n".join(lines)
