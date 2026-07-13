from __future__ import annotations

import re
from decimal import Decimal

from arbitrage_bot.constants import MAX_AMOUNT_RUB, MIN_AMOUNT_RUB
from arbitrage_bot.errors import InvalidAmountError

_AMOUNT_PATTERN = re.compile(
    r"(?:"
    r"0|"
    r"[1-9][0-9]*|"
    r"[1-9][0-9]{0,2}(?: [0-9]{3})+|"
    r"[1-9][0-9]{0,2}(?:_[0-9]{3})+"
    r")(?:[.,][0-9]{1,2})?\Z",
    flags=re.ASCII,
)


def validate_amount_rub(amount: Decimal) -> Decimal:
    if not amount.is_finite() or amount < MIN_AMOUNT_RUB or amount > MAX_AMOUNT_RUB:
        raise InvalidAmountError(f"Сумма должна быть от {MIN_AMOUNT_RUB} до {MAX_AMOUNT_RUB} RUB")
    return amount


def parse_amount_rub(raw_amount: str) -> Decimal:
    """Parse a RUB amount without accepting ambiguous or silently rounded input."""
    normalized = raw_amount.strip()
    if not _AMOUNT_PATTERN.fullmatch(normalized):
        raise InvalidAmountError(
            "Укажите сумму в RUB числом, используя не более двух знаков после запятой"
        )

    amount = Decimal(normalized.replace(" ", "").replace("_", "").replace(",", "."))
    return validate_amount_rub(amount)
