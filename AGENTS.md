# Project instructions

- The runtime is Python 3.11+ with aiogram long polling, aiohttp and SQLite. Run checks with `.venv/Scripts/python -m pytest`, `.venv/Scripts/ruff check .`, `.venv/Scripts/ruff format --check .` and `.venv/Scripts/mypy src` on Windows; use the corresponding `.venv/bin/*` paths on Linux.
- Keep every price and fee calculation in `Decimal`. Altyn `USDT -> RUB` is its bid; its ask is the reciprocal of `RUB -> USDT`. Rapira bid/ask come from the public order book, while the taker fee comes from `market/fee/page-query`; do not replace any missing field with zero or a last-trade price.
- SQLite schema changes require an explicit `PRAGMA user_version` migration. Never silently recreate or reinterpret an existing production database.
- Production runs as the `arbitrage-bot` systemd user. Code is under `/opt/arbitrage-altyn-bot`, mutable data under `/var/lib/arbitrage-altyn-bot`, and runtime secrets under `/etc/arbitrage-altyn-bot/bot.env`.
