from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

from .errors import ConfigurationError


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"Required environment variable {name} is missing")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    telegram_bot_token: str
    database_path: Path
    support_url: str

    @classmethod
    def from_environment(cls, dotenv_path: Path | None = None) -> Settings:
        load_dotenv(dotenv_path=dotenv_path, override=False)
        token = _required_env("TELEGRAM_BOT_TOKEN")
        database_path = Path(_required_env("DATABASE_PATH")).expanduser()
        support_url = _required_env("SUPPORT_URL")
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
        )
