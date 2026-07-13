from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Final, cast

import aiohttp

from .constants import (
    ALTYN_BUY_FEE_RATE,
    ALTYN_RATES_URL,
    ALTYN_SELL_FEE_RATE,
    HTTP_TIMEOUT_SECONDS,
    RAPIRA_DEPTH_URL,
    RAPIRA_FEE_URL,
    RAPIRA_PUBLIC_FEE_LEVEL,
    RAPIRA_SYMBOL,
)
from .domain import BuyFeeMode, Exchange, ExchangeQuote, RateSnapshot
from .errors import MarketDataError

_ALTYN: Final = "altyn"
_RAPIRA: Final = "rapira"
_MISSING: Final = object()

_ALTYN_HEADERS: Final = {
    "Accept": "application/json",
    "Referer": "https://altyn.one/",
}
_RAPIRA_HEADERS: Final = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded",
}


def _error(service: str, code: str, detail: str) -> MarketDataError:
    return MarketDataError(service, code, detail)


def _mapping(value: object, service: str, path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _error(service, "invalid_schema", f"{path} must be an object")
    return cast(dict[str, object], value)


def _list(value: object, service: str, path: str) -> list[object]:
    if not isinstance(value, list):
        raise _error(service, "invalid_schema", f"{path} must be an array")
    return cast(list[object], value)


def _required(mapping: dict[str, object], key: str, service: str, path: str) -> object:
    value = mapping.get(key, _MISSING)
    if value is _MISSING:
        raise _error(service, "invalid_schema", f"{path}.{key} is required")
    return value


def _string(value: object, service: str, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise _error(service, "invalid_schema", f"{path} must be a non-empty string")
    return value


def _decimal(value: object, service: str, path: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise _error(service, "invalid_value", f"{path} must be a decimal number")
    if isinstance(value, str) and (not value or value != value.strip()):
        raise _error(service, "invalid_value", f"{path} must be a decimal number")

    try:
        parsed = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise _error(service, "invalid_value", f"{path} must be a decimal number") from exc

    if not parsed.is_finite():
        raise _error(service, "invalid_value", f"{path} must be finite")
    return parsed


def _positive_decimal(value: object, service: str, path: str) -> Decimal:
    parsed = _decimal(value, service, path)
    if parsed <= 0:
        raise _error(service, "invalid_value", f"{path} must be positive")
    return parsed


def parse_altyn_rates(payload: object) -> ExchangeQuote:
    """Validate Altyn's directional rates and build a USDT/RUB quote."""

    rows = _list(payload, _ALTYN, "response")
    wanted = {("USDT", "RUB"), ("RUB", "USDT")}
    found: dict[tuple[str, str], Decimal] = {}

    for index, raw_row in enumerate(rows):
        path = f"response[{index}]"
        row = _mapping(raw_row, _ALTYN, path)
        from_currency = _string(
            _required(row, "from_currency", _ALTYN, path),
            _ALTYN,
            f"{path}.from_currency",
        )
        to_currency = _string(
            _required(row, "to_currency", _ALTYN, path),
            _ALTYN,
            f"{path}.to_currency",
        )
        rate = _positive_decimal(
            _required(row, "rate", _ALTYN, path),
            _ALTYN,
            f"{path}.rate",
        )

        direction = (from_currency, to_currency)
        if direction not in wanted:
            continue
        if direction in found:
            raise _error(
                _ALTYN,
                "duplicate_rate",
                f"duplicate {from_currency}->{to_currency} rate",
            )
        found[direction] = rate

    missing = wanted.difference(found)
    if missing:
        directions = ", ".join(f"{source}->{target}" for source, target in sorted(missing))
        raise _error(_ALTYN, "missing_rate", f"missing rate: {directions}")

    bid = found[("USDT", "RUB")]
    ask = Decimal(1) / found[("RUB", "USDT")]
    if bid > ask:
        raise _error(_ALTYN, "crossed_market", "USDT/RUB bid exceeds ask")

    try:
        return ExchangeQuote(
            exchange=Exchange.ALTYN,
            bid=bid,
            ask=ask,
            buy_fee_rate=ALTYN_BUY_FEE_RATE,
            sell_fee_rate=ALTYN_SELL_FEE_RATE,
            buy_fee_mode=BuyFeeMode.ADDED_TO_QUOTE,
        )
    except ValueError as exc:
        raise _error(_ALTYN, "invalid_quote", str(exc)) from exc


def _validate_exact_market_field(
    market: dict[str, object],
    field: str,
    expected: str,
    path: str,
    *,
    required: bool,
) -> None:
    value = market.get(field, _MISSING)
    if value is _MISSING:
        if not required:
            return
        raise _error(_RAPIRA, "invalid_market", f"{path}.{field} is required")
    if not isinstance(value, str) or value != expected:
        raise _error(_RAPIRA, "invalid_market", f"{path}.{field} must equal {expected!r}")


def _best_price(side: dict[str, object], side_name: str, *, choose_minimum: bool) -> Decimal:
    raw_items = _required(side, "items", _RAPIRA, side_name)
    items = _list(raw_items, _RAPIRA, f"{side_name}.items")
    if not items:
        raise _error(_RAPIRA, "empty_market", f"{side_name}.items is empty")

    prices: list[Decimal] = []
    for index, raw_item in enumerate(items):
        path = f"{side_name}.items[{index}]"
        item = _mapping(raw_item, _RAPIRA, path)
        price = _positive_decimal(
            _required(item, "price", _RAPIRA, path),
            _RAPIRA,
            f"{path}.price",
        )
        _positive_decimal(
            _required(item, "amount", _RAPIRA, path),
            _RAPIRA,
            f"{path}.amount",
        )
        prices.append(price)

    return min(prices) if choose_minimum else max(prices)


def parse_rapira_depth(payload: object) -> tuple[Decimal, Decimal]:
    """Return the executable USDT/RUB bid and ask from Rapira's order book."""

    root = _mapping(payload, _RAPIRA, "response")
    app_code = root.get("code", _MISSING)
    if app_code is not _MISSING:
        if type(app_code) is not int:
            raise _error(_RAPIRA, "invalid_schema", "response.code must be an integer")
        if app_code != 0:
            raise _error(_RAPIRA, "application_error", f"depth API returned code {app_code}")
    _validate_exact_market_field(
        root,
        "symbol",
        RAPIRA_SYMBOL,
        "response",
        required=False,
    )

    ask_side = _mapping(_required(root, "ask", _RAPIRA, "response"), _RAPIRA, "ask")
    bid_side = _mapping(_required(root, "bid", _RAPIRA, "response"), _RAPIRA, "bid")

    _validate_exact_market_field(ask_side, "symbol", RAPIRA_SYMBOL, "ask", required=True)
    _validate_exact_market_field(bid_side, "symbol", RAPIRA_SYMBOL, "bid", required=True)
    _validate_exact_market_field(ask_side, "direction", "SELL", "ask", required=True)
    _validate_exact_market_field(bid_side, "direction", "BUY", "bid", required=True)

    ask = _best_price(ask_side, "ask", choose_minimum=True)
    bid = _best_price(bid_side, "bid", choose_minimum=False)
    if bid > ask:
        raise _error(_RAPIRA, "crossed_market", "USDT/RUB bid exceeds ask")
    return bid, ask


def _is_public_fee_level(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, str):
        return value == str(RAPIRA_PUBLIC_FEE_LEVEL)
    return type(value) is int and value == RAPIRA_PUBLIC_FEE_LEVEL


def parse_rapira_fee(payload: object) -> Decimal:
    """Return Rapira's level-zero taker fee for USDT/RUB."""

    root = _mapping(payload, _RAPIRA, "response")
    code = _required(root, "code", _RAPIRA, "response")
    if type(code) is not int:
        raise _error(_RAPIRA, "invalid_schema", "response.code must be an integer")
    if code != 0:
        raise _error(_RAPIRA, "application_error", f"fee API returned code {code}")

    data = _mapping(_required(root, "data", _RAPIRA, "response"), _RAPIRA, "data")
    content = _list(_required(data, "content", _RAPIRA, "data"), _RAPIRA, "data.content")
    if len(content) != 1:
        raise _error(
            _RAPIRA,
            "invalid_fee",
            "data.content must contain exactly one fee record",
        )

    record = _mapping(content[0], _RAPIRA, "data.content[0]")
    symbol = _string(
        _required(record, "symbol", _RAPIRA, "data.content[0]"),
        _RAPIRA,
        "data.content[0].symbol",
    )
    if symbol != RAPIRA_SYMBOL:
        raise _error(_RAPIRA, "invalid_fee", f"fee symbol must equal {RAPIRA_SYMBOL!r}")

    level = _required(record, "level", _RAPIRA, "data.content[0]")
    if not _is_public_fee_level(level):
        raise _error(
            _RAPIRA,
            "invalid_fee",
            f"fee level must equal {RAPIRA_PUBLIC_FEE_LEVEL}",
        )

    fee = _decimal(
        _required(record, "takerFee", _RAPIRA, "data.content[0]"),
        _RAPIRA,
        "data.content[0].takerFee",
    )
    if fee < 0 or fee >= 1:
        raise _error(_RAPIRA, "invalid_fee", "taker fee must be in [0, 1)")
    return fee


def _is_json_content_type(value: str) -> bool:
    media_type = value.partition(";")[0].strip().lower()
    return media_type == "application/json" or (
        media_type.startswith("application/") and media_type.endswith("+json")
    )


async def _decode_json_response(response: aiohttp.ClientResponse, service: str) -> object:
    status = response.status
    if status < 200 or status >= 300:
        raise _error(service, "http_status", f"HTTP request failed with status {status}")

    content_type = response.headers.get("Content-Type", "")
    if not _is_json_content_type(content_type):
        raise _error(service, "invalid_content_type", "response is not JSON")

    try:
        return await response.json()
    except (aiohttp.ClientError, TypeError, ValueError, UnicodeError) as exc:
        raise _error(service, "invalid_json", "response body is not valid JSON") from exc


class RateCollector:
    """Fetch and validate one complete Altyn/Rapira market snapshot."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)

    async def _get_json(self, url: str, service: str, headers: dict[str, str]) -> object:
        try:
            response = await self._session.get(url, headers=headers, timeout=self._timeout)
        except (aiohttp.ClientError, TimeoutError) as exc:
            detail = f"request failed: {type(exc).__name__}"
            raise _error(service, "request_failed", detail) from exc

        try:
            return await _decode_json_response(response, service)
        finally:
            response.release()

    async def _post_json(
        self,
        url: str,
        service: str,
        data: dict[str, str],
    ) -> object:
        try:
            response = await self._session.post(
                url,
                data=data,
                headers=_RAPIRA_HEADERS,
                timeout=self._timeout,
            )
        except (aiohttp.ClientError, TimeoutError) as exc:
            detail = f"request failed: {type(exc).__name__}"
            raise _error(service, "request_failed", detail) from exc

        try:
            return await _decode_json_response(response, service)
        finally:
            response.release()

    async def fetch_altyn_quote(self) -> ExchangeQuote:
        payload = await self._get_json(ALTYN_RATES_URL, _ALTYN, _ALTYN_HEADERS)
        return parse_altyn_rates(payload)

    async def fetch_rapira_quote(self) -> ExchangeQuote:
        depth_payload, fee_payload = await asyncio.gather(
            self._post_json(
                RAPIRA_DEPTH_URL,
                _RAPIRA,
                {"symbol": RAPIRA_SYMBOL},
            ),
            self._post_json(
                RAPIRA_FEE_URL,
                _RAPIRA,
                {
                    "startLevel": str(RAPIRA_PUBLIC_FEE_LEVEL),
                    "endLevel": str(RAPIRA_PUBLIC_FEE_LEVEL),
                    "symbol": RAPIRA_SYMBOL,
                },
            ),
        )
        bid, ask = parse_rapira_depth(depth_payload)
        taker_fee = parse_rapira_fee(fee_payload)

        try:
            return ExchangeQuote(
                exchange=Exchange.RAPIRA,
                bid=bid,
                ask=ask,
                buy_fee_rate=taker_fee,
                sell_fee_rate=taker_fee,
                buy_fee_mode=BuyFeeMode.DEDUCTED_FROM_BASE,
            )
        except ValueError as exc:
            raise _error(_RAPIRA, "invalid_quote", str(exc)) from exc

    async def collect(self) -> RateSnapshot:
        altyn, rapira = await asyncio.gather(
            self.fetch_altyn_quote(),
            self.fetch_rapira_quote(),
        )
        return RateSnapshot(altyn=altyn, rapira=rapira, fetched_at=datetime.now(UTC))
