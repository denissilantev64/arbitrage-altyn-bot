from decimal import Decimal

import pytest

from arbitrage_bot.amounts import parse_amount_rub
from arbitrage_bot.errors import InvalidAmountError


@pytest.mark.parametrize(
    ("raw_amount", "expected"),
    [
        ("0.01", Decimal("0.01")),
        ("1", Decimal("1")),
        ("10.5", Decimal("10.5")),
        ("12,34", Decimal("12.34")),
        (" 1 000 000 ", Decimal("1000000")),
        ("1_000_000,50", Decimal("1000000.50")),
        ("1000000000000.00", Decimal("1000000000000.00")),
    ],
)
def test_parse_amount_rub(raw_amount: str, expected: Decimal) -> None:
    assert parse_amount_rub(raw_amount) == expected


@pytest.mark.parametrize(
    "raw_amount",
    [
        "",
        " ",
        "0",
        "-1",
        "+1",
        "01",
        ".50",
        "1.",
        "1.234",
        "1,2,3",
        "1 00",
        "1  000",
        "1__000",
        "1_000 000",
        "1000_000",
        "1e3",
        "NaN",
        "Infinity",
        "1000000000000.01",
    ],
)
def test_parse_amount_rub_rejects_invalid_or_out_of_range_input(raw_amount: str) -> None:
    with pytest.raises(InvalidAmountError):
        parse_amount_rub(raw_amount)
