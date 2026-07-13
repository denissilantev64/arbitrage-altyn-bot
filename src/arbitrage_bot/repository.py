from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Final, Literal

import aiosqlite

from .domain import BuyFeeMode, Exchange, ExchangeQuote, RateSnapshot
from .errors import RatesUnavailableError

_SCHEMA_VERSION: Final = 1
_BUSY_TIMEOUT_MS: Final = 5_000
_MAX_FUTURE_SKEW_SECONDS: Final = Decimal("5")


@dataclass(frozen=True, slots=True)
class _SchemaDefinition:
    object_type: Literal["table", "index"]
    name: str
    table_name: str
    sql: str


_SCHEMA_DEFINITIONS: Final[tuple[_SchemaDefinition, ...]] = (
    _SchemaDefinition(
        object_type="table",
        name="rate_snapshots",
        table_name="rate_snapshots",
        sql="""
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
            )
        """,
    ),
    _SchemaDefinition(
        object_type="index",
        name="rate_snapshots_fetched_at_idx",
        table_name="rate_snapshots",
        sql="""
            CREATE INDEX rate_snapshots_fetched_at_idx
            ON rate_snapshots (fetched_at DESC, id DESC)
        """,
    ),
    _SchemaDefinition(
        object_type="table",
        name="rate_refresh_state",
        table_name="rate_refresh_state",
        sql="""
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
            )
        """,
    ),
    _SchemaDefinition(
        object_type="table",
        name="users",
        table_name="users",
        sql="""
            CREATE TABLE users (
                chat_id INTEGER PRIMARY KEY,
                subscribed INTEGER NOT NULL CHECK (subscribed IN (0, 1)),
                first_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """,
    ),
    _SchemaDefinition(
        object_type="table",
        name="morning_broadcasts",
        table_name="morning_broadcasts",
        sql="""
            CREATE TABLE morning_broadcasts (
                run_date TEXT PRIMARY KEY,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """,
    ),
    _SchemaDefinition(
        object_type="table",
        name="morning_deliveries",
        table_name="morning_deliveries",
        sql="""
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
            )
        """,
    ),
)


@dataclass(frozen=True, slots=True)
class MorningBroadcastBatch:
    message: str
    pending_chat_ids: tuple[int, ...]
    complete: bool


class SQLiteRepository:
    """Persistent storage for market snapshots and Telegram subscriptions."""

    def __init__(self, database_path: str | Path) -> None:
        self._database_path = str(database_path)
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the database and apply connection-level safety settings."""
        async with self._lock:
            if self._connection is not None:
                return

            connection = await aiosqlite.connect(
                self._database_path,
                isolation_level=None,
            )
            try:
                await connection.execute(f"PRAGMA busy_timeout = {_BUSY_TIMEOUT_MS}")
                await connection.execute("PRAGMA foreign_keys = ON")
                await connection.execute("PRAGMA journal_mode = WAL")
            except BaseException:
                await connection.close()
                raise
            self._connection = connection

    async def initialize(self) -> None:
        """Create a new version-1 schema or validate an existing one."""
        async with self._lock:
            connection = self._require_connection()
            version = await self._read_schema_version(connection)

            if version == _SCHEMA_VERSION:
                await self._validate_schema(connection)
                return
            if version != 0:
                raise RuntimeError(
                    f"unsupported database schema version {version}; expected {_SCHEMA_VERSION}"
                )

            existing_objects = await self._user_schema_object_names(connection)
            if existing_objects:
                names = ", ".join(sorted(existing_objects))
                raise RuntimeError(
                    "database has an unversioned schema; refusing to modify "
                    f"existing objects: {names}"
                )

            await connection.execute("BEGIN IMMEDIATE")
            try:
                for definition in _SCHEMA_DEFINITIONS:
                    await connection.execute(definition.sql)
                await connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

            await self._validate_schema(connection)

    async def close(self) -> None:
        """Close the database connection. Calling this twice is safe."""
        async with self._lock:
            if self._connection is None:
                return
            connection = self._connection
            self._connection = None
            await connection.close()

    async def __aenter__(self) -> SQLiteRepository:
        await self.connect()
        await self.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc_value: object,
        traceback: object,
    ) -> None:
        await self.close()

    async def save_snapshot(self, snapshot: RateSnapshot) -> None:
        values = (
            str(snapshot.altyn.bid),
            str(snapshot.altyn.ask),
            str(snapshot.altyn.buy_fee_rate),
            str(snapshot.altyn.sell_fee_rate),
            snapshot.altyn.buy_fee_mode.value,
            str(snapshot.rapira.bid),
            str(snapshot.rapira.ask),
            str(snapshot.rapira.buy_fee_rate),
            str(snapshot.rapira.sell_fee_rate),
            snapshot.rapira.buy_fee_mode.value,
            _datetime_to_storage(snapshot.fetched_at),
        )

        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await connection.execute(
                    """
                    INSERT INTO rate_snapshots (
                        altyn_bid,
                        altyn_ask,
                        altyn_buy_fee_rate,
                        altyn_sell_fee_rate,
                        altyn_buy_fee_mode,
                        rapira_bid,
                        rapira_ask,
                        rapira_buy_fee_rate,
                        rapira_sell_fee_rate,
                        rapira_buy_fee_mode,
                        fetched_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                try:
                    snapshot_id = cursor.lastrowid
                finally:
                    await cursor.close()
                if not isinstance(snapshot_id, int) or isinstance(snapshot_id, bool):
                    raise RuntimeError("database did not return a snapshot id")
                await connection.execute(
                    """
                    INSERT INTO rate_refresh_state (
                        singleton_id, status, attempted_at, snapshot_id, service, error_code
                    ) VALUES (1, 'success', ?, ?, NULL, NULL)
                    ON CONFLICT(singleton_id) DO UPDATE SET
                        status = excluded.status,
                        attempted_at = excluded.attempted_at,
                        snapshot_id = excluded.snapshot_id,
                        service = NULL,
                        error_code = NULL
                    """,
                    (values[-1], snapshot_id),
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def record_refresh_failure(
        self,
        service: str,
        error_code: str,
        attempted_at: datetime | None = None,
    ) -> None:
        service_value = _validate_status_text(service, "service")
        error_code_value = _validate_status_text(error_code, "error_code")
        attempt = attempted_at or datetime.now(UTC)
        attempted_at_text = _datetime_to_storage(attempt)

        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute(
                    """
                    INSERT INTO rate_refresh_state (
                        singleton_id, status, attempted_at, snapshot_id, service, error_code
                    ) VALUES (1, 'failure', ?, NULL, ?, ?)
                    ON CONFLICT(singleton_id) DO UPDATE SET
                        status = excluded.status,
                        attempted_at = excluded.attempted_at,
                        snapshot_id = NULL,
                        service = excluded.service,
                        error_code = excluded.error_code
                    """,
                    (attempted_at_text, service_value, error_code_value),
                )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def latest_snapshot(
        self,
        max_age_seconds: int | float | Decimal,
        now: datetime | None = None,
    ) -> RateSnapshot:
        max_age = _coerce_max_age(max_age_seconds)
        current = now or datetime.now(UTC)
        _require_aware_datetime(current, "now")

        async with self._lock:
            connection = self._require_connection()
            state_cursor = await connection.execute(
                """
                SELECT status, attempted_at, snapshot_id, service, error_code
                FROM rate_refresh_state
                WHERE singleton_id = 1
                """
            )
            try:
                state_row = await state_cursor.fetchone()
            finally:
                await state_cursor.close()

            if state_row is None:
                raise RatesUnavailableError("rates have not been refreshed yet")
            status, attempted_at, snapshot_id = _validate_refresh_state(state_row)
            if status == "failure":
                raise RatesUnavailableError("the latest rate refresh failed")

            cursor = await connection.execute(
                """
                SELECT
                    altyn_bid,
                    altyn_ask,
                    altyn_buy_fee_rate,
                    altyn_sell_fee_rate,
                    altyn_buy_fee_mode,
                    rapira_bid,
                    rapira_ask,
                    rapira_buy_fee_rate,
                    rapira_sell_fee_rate,
                    rapira_buy_fee_mode,
                    fetched_at
                FROM rate_snapshots
                WHERE id = ? AND id = (SELECT MAX(id) FROM rate_snapshots)
                """,
                (snapshot_id,),
            )
            try:
                row = await cursor.fetchone()
            finally:
                await cursor.close()

        if row is None:
            raise RuntimeError("rate refresh state does not reference the latest snapshot")

        snapshot = _snapshot_from_row(row)
        if snapshot.fetched_at != attempted_at:
            raise RuntimeError("rate refresh state timestamp does not match its snapshot")
        age = snapshot.age_seconds(current)
        if age < -_MAX_FUTURE_SKEW_SECONDS:
            raise RatesUnavailableError("latest rate snapshot is dated in the future")
        if age > max_age:
            raise RatesUnavailableError("latest rate snapshot is stale")
        return snapshot

    async def ensure_user(self, chat_id: int) -> bool:
        """Create a subscribed user if absent and return the current state."""
        _require_chat_id(chat_id)
        timestamp = _datetime_to_storage(datetime.now(UTC))

        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                await connection.execute(
                    """
                    INSERT INTO users (chat_id, subscribed, first_seen_at, updated_at)
                    VALUES (?, 1, ?, ?)
                    ON CONFLICT(chat_id) DO NOTHING
                    """,
                    (chat_id, timestamp, timestamp),
                )
                row = await self._fetch_user(connection, chat_id)
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

        if row is None:
            raise RuntimeError("user was not persisted")
        _, subscribed = _validate_user_row(row)
        return subscribed

    async def set_subscription(self, chat_id: int, subscribed: bool) -> bool:
        """Set subscription state and report whether the stored state changed."""
        _require_chat_id(chat_id)
        if not isinstance(subscribed, bool):
            raise TypeError("subscribed must be a bool")
        timestamp = _datetime_to_storage(datetime.now(UTC))

        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetch_user(connection, chat_id)
                if row is None:
                    await connection.execute(
                        """
                        INSERT INTO users (chat_id, subscribed, first_seen_at, updated_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (chat_id, int(subscribed), timestamp, timestamp),
                    )
                    changed = True
                else:
                    _, current = _validate_user_row(row)
                    changed = current is not subscribed
                    if changed:
                        await connection.execute(
                            """
                            UPDATE users
                            SET subscribed = ?, updated_at = ?
                            WHERE chat_id = ?
                            """,
                            (int(subscribed), timestamp, chat_id),
                        )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

        return changed

    async def is_subscribed(self, chat_id: int) -> bool:
        _require_chat_id(chat_id)
        async with self._lock:
            connection = self._require_connection()
            row = await self._fetch_user(connection, chat_id)

        if row is None:
            return False
        _, subscribed = _validate_user_row(row)
        return subscribed

    async def list_subscribers(self) -> list[int]:
        async with self._lock:
            connection = self._require_connection()
            cursor = await connection.execute(
                """
                SELECT chat_id, subscribed, first_seen_at, updated_at
                FROM users
                ORDER BY chat_id
                """
            )
            try:
                rows = await cursor.fetchall()
            finally:
                await cursor.close()

        subscribers: list[int] = []
        for row in rows:
            chat_id, subscribed = _validate_user_row(row)
            if subscribed:
                subscribers.append(chat_id)
        return subscribers

    async def get_morning_broadcast(
        self,
        run_date: date,
    ) -> MorningBroadcastBatch | None:
        run_key = _run_date_to_storage(run_date)
        async with self._lock:
            connection = self._require_connection()
            row = await self._fetch_broadcast(connection, run_key)
            if row is None:
                return None
            message, complete = _validate_broadcast_row(row, run_key)
            pending = await self._fetch_pending_deliveries(connection, run_key)
            return _morning_broadcast_batch(message, pending, complete)

    async def prepare_morning_broadcast(
        self,
        run_date: date,
        message: str,
    ) -> MorningBroadcastBatch:
        run_key = _run_date_to_storage(run_date)
        message_value = _validate_message(message)
        created_at = _datetime_to_storage(datetime.now(UTC))

        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                row = await self._fetch_broadcast(connection, run_key)
                if row is None:
                    await connection.execute(
                        """
                        INSERT INTO morning_broadcasts (
                            run_date, message, created_at, completed_at
                        ) VALUES (?, ?, ?, NULL)
                        """,
                        (run_key, message_value, created_at),
                    )
                    await connection.execute(
                        """
                        INSERT INTO morning_deliveries (
                            run_date, chat_id, state, finished_at
                        )
                        SELECT ?, chat_id, 'pending', NULL
                        FROM users
                        WHERE subscribed = 1
                        """,
                        (run_key,),
                    )
                    row = await self._fetch_broadcast(connection, run_key)

                if row is None:
                    raise RuntimeError("morning broadcast was not persisted")
                stored_message, complete = _validate_broadcast_row(row, run_key)
                pending = await self._fetch_pending_deliveries(connection, run_key)
                if not pending and not complete:
                    await connection.execute(
                        """
                        UPDATE morning_broadcasts
                        SET completed_at = ?
                        WHERE run_date = ? AND completed_at IS NULL
                        """,
                        (created_at, run_key),
                    )
                    complete = True
                batch = _morning_broadcast_batch(stored_message, pending, complete)
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

        return batch

    async def finish_morning_delivery(
        self,
        run_date: date,
        chat_id: int,
        state: Literal["sent", "skipped"],
    ) -> bool:
        run_key = _run_date_to_storage(run_date)
        _require_chat_id(chat_id)
        if state not in ("sent", "skipped"):
            raise ValueError("delivery state must be 'sent' or 'skipped'")
        finished_at = _datetime_to_storage(datetime.now(UTC))

        async with self._lock:
            connection = self._require_connection()
            await connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await connection.execute(
                    """
                    SELECT state, finished_at
                    FROM morning_deliveries
                    WHERE run_date = ? AND chat_id = ?
                    """,
                    (run_key, chat_id),
                )
                try:
                    row = await cursor.fetchone()
                finally:
                    await cursor.close()
                if row is None:
                    raise RuntimeError("morning delivery does not exist")
                current_state = _validate_delivery_row(row)
                changed = current_state == "pending"
                if changed:
                    await connection.execute(
                        """
                        UPDATE morning_deliveries
                        SET state = ?, finished_at = ?
                        WHERE run_date = ? AND chat_id = ? AND state = 'pending'
                        """,
                        (state, finished_at, run_key, chat_id),
                    )

                pending = await self._fetch_pending_deliveries(connection, run_key)
                if not pending:
                    await connection.execute(
                        """
                        UPDATE morning_broadcasts
                        SET completed_at = COALESCE(completed_at, ?)
                        WHERE run_date = ?
                        """,
                        (finished_at, run_key),
                    )
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

        return changed

    async def is_morning_broadcast_complete(self, run_date: date) -> bool:
        run_key = _run_date_to_storage(run_date)
        async with self._lock:
            connection = self._require_connection()
            row = await self._fetch_broadcast(connection, run_key)
            if row is None:
                return False
            message, complete = _validate_broadcast_row(row, run_key)
            pending = await self._fetch_pending_deliveries(connection, run_key)
            batch = _morning_broadcast_batch(message, pending, complete)
        return batch.complete

    def _require_connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("repository is not connected")
        return self._connection

    @staticmethod
    async def _read_schema_version(connection: aiosqlite.Connection) -> int:
        cursor = await connection.execute("PRAGMA user_version")
        try:
            row = await cursor.fetchone()
        finally:
            await cursor.close()
        if row is None or len(row) != 1 or not isinstance(row[0], int):
            raise RuntimeError("database returned an invalid schema version")
        return row[0]

    @staticmethod
    async def _user_schema_object_names(connection: aiosqlite.Connection) -> set[str]:
        cursor = await connection.execute(
            """
            SELECT type, name
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            """
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        names: set[str] = set()
        for row in rows:
            if (
                len(row) != 2
                or not isinstance(row[0], str)
                or not isinstance(row[1], str)
                or not row[0]
                or not row[1]
            ):
                raise RuntimeError("database returned an invalid schema object")
            names.add(f"{row[0]}:{row[1]}")
        return names

    @staticmethod
    async def _validate_schema(connection: aiosqlite.Connection) -> None:
        cursor = await connection.execute(
            """
            SELECT type, name, tbl_name, sql
            FROM sqlite_master
            WHERE name NOT LIKE 'sqlite_%'
            """
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()

        actual: dict[tuple[str, str], tuple[str, str]] = {}
        for row in rows:
            if (
                len(row) != 4
                or not isinstance(row[0], str)
                or not isinstance(row[1], str)
                or not isinstance(row[2], str)
                or not isinstance(row[3], str)
            ):
                raise RuntimeError("database returned an invalid schema object definition")
            key = (row[0], row[1])
            if key in actual:
                raise RuntimeError(f"database returned duplicate schema object {key!r}")
            actual[key] = (row[2], _normalize_schema_sql(row[3]))

        expected = {
            (definition.object_type, definition.name): (
                definition.table_name,
                _normalize_schema_sql(definition.sql),
            )
            for definition in _SCHEMA_DEFINITIONS
        }
        if actual.keys() != expected.keys():
            expected_names = sorted(f"{kind}:{name}" for kind, name in expected)
            actual_names = sorted(f"{kind}:{name}" for kind, name in actual)
            raise RuntimeError(
                "database schema objects are invalid: "
                f"expected {expected_names}, got {actual_names}"
            )

        for key, expected_definition in expected.items():
            if actual[key] != expected_definition:
                kind, name = key
                raise RuntimeError(f"database {kind} {name!r} has an invalid schema definition")

    @staticmethod
    async def _fetch_user(
        connection: aiosqlite.Connection,
        chat_id: int,
    ) -> aiosqlite.Row | tuple[object, ...] | None:
        cursor = await connection.execute(
            """
            SELECT chat_id, subscribed, first_seen_at, updated_at
            FROM users
            WHERE chat_id = ?
            """,
            (chat_id,),
        )
        try:
            return await cursor.fetchone()
        finally:
            await cursor.close()

    @staticmethod
    async def _fetch_broadcast(
        connection: aiosqlite.Connection,
        run_key: str,
    ) -> aiosqlite.Row | tuple[object, ...] | None:
        cursor = await connection.execute(
            """
            SELECT run_date, message, created_at, completed_at
            FROM morning_broadcasts
            WHERE run_date = ?
            """,
            (run_key,),
        )
        try:
            return await cursor.fetchone()
        finally:
            await cursor.close()

    @staticmethod
    async def _fetch_pending_deliveries(
        connection: aiosqlite.Connection,
        run_key: str,
    ) -> list[int]:
        cursor = await connection.execute(
            """
            SELECT chat_id
            FROM morning_deliveries
            WHERE run_date = ? AND state = 'pending'
            ORDER BY chat_id
            """,
            (run_key,),
        )
        try:
            rows = await cursor.fetchall()
        finally:
            await cursor.close()
        result: list[int] = []
        for row in rows:
            if len(row) != 1 or not isinstance(row[0], int) or isinstance(row[0], bool):
                raise RuntimeError("stored morning delivery chat_id is invalid")
            result.append(row[0])
        return result


def _morning_broadcast_batch(
    message: str,
    pending: list[int],
    complete: bool,
) -> MorningBroadcastBatch:
    if complete and pending:
        raise RuntimeError("completed morning broadcast has pending deliveries")
    if not complete and not pending:
        raise RuntimeError("incomplete morning broadcast has no pending deliveries")
    return MorningBroadcastBatch(message, tuple(pending), complete)


def _normalize_schema_sql(value: str) -> str:
    return " ".join(value.strip().removesuffix(";").split())


def _validate_refresh_state(
    row: tuple[object, ...] | aiosqlite.Row,
) -> tuple[Literal["success", "failure"], datetime, int | None]:
    if len(row) != 5:
        raise RuntimeError("stored rate refresh state has an invalid field count")
    status = row[0]
    if status not in ("success", "failure"):
        raise RuntimeError("stored rate refresh status is invalid")
    attempted_at = _datetime_from_storage(row[1], "attempted_at")
    snapshot_id, service, error_code = row[2], row[3], row[4]
    if status == "success":
        if (
            not isinstance(snapshot_id, int)
            or isinstance(snapshot_id, bool)
            or service is not None
            or error_code is not None
        ):
            raise RuntimeError("stored successful rate refresh state is invalid")
        return "success", attempted_at, snapshot_id
    if snapshot_id is not None:
        raise RuntimeError("stored failed rate refresh references a snapshot")
    _stored_text(service, "refresh service")
    _stored_text(error_code, "refresh error_code")
    return "failure", attempted_at, None


def _validate_status_text(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    if not value or value != value.strip() or len(value) > 128 or "\x00" in value:
        raise ValueError(f"{field} must be non-empty normalized text up to 128 characters")
    return value


def _run_date_to_storage(value: date) -> str:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError("run_date must be a date")
    return value.isoformat()


def _validate_message(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("message must be a string")
    if not value or len(value) > 4096 or "\x00" in value:
        raise ValueError("message must contain between 1 and 4096 characters")
    return value


def _validate_broadcast_row(
    row: tuple[object, ...] | aiosqlite.Row,
    expected_run_key: str,
) -> tuple[str, bool]:
    if len(row) != 4 or row[0] != expected_run_key:
        raise RuntimeError("stored morning broadcast is invalid")
    message = _validate_message(_stored_text(row[1], "broadcast message"))
    created_at = _datetime_from_storage(row[2], "broadcast created_at")
    completed_value = row[3]
    if completed_value is None:
        return message, False
    completed_at = _datetime_from_storage(completed_value, "broadcast completed_at")
    if completed_at < created_at:
        raise RuntimeError("stored morning broadcast timestamps are invalid")
    return message, True


def _validate_delivery_row(row: tuple[object, ...] | aiosqlite.Row) -> str:
    if len(row) != 2 or row[0] not in ("pending", "sent", "skipped"):
        raise RuntimeError("stored morning delivery is invalid")
    state = str(row[0])
    finished_at = row[1]
    if state == "pending":
        if finished_at is not None:
            raise RuntimeError("pending morning delivery has a finished timestamp")
    elif finished_at is None:
        raise RuntimeError("finished morning delivery has no finished timestamp")
    else:
        _datetime_from_storage(finished_at, "delivery finished_at")
    return state


def _snapshot_from_row(row: tuple[object, ...] | aiosqlite.Row) -> RateSnapshot:
    if len(row) != 11:
        raise RuntimeError("stored rate snapshot has an invalid field count")
    try:
        altyn = ExchangeQuote(
            exchange=Exchange.ALTYN,
            bid=_stored_decimal(row[0], "altyn_bid"),
            ask=_stored_decimal(row[1], "altyn_ask"),
            buy_fee_rate=_stored_decimal(row[2], "altyn_buy_fee_rate"),
            sell_fee_rate=_stored_decimal(row[3], "altyn_sell_fee_rate"),
            buy_fee_mode=BuyFeeMode(_stored_text(row[4], "altyn_buy_fee_mode")),
        )
        rapira = ExchangeQuote(
            exchange=Exchange.RAPIRA,
            bid=_stored_decimal(row[5], "rapira_bid"),
            ask=_stored_decimal(row[6], "rapira_ask"),
            buy_fee_rate=_stored_decimal(row[7], "rapira_buy_fee_rate"),
            sell_fee_rate=_stored_decimal(row[8], "rapira_sell_fee_rate"),
            buy_fee_mode=BuyFeeMode(_stored_text(row[9], "rapira_buy_fee_mode")),
        )
        fetched_at = _datetime_from_storage(row[10], "fetched_at")
        return RateSnapshot(altyn=altyn, rapira=rapira, fetched_at=fetched_at)
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError("stored rate snapshot is invalid") from exc


def _validate_user_row(row: tuple[object, ...] | aiosqlite.Row) -> tuple[int, bool]:
    if len(row) != 4:
        raise RuntimeError("stored user has an invalid field count")
    chat_id = row[0]
    subscribed = row[1]
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        raise RuntimeError("stored user chat_id is invalid")
    if not isinstance(subscribed, int) or isinstance(subscribed, bool) or subscribed not in (0, 1):
        raise RuntimeError("stored user subscription state is invalid")
    first_seen_at = _datetime_from_storage(row[2], "first_seen_at")
    updated_at = _datetime_from_storage(row[3], "updated_at")
    if updated_at < first_seen_at:
        raise RuntimeError("stored user timestamps are invalid")
    return chat_id, bool(subscribed)


def _stored_decimal(value: object, field: str) -> Decimal:
    text = _stored_text(value, field)
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise RuntimeError(f"stored {field} is not a decimal") from exc


def _stored_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"stored {field} is not valid text")
    return value


def _datetime_to_storage(value: datetime) -> str:
    _require_aware_datetime(value, "datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _datetime_from_storage(value: object, field: str) -> datetime:
    text = _stored_text(value, field)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RuntimeError(f"stored {field} is not a valid datetime") from exc
    try:
        _require_aware_datetime(parsed, field)
    except ValueError as exc:
        raise RuntimeError(f"stored {field} is not timezone-aware") from exc
    return parsed


def _require_aware_datetime(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _coerce_max_age(value: int | float | Decimal) -> Decimal:
    if isinstance(value, bool):
        raise TypeError("max_age_seconds must be a number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("max_age_seconds must be a finite non-negative number") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("max_age_seconds must be a finite non-negative number")
    return result


def _require_chat_id(chat_id: int) -> None:
    if not isinstance(chat_id, int) or isinstance(chat_id, bool):
        raise TypeError("chat_id must be an int")
