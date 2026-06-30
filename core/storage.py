"""SQLite persistence for sessions, rooms, subscriptions and readings."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .models import Reading, RoomRef, normalize_subscription
from .session_identity import (
    canonical_session_umo,
    session_display_name,
    session_identity_key,
)


class RevisionConflict(RuntimeError):
    """Dashboard data changed since it was loaded."""


class ElectricityStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "electricity_monitor.db"
        self._lock = threading.RLock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR IGNORE INTO meta(key, value) VALUES ('revision', '0');

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    umo TEXT PRIMARY KEY,
                    platform TEXT NOT NULL DEFAULT '',
                    chat_type TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '',
                    last_seen INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS rooms (
                    room_key TEXT PRIMARY KEY,
                    area_id TEXT NOT NULL,
                    area_name TEXT NOT NULL DEFAULT '',
                    building_code TEXT NOT NULL,
                    building_name TEXT NOT NULL DEFAULT '',
                    floor_code TEXT NOT NULL,
                    floor_name TEXT NOT NULL DEFAULT '',
                    room_code TEXT NOT NULL,
                    room_name TEXT NOT NULL DEFAULT '',
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    umo TEXT NOT NULL REFERENCES sessions(umo) ON DELETE CASCADE,
                    room_key TEXT NOT NULL REFERENCES rooms(room_key) ON DELETE CASCADE,
                    alias TEXT NOT NULL COLLATE NOCASE,
                    unit TEXT NOT NULL DEFAULT '度',
                    threshold_text TEXT NOT NULL DEFAULT '20',
                    interval_seconds INTEGER NOT NULL DEFAULT 900,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    alerted INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    UNIQUE(umo, alias)
                );

                CREATE TABLE IF NOT EXISTS subscription_runtime (
                    subscription_id INTEGER PRIMARY KEY
                        REFERENCES subscriptions(id) ON DELETE CASCADE,
                    last_scan_at INTEGER NOT NULL DEFAULT 0,
                    last_success_at INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    last_alert_at INTEGER NOT NULL DEFAULT 0,
                    last_alert_error TEXT NOT NULL DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS readings (
                    room_key TEXT NOT NULL REFERENCES rooms(room_key) ON DELETE CASCADE,
                    captured_at INTEGER NOT NULL,
                    value_text TEXT NOT NULL,
                    balance_text TEXT NOT NULL DEFAULT '',
                    room_name TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(room_key, captured_at)
                );

                CREATE TABLE IF NOT EXISTS diagnostics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_seen
                    ON sessions(last_seen DESC);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_umo
                    ON subscriptions(umo, enabled, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_subscriptions_room
                    ON subscriptions(room_key, enabled);
                CREATE INDEX IF NOT EXISTS idx_readings_room_time
                    ON readings(room_key, captured_at DESC);
                CREATE INDEX IF NOT EXISTS idx_diagnostics_time
                    ON diagnostics(created_at DESC);
                """
            )
            defaults = {
                "auth_state": "unconfigured",
                "auth_error": "",
                "auth_notice_sent": "0",
                "admin_notice_umo": "",
            }
            for key, value in defaults.items():
                connection.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, value),
                )
            self._ensure_column(
                connection,
                "readings",
                "balance_text",
                "TEXT NOT NULL DEFAULT ''",
            )
            self._ensure_column(
                connection,
                "subscription_runtime",
                "last_alert_at",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                connection,
                "subscription_runtime",
                "last_alert_error",
                "TEXT NOT NULL DEFAULT ''",
            )

    @property
    def revision(self) -> int:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'revision'"
            ).fetchone()
        return int(row["value"] if row else 0)

    def _bump_revision(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'revision'"
        ).fetchone()
        revision = int(row["value"] if row else 0) + 1
        connection.execute(
            "INSERT INTO meta(key, value) VALUES ('revision', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(revision),),
        )
        return revision

    def register_session(
        self,
        *,
        umo: str,
        platform: str = "",
        chat_type: str = "",
        session_id: str = "",
        display_name: str = "",
        now: int | None = None,
    ) -> str | None:
        if not str(umo or "").strip():
            return None
        timestamp = int(now if now is not None else time.time())
        canonical = canonical_session_umo(
            umo,
            platform=platform,
            chat_type=chat_type,
            session_id=session_id,
        )
        with self._lock, self._connect() as connection:
            existing = None
            if chat_type and session_id:
                existing = connection.execute(
                    "SELECT umo FROM sessions WHERE chat_type = ? AND session_id = ? "
                    "ORDER BY last_seen DESC LIMIT 1",
                    (str(chat_type), str(session_id)),
                ).fetchone()
            target = str(existing["umo"]) if existing else canonical
            connection.execute(
                """
                INSERT INTO sessions(
                    umo, platform, chat_type, session_id, display_name, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(umo) DO UPDATE SET
                    platform = CASE WHEN excluded.platform != ''
                        THEN excluded.platform ELSE sessions.platform END,
                    chat_type = CASE WHEN excluded.chat_type != ''
                        THEN excluded.chat_type ELSE sessions.chat_type END,
                    session_id = CASE WHEN excluded.session_id != ''
                        THEN excluded.session_id ELSE sessions.session_id END,
                    display_name = CASE WHEN excluded.display_name != ''
                        THEN excluded.display_name ELSE sessions.display_name END,
                    last_seen = MAX(sessions.last_seen, excluded.last_seen)
                """,
                (
                    target,
                    str(platform or ""),
                    str(chat_type or ""),
                    str(session_id or ""),
                    str(display_name or ""),
                    timestamp,
                ),
            )
        return target

    def list_sessions(self, limit: int = 1000) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sessions ORDER BY last_seen DESC LIMIT ?",
                (max(1, min(int(limit), 5000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_session(self, umo: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE umo = ?",
                (umo,),
            ).fetchone()
        return dict(row) if row else None

    def remove_synthetic_session(
        self,
        *,
        platform: str,
        chat_type: str,
        session_id: str,
    ) -> int:
        synthetic_name = session_display_name(chat_type, session_id)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM sessions
                WHERE platform = ?
                  AND chat_type = ?
                  AND session_id = ?
                  AND (display_name = '' OR display_name = ?)
                  AND NOT EXISTS (
                    SELECT 1 FROM subscriptions
                    WHERE subscriptions.umo = sessions.umo
                  )
                """,
                (
                    str(platform or ""),
                    str(chat_type or ""),
                    str(session_id or ""),
                    synthetic_name,
                ),
            )
            if cursor.rowcount:
                self._bump_revision(connection)
            return int(cursor.rowcount)

    def save_room(
        self,
        room: RoomRef,
        *,
        now: int | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        timestamp = int(now if now is not None else time.time())
        values = (
            room.key,
            room.area_id,
            room.area_name,
            room.building_code,
            room.building_name,
            room.floor_code,
            room.floor_name,
            room.room_code,
            room.room_name,
            timestamp,
        )
        sql = """
            INSERT INTO rooms(
                room_key, area_id, area_name, building_code, building_name,
                floor_code, floor_name, room_code, room_name, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(room_key) DO UPDATE SET
                area_name = excluded.area_name,
                building_name = excluded.building_name,
                floor_name = excluded.floor_name,
                room_name = excluded.room_name,
                updated_at = excluded.updated_at
        """
        if connection is not None:
            connection.execute(sql, values)
        else:
            with self._lock, self._connect() as own_connection:
                own_connection.execute(sql, values)
        return room.key

    def save_subscription(
        self,
        *,
        umo: str,
        room: RoomRef,
        config: dict[str, Any],
        subscription_id: int | None = None,
        expected_revision: int | None = None,
        now: int | None = None,
    ) -> tuple[dict[str, Any], int]:
        normalized = normalize_subscription(config)
        timestamp = int(now if now is not None else time.time())
        with self._lock, self._connect() as connection:
            self._check_revision(connection, expected_revision)
            if not connection.execute(
                "SELECT 1 FROM sessions WHERE umo = ?", (umo,)
            ).fetchone():
                raise ValueError("会话不存在，请先让机器人收到该会话消息。")
            self.save_room(room, now=timestamp, connection=connection)
            try:
                if subscription_id is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO subscriptions(
                            umo, room_key, alias, unit, threshold_text,
                            interval_seconds, enabled, alerted, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                        """,
                        (
                            umo,
                            room.key,
                            normalized["alias"],
                            normalized["unit"],
                            normalized["threshold"],
                            normalized["interval_seconds"],
                            int(normalized["enabled"]),
                            timestamp,
                            timestamp,
                        ),
                    )
                    subscription_id = int(cursor.lastrowid)
                else:
                    cursor = connection.execute(
                        """
                        UPDATE subscriptions SET
                            alerted = CASE WHEN room_key <> ? THEN 0 ELSE alerted END,
                            room_key = ?, alias = ?, unit = ?, threshold_text = ?,
                            interval_seconds = ?, enabled = ?, updated_at = ?
                        WHERE id = ? AND umo = ?
                        """,
                        (
                            room.key,
                            room.key,
                            normalized["alias"],
                            normalized["unit"],
                            normalized["threshold"],
                            normalized["interval_seconds"],
                            int(normalized["enabled"]),
                            timestamp,
                            int(subscription_id),
                            umo,
                        ),
                    )
                    if cursor.rowcount == 0:
                        raise ValueError("订阅不存在或不属于该会话。")
            except sqlite3.IntegrityError as exc:
                if "subscriptions.umo, subscriptions.alias" in str(exc):
                    raise ValueError("当前会话已存在同名订阅。") from exc
                raise
            connection.execute(
                "INSERT OR IGNORE INTO subscription_runtime(subscription_id) VALUES (?)",
                (int(subscription_id),),
            )
            revision = self._bump_revision(connection)
        saved = self.get_subscription(int(subscription_id))
        if saved is None:
            raise RuntimeError("订阅保存后无法读取。")
        return saved, revision

    def delete_subscription(
        self,
        subscription_id: int,
        *,
        umo: str | None = None,
        expected_revision: int | None = None,
    ) -> int:
        with self._lock, self._connect() as connection:
            self._check_revision(connection, expected_revision)
            if umo:
                cursor = connection.execute(
                    "DELETE FROM subscriptions WHERE id = ? AND umo = ?",
                    (int(subscription_id), umo),
                )
            else:
                cursor = connection.execute(
                    "DELETE FROM subscriptions WHERE id = ?",
                    (int(subscription_id),),
                )
            if cursor.rowcount == 0:
                raise ValueError("订阅不存在。")
            return self._bump_revision(connection)

    def update_subscription_fields(
        self,
        subscription_id: int,
        *,
        umo: str,
        enabled: bool | None = None,
        threshold: str | None = None,
        interval_seconds: int | None = None,
        now: int | None = None,
    ) -> dict[str, Any]:
        current = self.get_subscription(subscription_id)
        if not current or current["umo"] != umo:
            raise ValueError("订阅不存在或不属于当前会话。")
        config = {
            "alias": current["alias"],
            "unit": current["unit"],
            "threshold": current["threshold"],
            "interval_seconds": current["interval_seconds"],
            "enabled": current["enabled"],
        }
        if enabled is not None:
            config["enabled"] = enabled
        if threshold is not None:
            config["threshold"] = threshold
        if interval_seconds is not None:
            config["interval_seconds"] = interval_seconds
        room = RoomRef.from_dict(current["room"])
        saved, _revision = self.save_subscription(
            umo=umo,
            room=room,
            config=config,
            subscription_id=subscription_id,
            now=now,
        )
        return saved

    def get_subscription(self, subscription_id: int) -> dict[str, Any] | None:
        items = self._subscription_query("WHERE sub.id = ?", (int(subscription_id),))
        return items[0] if items else None

    def find_subscription(self, umo: str, alias: str) -> dict[str, Any] | None:
        items = self._subscription_query(
            "WHERE sub.umo = ? AND sub.alias = ? COLLATE NOCASE",
            (umo, str(alias or "").strip()),
        )
        return items[0] if items else None

    def list_subscriptions(
        self,
        *,
        umo: str | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        conditions = []
        params: list[Any] = []
        if umo is not None:
            conditions.append("sub.umo = ?")
            params.append(umo)
        if enabled_only:
            conditions.append("sub.enabled = 1")
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return self._subscription_query(where, tuple(params))

    def list_due_subscriptions(self, *, now: int) -> list[dict[str, Any]]:
        return self._subscription_query(
            """
            WHERE sub.enabled = 1
              AND (
                COALESCE(rt.last_scan_at, 0) = 0
                OR ? - COALESCE(rt.last_scan_at, 0) >= sub.interval_seconds
              )
            """,
            (int(now),),
        )

    def _subscription_query(
        self,
        where: str,
        params: tuple[Any, ...],
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    sub.*,
                    rt.last_scan_at,
                    rt.last_success_at,
                    rt.last_error,
                    rt.last_alert_at,
                    rt.last_alert_error,
                    r.area_id, r.area_name, r.building_code, r.building_name,
                    r.floor_code, r.floor_name, r.room_code, r.room_name,
                    (
                        SELECT value_text FROM readings rd
                        WHERE rd.room_key = sub.room_key
                        ORDER BY rd.captured_at DESC LIMIT 1
                    ) latest_value,
                    (
                        SELECT captured_at FROM readings rd
                        WHERE rd.room_key = sub.room_key
                        ORDER BY rd.captured_at DESC LIMIT 1
                    ) latest_at,
                    (
                        SELECT balance_text FROM readings rd
                        WHERE rd.room_key = sub.room_key
                        ORDER BY rd.captured_at DESC LIMIT 1
                    ) latest_balance
                FROM subscriptions sub
                JOIN rooms r ON r.room_key = sub.room_key
                LEFT JOIN subscription_runtime rt ON rt.subscription_id = sub.id
                {where}
                ORDER BY sub.updated_at DESC, sub.id DESC
                """,
                params,
            ).fetchall()
        return [self._subscription_row(row) for row in rows]

    @staticmethod
    def _subscription_row(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        room = {
            "area_id": data.pop("area_id"),
            "area_name": data.pop("area_name"),
            "building_code": data.pop("building_code"),
            "building_name": data.pop("building_name"),
            "floor_code": data.pop("floor_code"),
            "floor_name": data.pop("floor_name"),
            "room_code": data.pop("room_code"),
            "room_name": data.pop("room_name"),
            "room_key": data["room_key"],
        }
        data["room"] = room
        data["threshold"] = data.pop("threshold_text")
        data["enabled"] = bool(data["enabled"])
        data["alerted"] = bool(data["alerted"])
        data["last_scan_at"] = int(data.get("last_scan_at") or 0)
        data["last_success_at"] = int(data.get("last_success_at") or 0)
        data["last_error"] = str(data.get("last_error") or "")
        data["last_alert_at"] = int(data.get("last_alert_at") or 0)
        data["last_alert_error"] = str(data.get("last_alert_error") or "")
        data["latest_at"] = int(data.get("latest_at") or 0)
        data["latest_balance"] = str(data.get("latest_balance") or "")
        return data

    def update_runtime(
        self,
        subscription_id: int,
        *,
        last_scan_at: int | None = None,
        last_success_at: int | None = None,
        last_error: str | None = None,
        last_alert_at: int | None = None,
        last_alert_error: str | None = None,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO subscription_runtime(subscription_id) VALUES (?)",
                (int(subscription_id),),
            )
            assignments = []
            params: list[Any] = []
            if last_scan_at is not None:
                assignments.append("last_scan_at = ?")
                params.append(int(last_scan_at))
            if last_success_at is not None:
                assignments.append("last_success_at = ?")
                params.append(int(last_success_at))
            if last_error is not None:
                assignments.append("last_error = ?")
                params.append(str(last_error)[:2000])
            if last_alert_at is not None:
                assignments.append("last_alert_at = ?")
                params.append(int(last_alert_at))
            if last_alert_error is not None:
                assignments.append("last_alert_error = ?")
                params.append(str(last_alert_error)[:2000])
            if assignments:
                params.append(int(subscription_id))
                connection.execute(
                    f"UPDATE subscription_runtime SET {', '.join(assignments)} "
                    "WHERE subscription_id = ?",
                    tuple(params),
                )

    def set_alerted(self, subscription_id: int, alerted: bool) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE subscriptions SET alerted = ? WHERE id = ?",
                (int(alerted), int(subscription_id)),
            )

    def save_reading(self, reading: Reading) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO readings(
                    room_key, captured_at, value_text, balance_text, room_name
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(room_key, captured_at) DO UPDATE SET
                    value_text = excluded.value_text,
                    balance_text = excluded.balance_text,
                    room_name = excluded.room_name
                """,
                (
                    reading.room_key,
                    int(reading.captured_at),
                    str(reading.value),
                    str(reading.balance) if reading.balance is not None else "",
                    reading.room_name,
                ),
            )

    def get_history(
        self,
        room_key: str,
        *,
        since: int | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        conditions = ["room_key = ?"]
        params: list[Any] = [room_key]
        if since is not None:
            conditions.append("captured_at >= ?")
            params.append(int(since))
        params.append(max(1, min(int(limit), 20_000)))
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    room_key, captured_at, value_text value,
                    balance_text balance, room_name
                FROM readings
                WHERE {' AND '.join(conditions)}
                ORDER BY captured_at ASC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def set_auth_state(self, state: str, error: str = "") -> None:
        with self._lock, self._connect() as connection:
            self._set_setting(connection, "auth_state", str(state))
            self._set_setting(connection, "auth_error", str(error)[:1000])
            if state == "valid":
                self._set_setting(connection, "auth_notice_sent", "0")

    def auth_snapshot(self) -> dict[str, Any]:
        return {
            "state": self.get_setting("auth_state", "unconfigured"),
            "error": self.get_setting("auth_error", ""),
            "notice_sent": self.get_setting("auth_notice_sent", "0") == "1",
        }

    def mark_auth_notice_sent(self, sent: bool) -> None:
        self.set_setting("auth_notice_sent", "1" if sent else "0")

    def set_admin_notice_umo(self, umo: str) -> None:
        session = self.get_session(umo)
        if not session:
            raise ValueError("管理员通知会话不存在。")
        if session.get("chat_type") != "private":
            raise ValueError("管理员通知目标必须是私聊会话。")
        self.set_setting("admin_notice_umo", umo)

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
        return str(row["value"]) if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._connect() as connection:
            self._set_setting(connection, key, value)

    @staticmethod
    def _set_setting(
        connection: sqlite3.Connection,
        key: str,
        value: str,
    ) -> None:
        connection.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(key), str(value)),
        )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        if column in {str(row["name"]) for row in rows}:
            return
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def add_diagnostic(
        self,
        scope: str,
        message: str,
        *,
        level: str = "warning",
        now: int | None = None,
    ) -> None:
        timestamp = int(now if now is not None else time.time())
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO diagnostics(scope, level, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (str(scope), str(level), str(message)[:2000], timestamp),
            )

    def list_diagnostics(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM diagnostics ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(row) for row in rows]

    def remove_old_data(self, *, now: int | None = None) -> None:
        timestamp = int(now if now is not None else time.time())
        cutoff = timestamp - 30 * 86_400
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM readings WHERE captured_at < ?", (cutoff,))
            connection.execute(
                "DELETE FROM diagnostics WHERE created_at < ?",
                (cutoff,),
            )

    @staticmethod
    def _check_revision(
        connection: sqlite3.Connection,
        expected_revision: int | None,
    ) -> None:
        if expected_revision is None:
            return
        row = connection.execute(
            "SELECT value FROM meta WHERE key = 'revision'"
        ).fetchone()
        current = int(row["value"] if row else 0)
        if int(expected_revision) != current:
            raise RevisionConflict(
                f"配置已更新，请刷新后重试（当前版本 {current}）。"
            )


def dedupe_sessions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = session_identity_key(
            row.get("chat_type", ""),
            row.get("session_id", ""),
            row.get("umo", ""),
        )
        current = selected.get(key)
        if current is None or int(row.get("last_seen") or 0) > int(
            current.get("last_seen") or 0
        ):
            selected[key] = row
    return sorted(
        selected.values(),
        key=lambda item: int(item.get("last_seen") or 0),
        reverse=True,
    )
