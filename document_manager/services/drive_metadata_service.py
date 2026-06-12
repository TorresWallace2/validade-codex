"""Persistent PostgreSQL metadata for Google Drive items.

Records are keyed by Google Drive file_id, never by display path.
This keeps favorites, pregoes, notes and validity stable across Render restarts,
Drive folder moves and service redeploys.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date
from threading import RLock
from time import perf_counter
from typing import Any, Iterator, Sequence

from flask import current_app

try:
    import psycopg2
    import psycopg2.extras
except ImportError:  # pragma: no cover
    psycopg2 = None  # type: ignore[assignment]


class DriveMetadataError(RuntimeError):
    """Raised when Google Drive metadata cannot be persisted."""


VALIDITY_TYPES = {"defined", "indeterminate", "not_defined"}
MANUAL_SOURCES = {"manual", "manual_not_defined", "manual_indeterminate"}
AUTO_SOURCE = "auto_filename"
_SCHEMA_READY = False
_SCHEMA_LOCK = RLock()


def _database_url() -> str:
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        raise DriveMetadataError(
            "DATABASE_URL nao configurada. Configure a Internal Database URL do Postgres no Render."
        )
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    return url


@contextmanager
def _connect() -> Iterator[Any]:
    if psycopg2 is None:
        raise DriveMetadataError("Dependencia psycopg2-binary ausente. Adicione psycopg2-binary ao requirements.txt.")
    conn = psycopg2.connect(_database_url())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema() -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS drive_file_metadata (
                    file_id TEXT PRIMARY KEY,
                    file_name TEXT,
                    source_uri TEXT,
                    mime_type TEXT,
                    web_url TEXT,
                    validity_type TEXT NOT NULL DEFAULT 'not_defined',
                    validity_date DATE,
                    warning_days INTEGER,
                    notes TEXT NOT NULL DEFAULT '',
                    is_favorite BOOLEAN NOT NULL DEFAULT FALSE,
                    auctions TEXT NOT NULL DEFAULT '',
                    validity_source TEXT NOT NULL DEFAULT 'not_defined',
                    manual_locked BOOLEAN NOT NULL DEFAULT FALSE,
                    auto_detected_date DATE,
                    auto_detected_name TEXT,
                    auto_applied_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS drive_user_favorites (
                    username TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    source_uri TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(username, file_id),
                    UNIQUE(username, account_id, name)
                );

                CREATE TABLE IF NOT EXISTS drive_user_presets (
                    username TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    account_id TEXT NOT NULL DEFAULT '',
                    name TEXT NOT NULL,
                    source_uri TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(username, file_id),
                    UNIQUE(username, account_id, name)
                );

                CREATE INDEX IF NOT EXISTS idx_drive_file_metadata_updated_at
                    ON drive_file_metadata(updated_at);
                CREATE INDEX IF NOT EXISTS idx_drive_user_favorites_username
                    ON drive_user_favorites(username);
                CREATE INDEX IF NOT EXISTS idx_drive_user_favorites_username_account
                    ON drive_user_favorites(username, account_id);
                CREATE INDEX IF NOT EXISTS idx_drive_user_presets_username
                    ON drive_user_presets(username);
                CREATE INDEX IF NOT EXISTS idx_drive_user_presets_username_account
                    ON drive_user_presets(username, account_id);
                """
            )
            cur.execute("ALTER TABLE drive_user_favorites ADD COLUMN IF NOT EXISTS account_id TEXT NOT NULL DEFAULT ''")
            cur.execute("ALTER TABLE drive_user_presets ADD COLUMN IF NOT EXISTS account_id TEXT NOT NULL DEFAULT ''")
            cur.execute("UPDATE drive_user_favorites SET account_id = COALESCE(account_id, '') WHERE account_id IS NULL")
            cur.execute("UPDATE drive_user_presets SET account_id = COALESCE(account_id, '') WHERE account_id IS NULL")
            cur.execute("ALTER TABLE drive_user_favorites DROP CONSTRAINT IF EXISTS drive_user_favorites_username_name_key")
            cur.execute("ALTER TABLE drive_user_presets DROP CONSTRAINT IF EXISTS drive_user_presets_username_name_key")
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_indexes
                        WHERE schemaname = ANY (current_schemas(false))
                          AND indexname = 'uq_drive_user_favorites_username_account_name'
                    ) THEN
                        CREATE UNIQUE INDEX uq_drive_user_favorites_username_account_name
                        ON drive_user_favorites(username, account_id, name);
                    END IF;
                END $$;
                """
            )
            cur.execute(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1
                        FROM pg_indexes
                        WHERE schemaname = ANY (current_schemas(false))
                          AND indexname = 'uq_drive_user_presets_username_account_name'
                    ) THEN
                        CREATE UNIQUE INDEX uq_drive_user_presets_username_account_name
                        ON drive_user_presets(username, account_id, name);
                    END IF;
                END $$;
                """
            )


def ensure_schema() -> None:
    # Avoid repeated DDL on every request.
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
            current_app.logger.info("drive_metadata.ensure_schema initialized in %.2fms", elapsed_ms)
        except Exception:
            pass


def _default_warning_days(warning_days: int | None = None) -> int:
    if warning_days:
        return int(warning_days)
    try:
        return int(current_app.config["APP_CONFIG"].warning_days)
    except Exception:  # pragma: no cover
        return 15


def _row_to_dict(row: Any | None) -> dict[str, Any]:
    return dict(row) if row else {}


def get_metadata(file_id: str) -> dict[str, Any]:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute("SELECT * FROM drive_file_metadata WHERE file_id = %s", (file_id,))
            return _row_to_dict(cur.fetchone())


def get_metadata_map(file_ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    ids = [item for item in file_ids if item]
    if not ids:
        return {}
    ensure_schema()
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute("SELECT * FROM drive_file_metadata WHERE file_id = ANY(%s)", (ids,))
            return {row["file_id"]: dict(row) for row in cur.fetchall()}


def touch_file(file_id: str, *, file_name: str = "", source_uri: str = "", mime_type: str = "", web_url: str = "") -> None:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO drive_file_metadata(file_id, file_name, source_uri, mime_type, web_url, warning_days)
                VALUES(%s, %s, %s, %s, %s, %s)
                ON CONFLICT(file_id) DO UPDATE SET
                    file_name = COALESCE(NULLIF(EXCLUDED.file_name, ''), drive_file_metadata.file_name),
                    source_uri = COALESCE(NULLIF(EXCLUDED.source_uri, ''), drive_file_metadata.source_uri),
                    mime_type = COALESCE(NULLIF(EXCLUDED.mime_type, ''), drive_file_metadata.mime_type),
                    web_url = COALESCE(NULLIF(EXCLUDED.web_url, ''), drive_file_metadata.web_url),
                    updated_at = CURRENT_TIMESTAMP
                """,
                (file_id, file_name, source_uri, mime_type, web_url, _default_warning_days()),
            )


def apply_auto_validity_from_filename(
    file_id: str,
    file_name: str,
    inferred_date: date | None,
    *,
    source_uri: str = "",
    mime_type: str = "",
    web_url: str = "",
    warning_days: int | None = None,
) -> dict[str, Any]:
    """Safely persist automatic validity.

    Automatic filename detection must never overwrite any manual user decision,
    including manual "Nao definido". We keep the auto-detected date for audit,
    but only apply it when the row is not manually locked and validity is still
    truly not defined.
    """
    ensure_schema()
    touch_file(file_id, file_name=file_name, source_uri=source_uri, mime_type=mime_type, web_url=web_url)
    if inferred_date is None:
        return get_metadata(file_id)

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute("SELECT * FROM drive_file_metadata WHERE file_id = %s FOR UPDATE", (file_id,))
            row = cur.fetchone() or {}
            current_type = (row.get("validity_type") or "not_defined")
            manual_locked = bool(row.get("manual_locked"))
            source = row.get("validity_source") or "not_defined"
            can_apply = (not manual_locked and current_type == "not_defined" and source not in MANUAL_SOURCES)
            if can_apply:
                cur.execute(
                    """
                    UPDATE drive_file_metadata SET
                        validity_type = 'defined',
                        validity_date = %s,
                        warning_days = COALESCE(warning_days, %s),
                        validity_source = %s,
                        manual_locked = FALSE,
                        auto_detected_date = %s,
                        auto_detected_name = %s,
                        auto_applied_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE file_id = %s
                    RETURNING *
                    """,
                    (inferred_date, _default_warning_days(warning_days), AUTO_SOURCE, inferred_date, file_name, file_id),
                )
            else:
                cur.execute(
                    """
                    UPDATE drive_file_metadata SET
                        auto_detected_date = %s,
                        auto_detected_name = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE file_id = %s
                    RETURNING *
                    """,
                    (inferred_date, file_name, file_id),
                )
            return dict(cur.fetchone())


def set_validity(file_id: str, validity_type: str, validity_date: date | None, warning_days: int | None = None) -> dict[str, Any]:
    validity_type = (validity_type or "").lower().strip()
    if validity_type not in VALIDITY_TYPES:
        raise DriveMetadataError("Tipo de validade invalido.")

    source = "manual"
    if validity_type == "indeterminate":
        source = "manual_indeterminate"
        validity_date = None
    elif validity_type == "not_defined":
        source = "manual_not_defined"
        validity_date = None

    ensure_schema()
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute(
                """
                INSERT INTO drive_file_metadata(file_id, validity_type, validity_date, warning_days, validity_source, manual_locked)
                VALUES(%s, %s, %s, %s, %s, TRUE)
                ON CONFLICT(file_id) DO UPDATE SET
                    validity_type = EXCLUDED.validity_type,
                    validity_date = EXCLUDED.validity_date,
                    warning_days = EXCLUDED.warning_days,
                    validity_source = EXCLUDED.validity_source,
                    manual_locked = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING *
                """,
                (file_id, validity_type, validity_date, _default_warning_days(warning_days), source),
            )
            return dict(cur.fetchone())


def set_notes(file_id: str, notes: str) -> dict[str, Any]:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute(
                """
                INSERT INTO drive_file_metadata(file_id, notes)
                VALUES(%s, %s)
                ON CONFLICT(file_id) DO UPDATE SET
                    notes = EXCLUDED.notes,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING *
                """,
                (file_id, notes or ""),
            )
            return dict(cur.fetchone())


def set_auctions(file_id: str, auctions: str) -> dict[str, Any]:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute(
                """
                INSERT INTO drive_file_metadata(file_id, auctions)
                VALUES(%s, %s)
                ON CONFLICT(file_id) DO UPDATE SET
                    auctions = EXCLUDED.auctions,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING *
                """,
                (file_id, auctions or ""),
            )
            return dict(cur.fetchone())


def add_favorite(username: str, file_id: str, account_id: str, name: str, source_uri: str) -> dict[str, Any]:
    ensure_schema()
    username = (username or "").strip().upper()
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute(
                """
                INSERT INTO drive_file_metadata(file_id, file_name, source_uri, is_favorite)
                VALUES(%s, %s, %s, TRUE)
                ON CONFLICT(file_id) DO UPDATE SET
                    file_name = COALESCE(NULLIF(EXCLUDED.file_name, ''), drive_file_metadata.file_name),
                    source_uri = EXCLUDED.source_uri,
                    is_favorite = TRUE,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (file_id, name, source_uri),
            )
            cur.execute(
                """
                INSERT INTO drive_user_favorites(username, file_id, account_id, name, source_uri)
                VALUES(%s, %s, %s, %s, %s)
                ON CONFLICT(username, file_id) DO UPDATE SET
                    account_id = EXCLUDED.account_id,
                    name = EXCLUDED.name,
                    source_uri = EXCLUDED.source_uri
                RETURNING file_id AS id, file_id, account_id, name, source_uri AS path, created_at
                """,
                (username, file_id, account_id, name, source_uri),
            )
            return dict(cur.fetchone())


def list_favorites(username: str) -> list[dict[str, Any]]:
    ensure_schema()
    username = (username or "").strip().upper()
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute(
                """
                SELECT file_id AS id, file_id, account_id, name, source_uri AS path, created_at
                FROM drive_user_favorites
                WHERE username = %s
                ORDER BY name ASC
                """,
                (username,),
            )
            return [dict(row) for row in cur.fetchall()]


def delete_favorite(username: str, name: str | None = None, file_id: str | None = None) -> None:
    ensure_schema()
    username = (username or "").strip().upper()
    with _connect() as conn:
        with conn.cursor() as cur:
            if file_id:
                cur.execute("DELETE FROM drive_user_favorites WHERE username = %s AND file_id = %s", (username, file_id))
            else:
                cur.execute("DELETE FROM drive_user_favorites WHERE username = %s AND name = %s", (username, name))
            if cur.rowcount == 0:
                raise DriveMetadataError("Favorito nao encontrado.")


def add_preset(username: str, file_id: str, account_id: str, name: str, source_uri: str) -> dict[str, Any]:
    ensure_schema()
    username = (username or "").strip().upper()
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute(
                """
                INSERT INTO drive_user_presets(username, file_id, account_id, name, source_uri)
                VALUES(%s, %s, %s, %s, %s)
                ON CONFLICT(username, file_id) DO UPDATE SET
                    account_id = EXCLUDED.account_id,
                    name = EXCLUDED.name,
                    source_uri = EXCLUDED.source_uri
                RETURNING file_id AS id, file_id, account_id, name, source_uri AS path, created_at
                """,
                (username, file_id, account_id, name, source_uri),
            )
            return dict(cur.fetchone())


def list_presets(username: str) -> list[dict[str, Any]]:
    ensure_schema()
    username = (username or "").strip().upper()
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:  # type: ignore[union-attr]
            cur.execute(
                """
                SELECT file_id AS id, file_id, account_id, name, source_uri AS path, created_at
                FROM drive_user_presets
                WHERE username = %s
                ORDER BY name ASC
                """,
                (username,),
            )
            return [dict(row) for row in cur.fetchall()]


def delete_preset(username: str, preset_id_or_file_id: Any) -> None:
    ensure_schema()
    username = (username or "").strip().upper()
    value = str(preset_id_or_file_id or "").strip()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM drive_user_presets WHERE username = %s AND file_id = %s", (username, value))
            if cur.rowcount == 0:
                raise DriveMetadataError("Pregao nao encontrado.")
