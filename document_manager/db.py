"""SQLite database helpers."""
from __future__ import annotations

import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash
from pathlib import Path
from typing import Any, Iterable

import click
from flask import Flask, current_app, g


ISO_FORMAT = "%Y-%m-%dT%H:%M:%S"
DEFAULT_LAST_PATH_KEY = "last_path"
WARNING_DAYS_KEY = "warning_days"


def get_db() -> sqlite3.Connection:
    """Return a cached database connection for the active Flask context."""

    if "db" not in g:
        config = current_app.config["APP_CONFIG"]
        database_path: Path = config.database_path
        conn = sqlite3.connect(database_path)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


def close_db(_: Any = None) -> None:
    """Close the cached database connection if it exists."""

    conn: sqlite3.Connection | None = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_app(app: Flask) -> None:
    """Register database handlers and ensure schema exists."""

    config = app.config["APP_CONFIG"]
    config.database_path.parent.mkdir(parents=True, exist_ok=True)

    app.teardown_appcontext(close_db)

    @app.cli.command("init-db")
    def init_db_command() -> None:
        """Recreate database tables from scratch."""

        init_schema()
        click.echo("Banco de dados inicializado.")

    with app.app_context():
        init_schema()


def init_schema() -> None:
    """Create database tables if they do not exist."""

    conn = get_db()
    cursor = conn.cursor()

    def _table_has_column(table: str, column: str) -> bool:
        cursor.execute(f"PRAGMA table_info({table})")
        return any(row["name"] == column for row in cursor.fetchall())

    def _get_default_user_id() -> int:
        row = cursor.execute("SELECT id FROM users WHERE username = ?", ("WALLACE",)).fetchone()
        if row:
            return row["id"]
        fallback = cursor.execute("SELECT id FROM users ORDER BY id ASC LIMIT 1").fetchone()
        if not fallback:
            raise RuntimeError('Nenhum usuario cadastrado para migracao.')
        return fallback["id"]

    def _ensure_user_scoped_table(table: str, create_sql: str) -> None:
        if _table_has_column(table, 'user_id'):
            return
        cursor.execute(f"ALTER TABLE {table} RENAME TO {table}_old")
        cursor.execute(create_sql)
        user_id = _get_default_user_id()
        cursor.execute(
            f"INSERT INTO {table} (user_id, name, path, created_at) SELECT ?, name, path, created_at FROM {table}_old",
            (user_id,),
        )
        cursor.execute(f"DROP TABLE {table}_old")
    cursor.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            validity_type TEXT NOT NULL DEFAULT 'not_defined',
            validity_date TEXT,
            warning_days INTEGER,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT NOT NULL,
            action TEXT NOT NULL,
            username TEXT,
            details TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, name),
            UNIQUE(user_id, path)
        );

        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, name),
            UNIQUE(user_id, path)
        );

        CREATE TABLE IF NOT EXISTS drive_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            google_email TEXT NOT NULL,
            google_name TEXT NOT NULL DEFAULT '',
            google_permission_id TEXT,
            status TEXT NOT NULL DEFAULT 'connected',
            is_active INTEGER NOT NULL DEFAULT 0,
            credentials_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_connected_at TEXT,
            last_used_at TEXT,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        """
    )

    if not _table_has_column('drive_accounts', 'google_permission_id'):
        cursor.execute("ALTER TABLE drive_accounts ADD COLUMN google_permission_id TEXT")
    if not _table_has_column('drive_accounts', 'status'):
        cursor.execute("ALTER TABLE drive_accounts ADD COLUMN status TEXT NOT NULL DEFAULT 'connected'")
    if not _table_has_column('drive_accounts', 'is_active'):
        cursor.execute("ALTER TABLE drive_accounts ADD COLUMN is_active INTEGER NOT NULL DEFAULT 0")
    if not _table_has_column('drive_accounts', 'credentials_json'):
        cursor.execute("ALTER TABLE drive_accounts ADD COLUMN credentials_json TEXT")
    if not _table_has_column('drive_accounts', 'created_at'):
        cursor.execute("ALTER TABLE drive_accounts ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
    if not _table_has_column('drive_accounts', 'updated_at'):
        cursor.execute("ALTER TABLE drive_accounts ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
    if not _table_has_column('drive_accounts', 'last_connected_at'):
        cursor.execute("ALTER TABLE drive_accounts ADD COLUMN last_connected_at TEXT")
    if not _table_has_column('drive_accounts', 'last_used_at'):
        cursor.execute("ALTER TABLE drive_accounts ADD COLUMN last_used_at TEXT")

    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_drive_accounts_user_email ON drive_accounts(user_id, google_email)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_drive_accounts_user_permission ON drive_accounts(user_id, google_permission_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_drive_accounts_user_active ON drive_accounts(user_id, is_active)"
    )

    config = current_app.config["APP_CONFIG"]
    default_warning_days = str(config.warning_days)
    cursor.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
        (WARNING_DAYS_KEY, default_warning_days),
    )

    default_root: Path
    if config.base_paths:
        default_root = config.base_paths[0].resolve()
    else:
        default_root = Path.home().resolve()
    cursor.execute(
        "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
        (DEFAULT_LAST_PATH_KEY, str(default_root)),
    )

    timestamp = datetime.utcnow().strftime(ISO_FORMAT)
    admin_hash = generate_password_hash('81097157')
    cursor.execute("""
        INSERT OR IGNORE INTO users(username, password_hash, role, is_active, created_at, updated_at)
        VALUES(?, ?, 'admin', 1, ?, ?)
    """, ("WALLACE", admin_hash, timestamp, timestamp))

    presets_sql = """
        CREATE TABLE IF NOT EXISTS presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, name),
            UNIQUE(user_id, path)
        );
    """
    favorites_sql = """
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
            UNIQUE(user_id, name),
            UNIQUE(user_id, path)
        );
    """
    _ensure_user_scoped_table('presets', presets_sql)
    _ensure_user_scoped_table('favorites', favorites_sql)

    conn.commit()


def query(sql: str, params: Iterable[Any] | None = None) -> list[sqlite3.Row]:
    """Execute a SELECT statement and return the results."""

    conn = get_db()
    cursor = conn.execute(sql, params or [])
    rows = cursor.fetchall()
    cursor.close()
    return rows


def execute(sql: str, params: Iterable[Any] | None = None) -> int:
    """Execute an INSERT/UPDATE/DELETE statement.

    Returns the number of affected rows.
    """

    conn = get_db()
    cursor = conn.execute(sql, params or [])
    conn.commit()
    rowcount = cursor.rowcount
    cursor.close()
    return rowcount


def touch_document(path: str) -> None:
    """Ensure a document record exists for the given path."""

    timestamp = datetime.utcnow().strftime(ISO_FORMAT)
    execute(
        """
        INSERT INTO documents(path, created_at, updated_at)
        VALUES(?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET updated_at=excluded.updated_at
        """,
        (path, timestamp, timestamp),
    )


def record_audit(path: str, action: str, username: str | None, details: str | None) -> None:
    """Persist an audit trail event."""

    timestamp = datetime.utcnow().strftime(ISO_FORMAT)
    if username is None:
        current_user = getattr(g, 'current_user', None)
        if isinstance(current_user, dict):
            username = current_user.get('username')
    execute(
        "INSERT INTO audit_logs(path, action, username, details, created_at) VALUES(?, ?, ?, ?, ?)",
        (path, action, username, details, timestamp),
    )
