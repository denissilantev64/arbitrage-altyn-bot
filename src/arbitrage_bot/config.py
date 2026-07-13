from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from .errors import ConfigurationError


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Required environment variable {name} is missing")
    return value


def _required_fee_percent(name: str) -> Decimal:
    raw_value = _required_env(name)
    try:
        value = Decimal(raw_value)
    except InvalidOperation as exc:
        raise ConfigurationError(f"{name} must be a finite decimal percent in [0, 100)") from exc
    if not value.is_finite() or value < 0 or value >= 100:
        raise ConfigurationError(f"{name} must be a finite decimal percent in [0, 100)")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    database_path: Path
    support_url: str
    altyn_buy_fee_rate: Decimal
    altyn_sell_fee_rate: Decimal

    @classmethod
    def from_environment(cls, dotenv_path: Path | None = None) -> Settings:
        load_dotenv(dotenv_path=dotenv_path, override=False)
        token = _required_env("TELEGRAM_BOT_TOKEN")
        database_path = Path(_required_env("DATABASE_PATH")).expanduser()
        support_url = _required_env("SUPPORT_URL")
        altyn_buy_fee_rate = _required_fee_percent("ALTYN_BUY_FEE_PERCENT") / Decimal(100)
        altyn_sell_fee_rate = _required_fee_percent("ALTYN_SELL_FEE_PERCENT") / Decimal(100)
        parsed_url = urlparse(support_url)
        if (
            parsed_url.scheme != "https"
            or parsed_url.netloc != "t.me"
            or not parsed_url.path.strip("/")
        ):
            raise ConfigurationError("SUPPORT_URL must be an https://t.me/<username> URL")
        return cls(
            telegram_bot_token=token,
            database_path=database_path,
            support_url=support_url,
            altyn_buy_fee_rate=altyn_buy_fee_rate,
            altyn_sell_fee_rate=altyn_sell_fee_rate,
        )
