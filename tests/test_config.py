from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from arbitrage_bot.config import Settings
from arbitrage_bot.errors import ConfigurationError

_VALID_ENV = {
    "TELEGRAM_BOT_TOKEN": "123456:test-token-value",
    "DATABASE_PATH": "data/bot.sqlite3",
    "SUPPORT_URL": "https://t.me/example_support",
    "ALTYN_BUY_FEE_PERCENT": "0.00",
    "ALTYN_SELL_FEE_PERCENT": "0.00",
}


def _set_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _VALID_ENV.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("missing_name", _VALID_ENV)
def test_settings_requires_each_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    missing_name: str,
) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv(missing_name, "   ")

    with pytest.raises(ConfigurationError) as exc_info:
        Settings.from_environment(tmp_path / "does-not-exist.env")

    message = str(exc_info.value)
    assert missing_name in message
    assert _VALID_ENV["TELEGRAM_BOT_TOKEN"] not in message


def test_settings_loads_values_from_explicit_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    for name in _VALID_ENV:
        monkeypatch.delenv(name, raising=False)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "TELEGRAM_BOT_TOKEN=dotenv-test-token\n"
        "DATABASE_PATH=~/arbitrage-test.sqlite3\n"
        "SUPPORT_URL=https://t.me/dotenv_support\n"
        "ALTYN_BUY_FEE_PERCENT=0.25\n"
        "ALTYN_SELL_FEE_PERCENT=0.04\n",
        encoding="utf-8",
    )

    settings = Settings.from_environment(dotenv_path)

    assert settings.telegram_bot_token == "dotenv-test-token"
    assert settings.database_path == Path("~/arbitrage-test.sqlite3").expanduser()
    assert settings.support_url == "https://t.me/dotenv_support"
    assert settings.altyn_buy_fee_rate == Decimal("0.0025")
    assert settings.altyn_sell_fee_rate == Decimal("0.0004")


def test_environment_takes_precedence_over_dotenv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _set_valid_environment(monkeypatch)
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "TELEGRAM_BOT_TOKEN=ignored-dotenv-token\n"
        "DATABASE_PATH=ignored.sqlite3\n"
        "SUPPORT_URL=https://t.me/ignored_support\n"
        "ALTYN_BUY_FEE_PERCENT=50\n"
        "ALTYN_SELL_FEE_PERCENT=50\n",
        encoding="utf-8",
    )

    settings = Settings.from_environment(dotenv_path)

    assert settings.telegram_bot_token == _VALID_ENV["TELEGRAM_BOT_TOKEN"]
    assert settings.database_path == Path(_VALID_ENV["DATABASE_PATH"])
    assert settings.support_url == _VALID_ENV["SUPPORT_URL"]
    assert settings.altyn_buy_fee_rate == Decimal(_VALID_ENV["ALTYN_BUY_FEE_PERCENT"]) / 100
    assert settings.altyn_sell_fee_rate == Decimal(_VALID_ENV["ALTYN_SELL_FEE_PERCENT"]) / 100


@pytest.mark.parametrize("name", ["ALTYN_BUY_FEE_PERCENT", "ALTYN_SELL_FEE_PERCENT"])
@pytest.mark.parametrize("value", ["invalid", "NaN", "Infinity", "-0.001", "100", "100.001"])
def test_settings_rejects_invalid_altyn_fee_percents_without_leaking_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    name: str,
    value: str,
) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigurationError) as exc_info:
        Settings.from_environment(tmp_path / "does-not-exist.env")

    assert str(exc_info.value) == f"{name} must be a finite decimal percent in [0, 100)"
    assert _VALID_ENV["TELEGRAM_BOT_TOKEN"] not in str(exc_info.value)


@pytest.mark.parametrize(
    "support_url",
    [
        "http://t.me/example_support",
        "https://example.com/example_support",
        "https://t.me",
        "https://t.me/",
        "not-a-url",
    ],
)
def test_settings_rejects_invalid_support_url_without_leaking_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    support_url: str,
) -> None:
    _set_valid_environment(monkeypatch)
    monkeypatch.setenv("SUPPORT_URL", support_url)

    with pytest.raises(ConfigurationError) as exc_info:
        Settings.from_environment(tmp_path / "does-not-exist.env")

    message = str(exc_info.value)
    assert message == "SUPPORT_URL must be an https://t.me/<username> URL"
    assert _VALID_ENV["TELEGRAM_BOT_TOKEN"] not in message
