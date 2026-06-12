"""Persistent PostgreSQL storage for Google Drive connected accounts."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from datetime import datetime
from threading import RLock
from time import perf_counter
from typing import Any

from flask import current_app

from .. import db
from ..config import AppConfig

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]


class DriveAccountsStorageError(RuntimeError):
    """Raised when Google Drive accounts storage cannot be used."""


_SCHEMA_READY = False
_SCHEMA_LOCK = RLock()
_MIGRATED_USERNAMES: set[str] = set()
_MIGRATION_LOCK = RLock()


def _database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise DriveAccountsStorageError(
            "DATABASE_URL nao configurada. Configure o Postgres persistente do Render para manter o login do Google Drive."
        )
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


def _connect():
    if psycopg2 is None:
        raise DriveAccountsStorageError("Dependencia psycopg2-binary ausente. Adicione psycopg2-binary ao requirements.txt.")
    return psycopg2.connect(_database_url())


def init_schema() -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS drive_accounts (
                    id BIGSERIAL PRIMARY KEY,
                    username TEXT NOT NULL,
                    google_email TEXT NOT NULL,
                    google_name TEXT NOT NULL DEFAULT '',
                    google_permission_id TEXT,
                    status TEXT NOT NULL DEFAULT 'connected',
                    is_active BOOLEAN NOT NULL DEFAULT FALSE,
                    credentials_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_connected_at TEXT,
                    last_used_at TEXT
                );
                """
            )
            cur.execute("ALTER TABLE drive_accounts ADD COLUMN IF NOT EXISTS username TEXT")
            cur.execute("ALTER TABLE drive_accounts ADD COLUMN IF NOT EXISTS google_email TEXT")
            cur.execute("ALTER TABLE drive_accounts ADD COLUMN IF NOT EXISTS google_name TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE drive_accounts ADD COLUMN IF NOT EXISTS google_permission_id TEXT")
            cur.execute("ALTER TABLE drive_accounts ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'connected'")
            cur.execute("ALTER TABLE drive_accounts ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT FALSE")
            cur.execute("ALTER TABLE drive_accounts ADD COLUMN IF NOT EXISTS credentials_json TEXT")
            cur.execute("ALTER TABLE drive_accounts ADD COLUMN IF NOT EXISTS created_at TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE drive_accounts ADD COLUMN IF NOT EXISTS updated_at TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE drive_accounts ADD COLUMN IF NOT EXISTS last_connected_at TEXT")
            cur.execute("ALTER TABLE drive_accounts ADD COLUMN IF NOT EXISTS last_used_at TEXT")
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_drive_accounts_username_email
                    ON drive_accounts(username, google_email)
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_drive_accounts_username_permission
                    ON drive_accounts(username, google_permission_id)
                    WHERE google_permission_id IS NOT NULL
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_drive_accounts_username_active
                    ON drive_accounts(username)
                    WHERE is_active = TRUE
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_drive_accounts_username_active
                    ON drive_accounts(username, is_active)
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_drive_accounts_username
                    ON drive_accounts(username)
                """
            )
        conn.commit()


def _utcnow_iso() -> str:
    return datetime.utcnow().strftime(db.ISO_FORMAT)


def ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        started = perf_counter()
        init_schema()
        _SCHEMA_READY = True
        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        try:
            current_app.logger.info("drive_accounts.ensure_schema initialized in %.2fms", elapsed_ms)
        except Exception:
            pass


def query_all(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(row) for row in rows]


def query_one(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = query_all(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rowcount = cur.rowcount
        conn.commit()
        return rowcount


def _sqlite_database_path() -> Path:
    try:
        config: AppConfig = current_app.config["APP_CONFIG"]
        return config.database_path
    except Exception:
        return Path("instance") / "documents.db"


def _legacy_user_id(conn: sqlite3.Connection, username: str) -> int | None:
    row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    if row is None:
        return None
    return int(row[0])


def _legacy_accounts(conn: sqlite3.Connection, username: str) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    user_id = _legacy_user_id(conn, username)
    if user_id is None:
        return []
    rows = conn.execute(
        """
        SELECT id, google_email, google_name, google_permission_id, status, is_active, credentials_json,
               created_at, updated_at, last_connected_at, last_used_at
        FROM drive_accounts
        WHERE user_id = ?
        ORDER BY is_active DESC, google_email ASC, id ASC
        """,
        (user_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def migrate_legacy_accounts_for_username(username: str) -> None:
    normalized = (username or "").strip().upper()
    if not normalized:
        return
    ensure_schema()
    with _MIGRATION_LOCK:
        if normalized in _MIGRATED_USERNAMES:
            return

        existing = query_one(
            "SELECT id FROM drive_accounts WHERE username = %s LIMIT 1",
            (normalized,),
        )
        if existing:
            _MIGRATED_USERNAMES.add(normalized)
            return

        sqlite_path = _sqlite_database_path()
        if not sqlite_path.exists():
            _MIGRATED_USERNAMES.add(normalized)
            return

        sqlite_conn = sqlite3.connect(sqlite_path)
        try:
            tables = sqlite_conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'drive_accounts'"
            ).fetchone()
            if not tables:
                _MIGRATED_USERNAMES.add(normalized)
                return

            rows = _legacy_accounts(sqlite_conn, normalized)
        finally:
            sqlite_conn.close()

        if not rows:
            _MIGRATED_USERNAMES.add(normalized)
            return

        with _connect() as conn:
            with conn.cursor() as cur:
                active_assigned = False
                for row in rows:
                    created_at = row.get("created_at") or _utcnow_iso()
                    updated_at = row.get("updated_at") or created_at
                    is_active = bool(row.get("is_active")) and not active_assigned
                    active_assigned = active_assigned or is_active
                    cur.execute(
                        """
                        INSERT INTO drive_accounts(
                            username, google_email, google_name, google_permission_id, status, is_active,
                            credentials_json, created_at, updated_at, last_connected_at, last_used_at
                        )
                        VALUES(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (username, google_email) DO NOTHING
                        """,
                        (
                            normalized,
                            row.get("google_email") or "",
                            row.get("google_name") or "",
                            row.get("google_permission_id") or None,
                            row.get("status") or "disconnected",
                            is_active,
                            row.get("credentials_json"),
                            created_at,
                            updated_at,
                            row.get("last_connected_at"),
                            row.get("last_used_at"),
                        ),
                    )
            conn.commit()

        _MIGRATED_USERNAMES.add(normalized)
