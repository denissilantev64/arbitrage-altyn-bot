from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock

import aiohttp
import pytest

import arbitrage_bot.rates as rates_module
from arbitrage_bot.constants import (
    ALTYN_ARBITRAGE_RATE_URL,
    ALTYN_REFERENCE_AMOUNT_RUB,
    HTTP_TIMEOUT_SECONDS,
    RAPIRA_DEPTH_URL,
    RAPIRA_FEE_URL,
    RAPIRA_SYMBOL,
)
from arbitrage_bot.domain import BuyFeeMode, Exchange
from arbitrage_bot.errors import MarketDataError
from arbitrage_bot.rates import (
    RateCollector,
    parse_altyn_arbitrage_rate,
    parse_rapira_depth,
    parse_rapira_fee,
)

_ALTYN_TOKEN = "a" * 64
_RECEIVED_AT = datetime(2026, 7, 15, 7, 38, 50, tzinfo=UTC)


def altyn_payload(*, as_of: datetime | None = None) -> dict[str, object]:
    return {
        "base_currency": "USDT",
        "quote_currency": "RUB",
        "network": "TRC20",
        "amount_rub": "1000000.00",
        "rate": "79.88",
        "network_fee_usdt": "3.00",
        "indicative": True,
        "as_of": (as_of or datetime(2026, 7, 15, 7, 38, 45, 559550, tzinfo=UTC)).isoformat(),
    }


def depth_payload() -> dict[str, object]:
    return {
        "symbol": RAPIRA_SYMBOL,
        "ask": {
            "direction": "SELL",
            "symbol": RAPIRA_SYMBOL,
            "highestPrice": "1",
            "lowestPrice": "99999",
            "items": [
                {"price": "80.20", "amount": "2"},
                {"price": "80.02", "amount": "3"},
            ],
        },
        "bid": {
            "direction": "BUY",
            "symbol": RAPIRA_SYMBOL,
            "highestPrice": "99999",
            "lowestPrice": "1",
            "items": [
                {"price": "79.90", "amount": "2"},
                {"price": "80.01", "amount": "3"},
            ],
        },
    }


def fee_payload(taker_fee: object = "0.00100000") -> dict[str, object]:
    return {
        "code": 0,
        "message": "SUCCESS",
        "data": {
            "content": [
                {
                    "level": "0",
                    "symbol": RAPIRA_SYMBOL,
                    "makerFee": "0",
                    "takerFee": taker_fee,
                }
            ]
        },
    }


def _parse_altyn(payload: object):
    return parse_altyn_arbitrage_rate(
        payload,
        requested_amount_rub=ALTYN_REFERENCE_AMOUNT_RUB,
        received_at=_RECEIVED_AT,
    )


def test_parse_altyn_arbitrage_rate_returns_amount_specific_buy_quote() -> None:
    quote = _parse_altyn(altyn_payload())

    assert quote.amount_rub == Decimal("1000000.00")
    assert quote.rate == Decimal("79.88")
    assert quote.network_fee_usdt == Decimal("3.00")
    assert quote.indicative is True
    assert quote.as_of == datetime(2026, 7, 15, 7, 38, 45, 559550, tzinfo=UTC)


@pytest.mark.parametrize("payload", [None, [], "invalid"])
def test_parse_altyn_arbitrage_rate_requires_object_root(payload: object) -> None:
    with pytest.raises(MarketDataError) as exc_info:
        _parse_altyn(payload)

    assert exc_info.value.code == "invalid_schema"


@pytest.mark.parametrize(
    ("field", "value", "expected_code"),
    [
        ("base_currency", "BTC", "invalid_market"),
        ("quote_currency", "USD", "invalid_market"),
        ("network", "ERC20", "invalid_market"),
        ("amount_rub", "999999.99", "amount_mismatch"),
        ("amount_rub", "0", "invalid_value"),
        ("rate", "0", "invalid_value"),
        ("rate", "NaN", "invalid_value"),
        ("network_fee_usdt", "-0.01", "invalid_value"),
        ("network_fee_usdt", "Infinity", "invalid_value"),
        ("indicative", 1, "invalid_schema"),
        ("as_of", "not-a-date", "invalid_value"),
        ("as_of", "2026-07-15T07:38:45", "invalid_value"),
    ],
)
def test_parse_altyn_arbitrage_rate_rejects_invalid_fields(
    field: str,
    value: object,
    expected_code: str,
) -> None:
    payload = altyn_payload()
    payload[field] = value

    with pytest.raises(MarketDataError) as exc_info:
        _parse_altyn(payload)

    assert exc_info.value.code == expected_code


@pytest.mark.parametrize("field", list(altyn_payload()))
def test_parse_altyn_arbitrage_rate_requires_every_field(field: str) -> None:
    payload = altyn_payload()
    del payload[field]

    with pytest.raises(MarketDataError) as exc_info:
        _parse_altyn(payload)

    assert exc_info.value.code == "invalid_schema"


@pytest.mark.parametrize("field", ["amount_rub", "rate", "network_fee_usdt"])
@pytest.mark.parametrize("value", [1, 1.0, True, "", " 1", "1 "])
def test_parse_altyn_arbitrage_rate_requires_decimal_strings(
    field: str,
    value: object,
) -> None:
    payload = altyn_payload()
    payload[field] = value

    with pytest.raises(MarketDataError):
        _parse_altyn(payload)


def test_parse_altyn_arbitrage_rate_enforces_source_time_boundaries() -> None:
    payload = altyn_payload()
    payload["as_of"] = (_RECEIVED_AT - timedelta(seconds=60)).isoformat()
    assert _parse_altyn(payload).as_of == _RECEIVED_AT - timedelta(seconds=60)

    payload["as_of"] = (_RECEIVED_AT - timedelta(seconds=60, microseconds=1)).isoformat()
    with pytest.raises(MarketDataError, match="stale_rate"):
        _parse_altyn(payload)

    payload["as_of"] = (_RECEIVED_AT + timedelta(seconds=5)).isoformat()
    assert _parse_altyn(payload).as_of == _RECEIVED_AT + timedelta(seconds=5)

    payload["as_of"] = (_RECEIVED_AT + timedelta(seconds=5, microseconds=1)).isoformat()
    with pytest.raises(MarketDataError, match="future_rate"):
        _parse_altyn(payload)


def test_parse_rapira_depth_uses_best_item_prices() -> None:
    bid, ask = parse_rapira_depth(depth_payload())

    assert bid == Decimal("80.01")
    assert ask == Decimal("80.02")


def test_parse_rapira_depth_accepts_absent_optional_root_symbol() -> None:
    payload = {
        "ask": {
            "symbol": RAPIRA_SYMBOL,
            "direction": "SELL",
            "items": [{"price": 81, "amount": 1}],
        },
        "bid": {
            "symbol": RAPIRA_SYMBOL,
            "direction": "BUY",
            "items": [{"price": 80, "amount": 1}],
        },
    }

    assert parse_rapira_depth(payload) == (Decimal("80"), Decimal("81"))


@pytest.mark.parametrize("code", [False, "0", 1])
def test_parse_rapira_depth_rejects_invalid_optional_application_code(code: object) -> None:
    payload = depth_payload()
    payload["code"] = code

    with pytest.raises(MarketDataError):
        parse_rapira_depth(payload)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("symbol",), "BTC/RUB"),
        (("ask", "symbol"), "BTC/RUB"),
        (("bid", "symbol"), "BTC/RUB"),
        (("ask", "direction"), "BUY"),
        (("bid", "direction"), "SELL"),
        (("ask", "direction"), None),
    ],
)
def test_parse_rapira_depth_rejects_wrong_pair_or_direction(
    path: tuple[str, ...],
    value: object,
) -> None:
    payload = depth_payload()
    target: dict[str, Any] = payload
    for key in path[:-1]:
        target = cast(dict[str, Any], target[key])
    target[path[-1]] = value

    with pytest.raises(MarketDataError) as exc_info:
        parse_rapira_depth(payload)

    assert exc_info.value.code == "invalid_market"


@pytest.mark.parametrize(
    ("side", "field"),
    [
        ("ask", "symbol"),
        ("ask", "direction"),
        ("bid", "symbol"),
        ("bid", "direction"),
    ],
)
def test_parse_rapira_depth_rejects_missing_side_market_field(side: str, field: str) -> None:
    payload = depth_payload()
    del cast(dict[str, object], payload[side])[field]

    with pytest.raises(MarketDataError) as exc_info:
        parse_rapira_depth(payload)

    assert exc_info.value.code == "invalid_market"


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"ask": [], "bid": {}},
        {"ask": {}, "bid": {"items": [{"price": "1", "amount": "1"}]}},
        {"ask": {"items": "bad"}, "bid": {"items": [{"price": "1", "amount": "1"}]}},
        {"ask": {"items": [None]}, "bid": {"items": [{"price": "1", "amount": "1"}]}},
        {"ask": {"items": [{}]}, "bid": {"items": [{"price": "1", "amount": "1"}]}},
        {
            "ask": {"items": [{"price": "bad", "amount": "1"}]},
            "bid": {"items": [{"price": "1", "amount": "1"}]},
        },
    ],
)
def test_parse_rapira_depth_rejects_schema_errors(payload: object) -> None:
    with pytest.raises(MarketDataError):
        parse_rapira_depth(payload)


@pytest.mark.parametrize("side", ["ask", "bid"])
def test_parse_rapira_depth_rejects_empty_side(side: str) -> None:
    payload = depth_payload()
    cast(dict[str, object], payload[side])["items"] = []

    with pytest.raises(MarketDataError) as exc_info:
        parse_rapira_depth(payload)

    assert exc_info.value.code == "empty_market"


@pytest.mark.parametrize("side", ["ask", "bid"])
@pytest.mark.parametrize("field", ["price", "amount"])
@pytest.mark.parametrize("value", [None, 0, "-1", "NaN", float("inf"), True])
def test_parse_rapira_depth_rejects_invalid_price_or_amount(
    side: str,
    field: str,
    value: object,
) -> None:
    payload = depth_payload()
    items = cast(list[dict[str, object]], cast(dict[str, Any], payload[side])["items"])
    if value is None:
        del items[0][field]
    else:
        items[0][field] = value

    with pytest.raises(MarketDataError):
        parse_rapira_depth(payload)


@pytest.mark.parametrize("field", ["price", "amount"])
def test_parse_rapira_depth_rejects_one_invalid_level_among_valid_levels(field: str) -> None:
    payload = depth_payload()
    items = cast(list[dict[str, object]], cast(dict[str, Any], payload["ask"])["items"])
    items.append({"price": "80.50", "amount": "1"})
    items[1][field] = "0"

    with pytest.raises(MarketDataError):
        parse_rapira_depth(payload)


def test_parse_rapira_depth_rejects_crossed_market() -> None:
    payload = depth_payload()
    cast(dict[str, object], payload["bid"])["items"] = [{"price": "80.03", "amount": "1"}]

    with pytest.raises(MarketDataError) as exc_info:
        parse_rapira_depth(payload)

    assert exc_info.value.code == "crossed_market"


@pytest.mark.parametrize("zero", [0, "0", "0.00000000"])
def test_parse_rapira_fee_accepts_zero_taker_fee(zero: object) -> None:
    assert parse_rapira_fee(fee_payload(zero)) == Decimal(0)


def test_parse_rapira_fee_returns_taker_not_maker_fee() -> None:
    payload = fee_payload("0.001")
    record = cast(list[dict[str, object]], cast(dict[str, Any], payload["data"])["content"])[0]
    record["makerFee"] = "0.99"

    assert parse_rapira_fee(payload) == Decimal("0.001")


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.pop("code"),
        lambda payload: payload.__setitem__("code", False),
        lambda payload: payload.__setitem__("code", "0"),
        lambda payload: payload.__setitem__("code", 1),
        lambda payload: payload.pop("data"),
        lambda payload: payload.__setitem__("data", None),
        lambda payload: cast(dict[str, object], payload["data"]).pop("content"),
        lambda payload: cast(dict[str, object], payload["data"]).__setitem__("content", []),
        lambda payload: cast(dict[str, object], payload["data"]).__setitem__(
            "content", [fee_payload()["data"], fee_payload()["data"]]
        ),
    ],
)
def test_parse_rapira_fee_rejects_invalid_envelope(mutator: Any) -> None:
    payload = fee_payload()
    mutator(payload)

    with pytest.raises(MarketDataError):
        parse_rapira_fee(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol", "BTC/RUB"),
        ("symbol", None),
        ("level", "1"),
        ("level", False),
        ("level", "0.0"),
        ("takerFee", None),
        ("takerFee", "NaN"),
        ("takerFee", "-0.001"),
        ("takerFee", "1"),
    ],
)
def test_parse_rapira_fee_rejects_wrong_record(field: str, value: object) -> None:
    payload = fee_payload()
    content = cast(list[dict[str, object]], cast(dict[str, Any], payload["data"])["content"])
    content[0][field] = value

    with pytest.raises(MarketDataError):
        parse_rapira_fee(payload)


def test_parse_rapira_fee_rejects_missing_taker_fee() -> None:
    payload = fee_payload()
    content = cast(list[dict[str, object]], cast(dict[str, Any], payload["data"])["content"])
    del content[0]["takerFee"]

    with pytest.raises(MarketDataError):
        parse_rapira_fee(payload)


class FakeResponse:
    def __init__(
        self,
        payload: object,
        *,
        status: int = 200,
        content_type: str = "application/json; charset=utf-8",
        json_error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.json_error = json_error
        self.json_calls = 0
        self.released = False

    async def json(self) -> object:
        self.json_calls += 1
        if self.json_error is not None:
            raise self.json_error
        return self.payload

    def release(self) -> None:
        self.released = True


class FakeSession:
    def __init__(
        self,
        routes: dict[tuple[str, str], FakeResponse | Exception],
        *,
        concurrency_target: int | None = None,
    ) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict[str, object]]] = []
        self.concurrency_target = concurrency_target
        self.started = 0
        self.all_started = asyncio.Event()

    async def _send(self, method: str, url: str, kwargs: dict[str, object]) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        self.started += 1
        if self.concurrency_target is not None:
            if self.started == self.concurrency_target:
                self.all_started.set()
            await self.all_started.wait()

        response = self.routes[(method, url)]
        if isinstance(response, Exception):
            raise response
        return response

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        return await self._send("GET", url, kwargs)

    async def post(self, url: str, **kwargs: object) -> FakeResponse:
        return await self._send("POST", url, kwargs)


def collector_for(session: FakeSession) -> RateCollector:
    return RateCollector(
        cast(aiohttp.ClientSession, session),
        altyn_arbitrage_token=_ALTYN_TOKEN,
    )


@pytest.mark.asyncio
async def test_rate_collector_fetches_all_sources_concurrently_with_exact_requests() -> None:
    altyn_response = FakeResponse(altyn_payload(as_of=datetime.now(UTC)))
    depth_response = FakeResponse(depth_payload())
    fee_response = FakeResponse(fee_payload())
    session = FakeSession(
        {
            ("GET", ALTYN_ARBITRAGE_RATE_URL): altyn_response,
            ("POST", RAPIRA_DEPTH_URL): depth_response,
            ("POST", RAPIRA_FEE_URL): fee_response,
        },
        concurrency_target=3,
    )

    snapshot = await asyncio.wait_for(collector_for(session).collect(), timeout=1)

    assert snapshot.altyn.amount_rub == ALTYN_REFERENCE_AMOUNT_RUB
    assert snapshot.altyn.rate == Decimal("79.88")
    assert snapshot.altyn.network_fee_usdt == Decimal("3.00")
    assert snapshot.rapira.exchange is Exchange.RAPIRA
    assert snapshot.rapira.bid == Decimal("80.01")
    assert snapshot.rapira.ask == Decimal("80.02")
    assert snapshot.rapira.buy_fee_rate == Decimal("0.001")
    assert snapshot.rapira.sell_fee_rate == Decimal("0.001")
    assert snapshot.rapira.buy_fee_mode is BuyFeeMode.DEDUCTED_FROM_BASE
    assert snapshot.fetched_at.tzinfo is UTC
    assert snapshot.fetched_at <= snapshot.altyn.as_of

    calls = {(method, url): kwargs for method, url, kwargs in session.calls}
    assert calls[("POST", RAPIRA_DEPTH_URL)]["data"] == {"symbol": RAPIRA_SYMBOL}
    assert calls[("POST", RAPIRA_FEE_URL)]["data"] == {
        "startLevel": "0",
        "endLevel": "0",
        "symbol": RAPIRA_SYMBOL,
    }
    assert calls[("POST", RAPIRA_DEPTH_URL)]["headers"] == {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    assert calls[("GET", ALTYN_ARBITRAGE_RATE_URL)]["headers"] == {
        "Accept": "application/json",
        "X-Arbitrage-Token": _ALTYN_TOKEN,
    }
    assert calls[("GET", ALTYN_ARBITRAGE_RATE_URL)]["params"] == {"amount_rub": "1000000.00"}
    assert calls[("GET", ALTYN_ARBITRAGE_RATE_URL)]["allow_redirects"] is False
    assert _ALTYN_TOKEN not in ALTYN_ARBITRAGE_RATE_URL
    for kwargs in calls.values():
        timeout = cast(aiohttp.ClientTimeout, kwargs["timeout"])
        assert timeout.total == HTTP_TIMEOUT_SECONDS
    assert altyn_response.released
    assert depth_response.released
    assert fee_response.released


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        (FakeResponse(altyn_payload(), status=503), "http_status"),
        (FakeResponse(altyn_payload(), content_type="text/html"), "invalid_content_type"),
        (FakeResponse(altyn_payload(), json_error=ValueError("bad JSON")), "invalid_json"),
    ],
)
async def test_rate_collector_rejects_bad_http_response(
    response: FakeResponse,
    expected_code: str,
) -> None:
    session = FakeSession({("GET", ALTYN_ARBITRAGE_RATE_URL): response})

    with pytest.raises(MarketDataError) as exc_info:
        await collector_for(session).fetch_altyn_quote(ALTYN_REFERENCE_AMOUNT_RUB)

    assert exc_info.value.service == "altyn"
    assert exc_info.value.code == expected_code
    assert response.released


@pytest.mark.asyncio
async def test_rate_collector_wraps_transport_error_without_retry() -> None:
    session = FakeSession(
        {("GET", ALTYN_ARBITRAGE_RATE_URL): aiohttp.ClientConnectionError("network down")}
    )

    with pytest.raises(MarketDataError) as exc_info:
        await collector_for(session).fetch_altyn_quote(ALTYN_REFERENCE_AMOUNT_RUB)

    assert exc_info.value.service == "altyn"
    assert exc_info.value.code == "request_failed"
    assert len(session.calls) == 1
    assert _ALTYN_TOKEN not in str(exc_info.value)


@pytest.mark.asyncio
async def test_rate_collector_caches_same_amount_for_provider_cache_window() -> None:
    response = FakeResponse(altyn_payload(as_of=datetime.now(UTC)))
    session = FakeSession({("GET", ALTYN_ARBITRAGE_RATE_URL): response})
    collector = collector_for(session)

    first = await collector.fetch_altyn_quote(ALTYN_REFERENCE_AMOUNT_RUB)
    second = await collector.fetch_altyn_quote(Decimal("1000000"))

    assert second is first
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_rate_collector_limits_uncached_altyn_requests_without_retry() -> None:
    response = FakeResponse(altyn_payload(as_of=datetime.now(UTC)))
    session = FakeSession({("GET", ALTYN_ARBITRAGE_RATE_URL): response})
    collector = collector_for(session)
    await collector.fetch_altyn_quote(ALTYN_REFERENCE_AMOUNT_RUB)

    with pytest.raises(MarketDataError) as exc_info:
        await collector.fetch_altyn_quote(Decimal("2000000"))

    assert exc_info.value.code == "client_rate_limit"
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_scheduled_altyn_request_waits_for_local_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_payload = altyn_payload(as_of=datetime.now(UTC))
    first_payload["amount_rub"] = "2000000.00"
    route = ("GET", ALTYN_ARBITRAGE_RATE_URL)
    session = FakeSession({route: FakeResponse(first_payload)})
    collector = collector_for(session)
    await collector.fetch_altyn_quote(Decimal("2000000"))

    session.routes[route] = FakeResponse(altyn_payload(as_of=datetime.now(UTC)))
    sleep = AsyncMock()
    monkeypatch.setattr(rates_module.asyncio, "sleep", sleep)
    quote = await collector.fetch_altyn_quote(
        ALTYN_REFERENCE_AMOUNT_RUB,
        wait_for_slot=True,
    )

    assert quote.amount_rub == ALTYN_REFERENCE_AMOUNT_RUB
    sleep.assert_awaited_once()
    delay = sleep.await_args.args[0]
    assert 0 < delay <= 10
    assert len(session.calls) == 2
