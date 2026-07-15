from __future__ import annotations


class ArbitrageBotError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(ArbitrageBotError):
    """Required configuration is missing or invalid."""


class MarketDataError(ArbitrageBotError):
    """Market data cannot be fetched or validated safely."""

    def __init__(self, service: str, code: str, detail: str) -> None:
        super().__init__(f"{service}:{code}: {detail}")
        self.service = service
        self.code = code


class RatesUnavailableError(ArbitrageBotError):
    """No sufficiently fresh complete rate snapshot exists."""


class InvalidAmountError(ArbitrageBotError):
    """A requested RUB amount has an invalid format or range."""


class InsufficientAmountError(ArbitrageBotError):
    """A RUB amount cannot cover the fixed network fee."""
