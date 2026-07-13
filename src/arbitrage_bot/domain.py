from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum


class Exchange(StrEnum):
    ALTYN = "Altyn"
    RAPIRA = "Rapira"


class BuyFeeMode(StrEnum):
    ADDED_TO_QUOTE = "added_to_quote"
    DEDUCTED_FROM_BASE = "deducted_from_base"


def _require_finite_positive(value: Decimal, field: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{field} must be finite and positive")


def _require_fee(value: Decimal, field: str) -> None:
    if not value.is_finite() or value < 0 or value >= 1:
        raise ValueError(f"{field} must be finite and in [0, 1)")


@dataclass(frozen=True, slots=True)
class ExchangeQuote:
    exchange: Exchange
    bid: Decimal
    ask: Decimal
    buy_fee_rate: Decimal
    sell_fee_rate: Decimal
    buy_fee_mode: BuyFeeMode

    def __post_init__(self) -> None:
        if not isinstance(self.exchange, Exchange):
            raise TypeError("exchange must be an Exchange")
        if not isinstance(self.buy_fee_mode, BuyFeeMode):
            raise TypeError("buy_fee_mode must be a BuyFeeMode")
        _require_finite_positive(self.bid, "bid")
        _require_finite_positive(self.ask, "ask")
        if self.bid > self.ask:
            raise ValueError("bid must not exceed ask")
        _require_fee(self.buy_fee_rate, "buy_fee_rate")
        _require_fee(self.sell_fee_rate, "sell_fee_rate")

    @property
    def effective_ask(self) -> Decimal:
        if self.buy_fee_mode is BuyFeeMode.ADDED_TO_QUOTE:
            return self.ask * (Decimal(1) + self.buy_fee_rate)
        return self.ask / (Decimal(1) - self.buy_fee_rate)

    @property
    def effective_bid(self) -> Decimal:
        return self.bid * (Decimal(1) - self.sell_fee_rate)


@dataclass(frozen=True, slots=True)
class RateSnapshot:
    altyn: ExchangeQuote
    rapira: ExchangeQuote
    fetched_at: datetime

    def __post_init__(self) -> None:
        if self.altyn.exchange is not Exchange.ALTYN:
            raise ValueError("altyn quote has the wrong exchange")
        if self.rapira.exchange is not Exchange.RAPIRA:
            raise ValueError("rapira quote has the wrong exchange")
        if not isinstance(self.fetched_at, datetime):
            raise TypeError("fetched_at must be a datetime")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")

    def age_seconds(self, now: datetime | None = None) -> Decimal:
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return Decimal(str((current - self.fetched_at).total_seconds()))


@dataclass(frozen=True, slots=True)
class SpreadResult:
    buy_exchange: Exchange
    sell_exchange: Exchange
    raw_buy: Decimal
    raw_sell: Decimal
    effective_buy: Decimal
    effective_sell: Decimal
    buy_fee_rate: Decimal
    sell_fee_rate: Decimal
    gross_spread_rub: Decimal
    gross_spread_percent: Decimal
    net_spread_rub: Decimal
    net_spread_percent: Decimal


@dataclass(frozen=True, slots=True)
class AmountResult:
    amount_rub: Decimal
    usdt_to_sell: Decimal
    final_rub: Decimal
    profit_rub: Decimal
    profit_percent: Decimal
