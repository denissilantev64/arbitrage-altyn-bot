from __future__ import annotations

from decimal import Decimal

ALTYN_RATES_URL = "https://api.lk.altyn.one/website/rates/"
RAPIRA_DEPTH_URL = "https://api.rapira.net/market/exchange-plate-mini"
RAPIRA_FEE_URL = "https://api.rapira.net/market/fee/page-query"
RAPIRA_SYMBOL = "USDT/RUB"

RAPIRA_PUBLIC_FEE_LEVEL = 0

RATE_REFRESH_SECONDS = 60
RATE_MAX_AGE_SECONDS = 180
HTTP_TIMEOUT_SECONDS = 15
MORNING_HOUR_MSK = 9
MORNING_MINUTE_MSK = 0
MOSCOW_UTC_OFFSET_HOURS = 3

MIN_AMOUNT_RUB = Decimal("0.01")
MAX_AMOUNT_RUB = Decimal("1000000000000.00")
