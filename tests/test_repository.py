from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from arbitrage_bot.constants import RATE_MAX_AGE_SECONDS
from arbitrage_bot.domain import (
    AltynBuyQuote,
    BuyFeeMode,
    Exchange,
    ExchangeQuote,
    RateSnapshot,
)
from arbitrage_bot.errors import RatesUnavailableError
from arbitrage_bot.repository import SQLiteRepository

_VERSION_ONE_SCHEMA_SQL = """
CREATE TABLE rate_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    altyn_bid TEXT NOT NULL,
    altyn_ask TEXT NOT NULL,
    altyn_buy_fee_rate TEXT NOT NULL,
    altyn_sell_fee_rate TEXT NOT NULL,
    altyn_buy_fee_mode TEXT NOT NULL,
    rapira_bid TEXT NOT NULL,
    rapira_ask TEXT NOT NULL,
    rapira_buy_fee_rate TEXT NOT NULL,
    rapira_sell_fee_rate TEXT NOT NULL,
    rapira_buy_fee_mode TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE INDEX rate_snapshots_fetched_at_idx
ON rate_snapshots (fetched_at DESC, id DESC);
CREATE TABLE rate_refresh_state (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    status TEXT NOT NULL CHECK (status IN ('success', 'failure')),
    attempted_at TEXT NOT NULL,
    snapshot_id INTEGER,
    service TEXT,
    error_code TEXT,
    FOREIGN KEY (snapshot_id) REFERENCES rate_snapshots (id),
    CHECK (
        (status = 'success' AND snapshot_id IS NOT NULL
            AND service IS NULL AND error_code IS NULL)
        OR
        (status = 'failure' AND snapshot_id IS NULL
            AND service IS NOT NULL AND error_code IS NOT NULL)
    )
);
CREATE TABLE users (
    chat_id INTEGER PRIMARY KEY,
    subscribed INTEGER NOT NULL CHECK (subscribed IN (0, 1)),
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE morning_broadcasts (
    run_date TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE TABLE morning_deliveries (
    run_date TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'sent', 'skipped')),
    finished_at TEXT,
    PRIMARY KEY (run_date, chat_id),
    FOREIGN KEY (run_date) REFERENCES morning_broadcasts (run_date),
    FOREIGN KEY (chat_id) REFERENCES users (chat_id),
    CHECK (
        (state = 'pending' AND finished_at IS NULL)
        OR
        (state IN ('sent', 'skipped') AND finished_at IS NOT NULL)
    )
);
PRAGMA user_version = 1;
"""


def create_version_one_database(database_path: Path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(_VERSION_ONE_SCHEMA_SQL)


@pytest.fixture
async def repository(tmp_path: Path):
    instance = SQLiteRepository(tmp_path / "bot.sqlite3")
    await instance.connect()
    await instance.initialize()
    try:
        yield instance
    finally:
        await instance.close()


def make_snapshot(fetched_at: datetime) -> RateSnapshot:
    return RateSnapshot(
        altyn=AltynBuyQuote(
            amount_rub=Decimal("1000000.00"),
            rate=Decimal("77.250000"),
            network_fee_usdt=Decimal("1.5000"),
            indicative=False,
            as_of=fetched_at - timedelta(seconds=1),
        ),
        rapira=ExchangeQuote(
            exchange=Exchange.RAPIRA,
            bid=Decimal("80.02000001"),
            ask=Decimal("80.03000009"),
            buy_fee_rate=Decimal("0.001"),
            sell_fee_rate=Decimal("0.0010"),
            buy_fee_mode=BuyFeeMode.DEDUCTED_FROM_BASE,
        ),
        fetched_at=fetched_at,
    )


async def test_snapshot_round_trip_preserves_decimal_values_and_models(
    repository: SQLiteRepository,
) -> None:
    fetched_at = datetime(2026, 7, 13, 9, 0, 1, 123456, tzinfo=timezone(timedelta(hours=3)))
    snapshot = make_snapshot(fetched_at)

    await repository.save_snapshot(snapshot)
    restored = await repository.latest_snapshot(
        max_age_seconds=60,
        now=fetched_at + timedelta(seconds=30),
    )

    assert restored == snapshot
    assert restored.altyn.amount_rub.as_tuple() == snapshot.altyn.amount_rub.as_tuple()
    assert restored.altyn.rate.as_tuple() == snapshot.altyn.rate.as_tuple()
    assert restored.altyn.network_fee_usdt.as_tuple() == snapshot.altyn.network_fee_usdt.as_tuple()
    assert restored.altyn.indicative is False
    assert restored.altyn.as_of == snapshot.altyn.as_of
    assert restored.rapira.ask.as_tuple() == snapshot.rapira.ask.as_tuple()
    assert restored.rapira.sell_fee_rate.as_tuple() == snapshot.rapira.sell_fee_rate.as_tuple()


async def test_latest_snapshot_rejects_no_data_and_stale_data(
    repository: SQLiteRepository,
) -> None:
    now = datetime(2026, 7, 13, 6, 0, tzinfo=UTC)

    with pytest.raises(RatesUnavailableError, match="not been refreshed"):
        await repository.latest_snapshot(max_age_seconds=180, now=now)

    await repository.save_snapshot(make_snapshot(now - timedelta(seconds=181)))

    with pytest.raises(RatesUnavailableError, match="stale"):
        await repository.latest_snapshot(max_age_seconds=180, now=now)


async def test_product_freshness_window_is_inclusive(
    repository: SQLiteRepository,
) -> None:
    now = datetime(2026, 7, 13, 6, 0, tzinfo=UTC)
    snapshot = make_snapshot(now - timedelta(seconds=RATE_MAX_AGE_SECONDS))
    await repository.save_snapshot(snapshot)

    assert await repository.latest_snapshot(RATE_MAX_AGE_SECONDS, now=now) == snapshot
    with pytest.raises(RatesUnavailableError, match="stale"):
        await repository.latest_snapshot(
            RATE_MAX_AGE_SECONDS,
            now=now + timedelta(microseconds=1),
        )


async def test_latest_snapshot_rejects_materially_future_data(
    repository: SQLiteRepository,
) -> None:
    now = datetime(2026, 7, 13, 6, 0, tzinfo=UTC)
    await repository.save_snapshot(make_snapshot(now + timedelta(seconds=6)))

    with pytest.raises(RatesUnavailableError, match="future"):
        await repository.latest_snapshot(max_age_seconds=180, now=now)


async def test_subscription_default_and_transitions(repository: SQLiteRepository) -> None:
    assert await repository.is_subscribed(1001) is False
    assert await repository.ensure_user(1001) is True
    assert await repository.ensure_user(1001) is True
    assert await repository.list_subscribers() == [1001]

    assert await repository.set_subscription(1001, False) is True
    assert await repository.set_subscription(1001, False) is False
    assert await repository.ensure_user(1001) is False
    assert await repository.is_subscribed(1001) is False
    assert await repository.list_subscribers() == []

    assert await repository.set_subscription(1001, True) is True
    assert await repository.set_subscription(1001, True) is False
    assert await repository.is_subscribed(1001) is True


async def test_set_subscription_creates_user_with_requested_state(
    repository: SQLiteRepository,
) -> None:
    assert await repository.set_subscription(2002, False) is True
    assert await repository.ensure_user(2002) is False
    assert await repository.set_subscription(3003, True) is True
    assert await repository.list_subscribers() == [3003]


async def test_refresh_failure_invalidates_fresh_snapshot_until_success(
    repository: SQLiteRepository,
) -> None:
    now = datetime(2026, 7, 13, 6, 0, tzinfo=UTC)
    await repository.save_snapshot(make_snapshot(now))
    assert await repository.latest_snapshot(180, now=now) == make_snapshot(now)

    await repository.record_refresh_failure("rapira", "request_failed", now + timedelta(seconds=1))
    with pytest.raises(RatesUnavailableError, match="latest rate refresh failed"):
        await repository.latest_snapshot(180, now=now + timedelta(seconds=1))

    recovered = make_snapshot(now + timedelta(seconds=2))
    await repository.save_snapshot(recovered)
    assert await repository.latest_snapshot(180, now=now + timedelta(seconds=2)) == recovered


async def test_morning_broadcast_outbox_survives_partial_delivery(
    repository: SQLiteRepository,
) -> None:
    run_date = date(2026, 7, 13)
    await repository.ensure_user(1001)
    await repository.ensure_user(1002)
    await repository.set_subscription(1003, False)

    batches = await asyncio.gather(
        *(repository.prepare_morning_broadcast(run_date, "original") for _ in range(10))
    )
    assert all(batch.message == "original" for batch in batches)
    assert all(batch.pending_chat_ids == (1001, 1002) for batch in batches)

    assert await repository.finish_morning_delivery(run_date, 1001, "sent") is True
    resumed = await repository.prepare_morning_broadcast(run_date, "new text is ignored")
    assert resumed.message == "original"
    assert resumed.pending_chat_ids == (1002,)
    assert resumed.complete is False

    assert await repository.finish_morning_delivery(run_date, 1002, "skipped") is True
    assert await repository.finish_morning_delivery(run_date, 1002, "skipped") is False
    assert await repository.is_morning_broadcast_complete(run_date) is True
    completed = await repository.prepare_morning_broadcast(run_date, "other")
    assert completed.pending_chat_ids == ()
    assert completed.complete is True


async def test_morning_broadcast_without_subscribers_completes(
    repository: SQLiteRepository,
) -> None:
    run_date = date(2026, 7, 14)

    batch = await repository.prepare_morning_broadcast(run_date, "message")

    assert batch.pending_chat_ids == ()
    assert batch.complete is True
    assert await repository.is_morning_broadcast_complete(run_date) is True


async def test_get_morning_broadcast_returns_none_when_absent(
    repository: SQLiteRepository,
) -> None:
    assert await repository.get_morning_broadcast(date(2026, 7, 15)) is None


async def test_get_morning_broadcast_returns_current_outbox_state(
    repository: SQLiteRepository,
) -> None:
    run_date = date(2026, 7, 15)
    await repository.ensure_user(1001)
    await repository.ensure_user(1002)
    prepared = await repository.prepare_morning_broadcast(run_date, "stored message")

    assert await repository.get_morning_broadcast(run_date) == prepared

    await repository.finish_morning_delivery(run_date, 1001, "sent")
    resumed = await repository.get_morning_broadcast(run_date)
    assert resumed is not None
    assert resumed.message == "stored message"
    assert resumed.pending_chat_ids == (1002,)
    assert resumed.complete is False

    await repository.finish_morning_delivery(run_date, 1002, "sent")
    completed = await repository.get_morning_broadcast(run_date)
    assert completed is not None
    assert completed.pending_chat_ids == ()
    assert completed.complete is True


async def test_initialize_migrates_version_one_without_reinterpreting_old_rates(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "version-one.sqlite3"
    create_version_one_database(database_path)
    old_snapshots = [
        (
            7,
            "77.250000",
            "80.456789123456789",
            "0.0150",
            "0",
            "added_to_quote",
            "80.02000001",
            "80.03000009",
            "0.001",
            "0.0010",
            "deducted_from_base",
            "2026-07-13T06:00:00.000000+00:00",
        ),
        (
            9,
            "78.100",
            "79.900",
            "0",
            "0",
            "added_to_quote",
            "80.100",
            "80.200",
            "0.001",
            "0.001",
            "deducted_from_base",
            "2026-07-13T06:01:00.000000+00:00",
        ),
    ]
    archived_refresh_state = (
        1,
        "success",
        "2026-07-13T06:01:00.000000+00:00",
        9,
        None,
        None,
    )
    with sqlite3.connect(database_path) as connection:
        connection.executemany(
            "INSERT INTO rate_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            old_snapshots,
        )
        connection.execute(
            "INSERT INTO rate_refresh_state VALUES (?, ?, ?, ?, ?, ?)",
            archived_refresh_state,
        )
        connection.execute(
            """
            INSERT INTO users (chat_id, subscribed, first_seen_at, updated_at)
            VALUES (1001, 1, ?, ?)
            """,
            (
                "2026-07-13T05:00:00.000000+00:00",
                "2026-07-13T05:00:00.000000+00:00",
            ),
        )
        connection.execute(
            """
            INSERT INTO morning_broadcasts (
                run_date, message, created_at, completed_at
            ) VALUES ('2026-07-13', 'stored message', ?, NULL)
            """,
            ("2026-07-13T06:02:00.000000+00:00",),
        )
        connection.execute(
            """
            INSERT INTO morning_deliveries (run_date, chat_id, state, finished_at)
            VALUES ('2026-07-13', 1001, 'pending', NULL)
            """
        )

    migrated = SQLiteRepository(database_path)
    await migrated.connect()
    try:
        await migrated.initialize()
        with pytest.raises(RatesUnavailableError, match="not been refreshed"):
            await migrated.latest_snapshot(180)
        assert await migrated.is_subscribed(1001) is True
        broadcast = await migrated.get_morning_broadcast(date(2026, 7, 13))
        assert broadcast is not None
        assert broadcast.message == "stored message"
        assert broadcast.pending_chat_ids == (1001,)
    finally:
        await migrated.close()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        assert (
            connection.execute("SELECT * FROM rate_snapshots_v1_archive ORDER BY id").fetchall()
            == old_snapshots
        )
        assert (
            connection.execute("SELECT * FROM rate_refresh_state_v1_archive").fetchone()
            == archived_refresh_state
        )
        assert connection.execute("SELECT * FROM rate_snapshots").fetchall() == []
        assert connection.execute("SELECT * FROM rate_refresh_state").fetchall() == []

    fetched_at = datetime(2026, 7, 13, 6, 2, tzinfo=UTC)
    reopened = SQLiteRepository(database_path)
    await reopened.connect()
    try:
        await reopened.initialize()
        snapshot = make_snapshot(fetched_at)
        await reopened.save_snapshot(snapshot)
        assert await reopened.latest_snapshot(60, now=fetched_at) == snapshot
    finally:
        await reopened.close()

    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute("SELECT * FROM rate_snapshots_v1_archive ORDER BY id").fetchall()
            == old_snapshots
        )
        assert connection.execute("SELECT COUNT(*) FROM rate_snapshots").fetchone() == (1,)


async def test_version_one_migration_rolls_back_on_invalid_archived_reference(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "invalid-version-one.sqlite3"
    create_version_one_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO rate_refresh_state (
                singleton_id, status, attempted_at, snapshot_id, service, error_code
            ) VALUES (1, 'success', ?, 999, NULL, NULL)
            """,
            ("2026-07-13T06:00:00.000000+00:00",),
        )

    repository = SQLiteRepository(database_path)
    await repository.connect()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            await repository.initialize()
    finally:
        await repository.close()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (1,)
        objects = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
        }
        assert "rate_snapshots_v1_archive" not in objects
        assert "rate_refresh_state_v1_archive" not in objects
        assert connection.execute("SELECT snapshot_id FROM rate_refresh_state").fetchone() == (999,)


async def test_initialize_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    database_path = tmp_path / "future.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 3")

    repository = SQLiteRepository(database_path)
    await repository.connect()
    try:
        with pytest.raises(RuntimeError, match="unsupported database schema version 3"):
            await repository.initialize()
    finally:
        await repository.close()


async def test_initialize_rejects_malformed_version_two_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "malformed.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.connect()
    await repository.initialize()
    await repository.close()

    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP INDEX rate_snapshots_fetched_at_idx")

    reopened = SQLiteRepository(database_path)
    await reopened.connect()
    try:
        with pytest.raises(RuntimeError, match="schema objects are invalid"):
            await reopened.initialize()
    finally:
        await reopened.close()


async def test_initialize_rejects_schema_without_required_check(tmp_path: Path) -> None:
    database_path = tmp_path / "missing-check.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.connect()
    await repository.initialize()
    await repository.close()

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA writable_schema = ON")
        connection.execute(
            """
            UPDATE sqlite_master
            SET sql = replace(sql, ' CHECK (subscribed IN (0, 1))', '')
            WHERE type = 'table' AND name = 'users'
            """
        )
        connection.execute("PRAGMA writable_schema = OFF")
        schema_version = connection.execute("PRAGMA schema_version").fetchone()[0]
        connection.execute(f"PRAGMA schema_version = {schema_version + 1}")

    reopened = SQLiteRepository(database_path)
    await reopened.connect()
    try:
        with pytest.raises(RuntimeError, match="invalid schema definition"):
            await reopened.initialize()
    finally:
        await reopened.close()


async def test_initialize_rejects_wrong_index_direction(tmp_path: Path) -> None:
    database_path = tmp_path / "wrong-index-direction.sqlite3"
    repository = SQLiteRepository(database_path)
    await repository.connect()
    await repository.initialize()
    await repository.close()

    with sqlite3.connect(database_path) as connection:
        connection.execute("DROP INDEX rate_snapshots_fetched_at_idx")
        connection.execute(
            """
            CREATE INDEX rate_snapshots_fetched_at_idx
            ON rate_snapshots (fetched_at ASC, id ASC)
            """
        )

    reopened = SQLiteRepository(database_path)
    await reopened.connect()
    try:
        with pytest.raises(RuntimeError, match="invalid schema definition"):
            await reopened.initialize()
    finally:
        await reopened.close()
