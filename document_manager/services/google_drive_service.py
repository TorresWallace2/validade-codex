"""Google Drive integration with persisted multi-account support."""
from __future__ import annotations

from collections import OrderedDict
import csv
from datetime import date, datetime
import hashlib
import io
import json
import os
import re
import time
from threading import RLock
from typing import Any, Optional, Sequence
from urllib.parse import parse_qs, quote, unquote, urlsplit
from uuid import uuid4

from flask import current_app, session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from .. import db
from ..status import (
    compute_status,
    format_display_date,
    normalise_validity_input,
    parse_validity_date,
)
from . import drive_metadata_service as metadata_svc

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
DRIVE_PREFIX = "gdrive://"
ROOT_PATH = "gdrive://root"
DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
LIST_CACHE_TTL_SECONDS = int(os.environ.get("GOOGLE_DRIVE_LIST_CACHE_TTL", "60"))
FOLDER_META_CACHE_TTL_SECONDS = int(os.environ.get("GOOGLE_DRIVE_FOLDER_META_CACHE_TTL", "300"))
PAGE_TOKENS_CACHE_TTL_SECONDS = int(os.environ.get("GOOGLE_DRIVE_PAGE_TOKENS_TTL", "300"))
SERVER_CACHE_MAX_ENTRIES = int(os.environ.get("GOOGLE_DRIVE_SERVER_CACHE_MAX_ENTRIES", "512"))
VALIDITY_IN_FILENAME_RE = re.compile(
    r"\bVAL(?:IDADE)?\.?\s*([0-3]?\d[\/\-.][0-1]?\d[\/\-.]\d{4})\b",
    re.IGNORECASE,
)

_SERVER_CACHE_LOCK = RLock()
_SERVER_CACHE: "OrderedDict[str, dict[str, Any]]" = OrderedDict()


class GoogleDriveError(Exception):
    """Raised when the Google Drive integration cannot complete an operation."""


def _log_metadata_warning(action: str, exc: Exception) -> None:
    try:
        current_app.logger.warning("google_drive metadata fallback during %s: %s", action, exc)
    except Exception:
        pass


def _redirect_uri() -> str:
    return os.environ.get("GOOGLE_REDIRECT_URI", "").strip()


def _client_config() -> dict[str, Any]:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    redirect_uri = _redirect_uri()
    if not client_id or not client_secret or not redirect_uri:
        raise GoogleDriveError(
            "Google Drive nao configurado. Configure GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET e GOOGLE_REDIRECT_URI."
        )
    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [redirect_uri],
        }
    }


def _utcnow_iso() -> str:
    return datetime.utcnow().strftime(db.ISO_FORMAT)


def _current_username() -> str:
    user = session.get("user") or {}
    username = str(user.get("username") or "").strip().upper()
    if not username:
        raise GoogleDriveError("Autenticacao requerida.")
    return username


def _current_user_id() -> int:
    username = _current_username()
    rows = db.query("SELECT id FROM users WHERE username = ?", (username,))
    if not rows:
        raise GoogleDriveError("Usuario nao encontrado.")
    return int(rows[0]["id"])


def create_flow() -> Flow:
    if current_app.debug:
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    return Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=_redirect_uri())


def authorization_url(intent: str = "connect_new") -> str:
    flow = create_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["google_oauth_state"] = state
    session["google_oauth_intent"] = intent
    code_verifier = getattr(flow, "code_verifier", None)
    if code_verifier:
        session["google_oauth_code_verifier"] = code_verifier
    return auth_url


def _credentials_to_dict(credentials: Credentials) -> dict[str, Any]:
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }


def _credentials_from_dict(data: dict[str, Any] | None) -> Credentials | None:
    if not data:
        return None
    granted_scopes = set(data.get("scopes") or [])
    if not set(SCOPES).issubset(granted_scopes):
        raise GoogleDriveError("Permissoes do Google Drive desatualizadas. Reconecte a conta.")
    return Credentials(**data)


def _row_to_account(row: Any) -> dict[str, Any]:
    return {
        "account_id": str(row["id"]),
        "google_email": row["google_email"],
        "google_name": row["google_name"] or "",
        "google_permission_id": row["google_permission_id"] or "",
        "status": row["status"] or "disconnected",
        "connected": (row["status"] or "") == "connected" and bool(row["credentials_json"]),
        "is_active": bool(row["is_active"]),
        "root_path": root_path_for(str(row["id"])),
        "label": account_label({"google_email": row["google_email"], "google_name": row["google_name"]}),
        "last_connected_at": row["last_connected_at"],
        "last_used_at": row["last_used_at"],
    }


def _load_accounts() -> list[dict[str, Any]]:
    rows = db.query(
        """
        SELECT id, google_email, google_name, google_permission_id, status, is_active, credentials_json,
               created_at, updated_at, last_connected_at, last_used_at
        FROM drive_accounts
        WHERE user_id = ?
        ORDER BY is_active DESC, google_email ASC, id ASC
        """,
        (_current_user_id(),),
    )
    return [_row_to_account(row) for row in rows]


def list_accounts() -> list[dict[str, Any]]:
    return _load_accounts()


def accounts_status() -> dict[str, Any]:
    accounts = list_accounts()
    active = next((account for account in accounts if account.get("is_active")), None)
    return {
        "connected": any(account.get("connected") for account in accounts),
        "accounts": accounts,
        "active_account": active,
        "default_root_path": active.get("root_path") if active else None,
    }


def account_label(account: dict[str, Any] | None) -> str:
    if not account:
        return "Google Drive"
    name = str(account.get("google_name") or "").strip()
    email = str(account.get("google_email") or "").strip()
    if name and email and name.lower() != email.lower():
        return f"{name} ({email})"
    return email or name or "Google Drive"


def _set_only_active(account_id: str) -> None:
    user_id = _current_user_id()
    db.execute("UPDATE drive_accounts SET is_active = 0 WHERE user_id = ?", (user_id,))
    db.execute(
        "UPDATE drive_accounts SET is_active = 1, updated_at = ?, last_used_at = ? WHERE user_id = ? AND id = ?",
        (_utcnow_iso(), _utcnow_iso(), user_id, int(account_id)),
    )


def get_account(account_id: str, *, require_connected: bool = False) -> dict[str, Any]:
    rows = db.query(
        """
        SELECT id, google_email, google_name, google_permission_id, status, is_active, credentials_json,
               created_at, updated_at, last_connected_at, last_used_at
        FROM drive_accounts
        WHERE user_id = ? AND id = ?
        """,
        (_current_user_id(), int(account_id)),
    )
    if not rows:
        raise GoogleDriveError("Conta Google Drive nao encontrada.")
    row = rows[0]
    data = dict(row)
    data["account_id"] = str(row["id"])
    data["label"] = account_label({"google_email": row["google_email"], "google_name": row["google_name"]})
    data["root_path"] = root_path_for(str(row["id"]))
    data["connected"] = (row["status"] or "") == "connected" and bool(row["credentials_json"])
    if require_connected and not data["connected"]:
        raise GoogleDriveError("Conta Google Drive desconectada. Reconecte a conta para continuar.")
    return data


def get_active_account(*, require_connected: bool = False) -> dict[str, Any] | None:
    rows = db.query(
        """
        SELECT id, google_email, google_name, google_permission_id, status, is_active, credentials_json,
               created_at, updated_at, last_connected_at, last_used_at
        FROM drive_accounts
        WHERE user_id = ? AND is_active = 1
        ORDER BY id DESC
        LIMIT 1
        """,
        (_current_user_id(),),
    )
    if not rows:
        return None
    account = get_account(str(rows[0]["id"]), require_connected=require_connected)
    return account


def activate_account(account_id: str) -> dict[str, Any]:
    account = get_account(account_id)
    _set_only_active(account_id)
    return get_account(account_id)


def is_connected(account_id: str | None = None) -> bool:
    if account_id:
        return bool(get_account(account_id).get("connected"))
    active = get_active_account()
    return bool(active and active.get("connected"))


def disconnect(account_id: str | None = None) -> None:
    target = account_id
    if not target:
        active = get_active_account()
        target = active["account_id"] if active else None
    if not target:
        return
    db.execute(
        """
        UPDATE drive_accounts
        SET status = 'disconnected',
            is_active = 0,
            credentials_json = NULL,
            updated_at = ?
        WHERE user_id = ? AND id = ?
        """,
        (_utcnow_iso(), _current_user_id(), int(target)),
    )
    _clear_drive_session_caches(target)


def root_path_for(account_id: str) -> str:
    return f"{DRIVE_PREFIX}{quote(str(account_id), safe='')}/root"


def _normalize_legacy_drive_path(path: str | None) -> tuple[str | None, str]:
    if not path or path == ROOT_PATH:
        active = get_active_account()
        if not active:
            raise GoogleDriveError("Nenhuma conta Google Drive ativa.")
        return active["account_id"], "root"
    raw = str(path).strip()
    if not raw.startswith(DRIVE_PREFIX):
        raise GoogleDriveError("Caminho do Google Drive invalido.")
    payload = unquote(raw[len(DRIVE_PREFIX):])
    if "/" in payload:
        account_id, file_id = payload.split("/", 1)
        return account_id or None, file_id or "root"
    active = get_active_account()
    if not active:
        raise GoogleDriveError("Caminho legado do Google Drive sem conta ativa vinculada.")
    return active["account_id"], payload or "root"


def parse_drive_path(path: str | None) -> tuple[str, str]:
    account_id, file_id = _normalize_legacy_drive_path(path)
    if not account_id:
        raise GoogleDriveError("Conta Google Drive nao resolvida para o caminho informado.")
    return str(account_id), file_id or "root"


def is_drive_path(path: str | None) -> bool:
    return bool(path and str(path).startswith(DRIVE_PREFIX))


def path_to_account_id(path: str | None) -> str:
    account_id, _ = parse_drive_path(path)
    return account_id


def path_to_id(path: str | None) -> str:
    _, file_id = parse_drive_path(path)
    return file_id or "root"


def extract_file_id(path: str | None) -> str:
    return path_to_id(path)


def id_to_path(file_id: str, account_id: str | None = None) -> str:
    resolved_account_id = str(account_id or path_to_account_id(ROOT_PATH))
    return f"{DRIVE_PREFIX}{quote(resolved_account_id, safe='')}/{quote(file_id or 'root', safe='')}"


def _metadata_file_id(account_id: str, file_id: str) -> str:
    return f"{account_id}:{file_id or 'root'}"


def metadata_file_id_for_path(path: str) -> str:
    account_id, file_id = parse_drive_path(path)
    return _metadata_file_id(account_id, file_id)


def build_service_for_account(account_id: str):
    account = get_account(account_id, require_connected=True)
    credentials_json = account.get("credentials_json")
    if not credentials_json:
        raise GoogleDriveError("Conta Google Drive desconectada.")
    credentials = _credentials_from_dict(json.loads(credentials_json))
    if not credentials:
        raise GoogleDriveError("Credenciais do Google Drive indisponiveis.")
    db.execute(
        "UPDATE drive_accounts SET last_used_at = ?, updated_at = ? WHERE user_id = ? AND id = ?",
        (_utcnow_iso(), _utcnow_iso(), _current_user_id(), int(account_id)),
    )
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _fetch_google_profile(credentials: Credentials) -> dict[str, str]:
    try:
        drive = build("drive", "v3", credentials=credentials, cache_discovery=False)
        about = drive.about().get(fields="user(displayName,emailAddress,permissionId)").execute()
        user = about.get("user") or {}
    except Exception as exc:
        raise GoogleDriveError(f"Falha ao identificar a conta Google autorizada: {exc}") from exc
    email = str(user.get("emailAddress") or "").strip()
    permission_id = str(user.get("permissionId") or "").strip()
    display_name = str(user.get("displayName") or "").strip()
    if not email:
        raise GoogleDriveError("Nao foi possivel identificar o email da conta Google autorizada.")
    return {
        "email": email,
        "name": display_name or email,
        "permission_id": permission_id,
    }


def finish_authorization(authorization_response: str, state: str | None) -> dict[str, Any]:
    expected_state = session.get("google_oauth_state")
    if not expected_state or state != expected_state:
        raise GoogleDriveError("Sessao OAuth invalida. Tente conectar novamente.")

    callback_query = parse_qs(urlsplit(authorization_response).query)
    callback_error = (callback_query.get("error") or [None])[0]
    if callback_error:
        callback_description = (callback_query.get("error_description") or [""])[0]
        safe_description = callback_description.replace("+", " ").strip()
        details = f"{callback_error}: {safe_description}" if safe_description else callback_error
        raise GoogleDriveError(f"Autorizacao Google recusada ou invalida ({details}).")

    flow = create_flow()
    code_verifier = session.get("google_oauth_code_verifier")
    if code_verifier:
        flow.code_verifier = code_verifier

    old_relax_scope = os.environ.get("OAUTHLIB_RELAX_TOKEN_SCOPE")
    os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = "1"
    try:
        flow.fetch_token(authorization_response=authorization_response)
    except Exception as exc:
        raise GoogleDriveError(f"Falha ao concluir autorizacao do Google Drive: {exc}") from exc
    finally:
        if old_relax_scope is None:
            os.environ.pop("OAUTHLIB_RELAX_TOKEN_SCOPE", None)
        else:
            os.environ["OAUTHLIB_RELAX_TOKEN_SCOPE"] = old_relax_scope

    credentials = flow.credentials
    if not credentials or not credentials.token:
        raise GoogleDriveError("Falha ao concluir autorizacao do Google Drive. Tente conectar novamente.")

    profile = _fetch_google_profile(credentials)
    intent = str(session.get("google_oauth_intent") or "connect_new")
    user_id = _current_user_id()
    now = _utcnow_iso()
    credentials_json = json.dumps(_credentials_to_dict(credentials), ensure_ascii=False)
    rows = db.query(
        """
        SELECT id
        FROM drive_accounts
        WHERE user_id = ? AND (google_permission_id = ? OR google_email = ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id, profile["permission_id"], profile["email"]),
    )
    if rows:
        account_id = str(rows[0]["id"])
        db.execute(
            """
            UPDATE drive_accounts
            SET google_email = ?, google_name = ?, google_permission_id = ?, status = 'connected',
                credentials_json = ?, updated_at = ?, last_connected_at = ?, last_used_at = ?
            WHERE user_id = ? AND id = ?
            """,
            (
                profile["email"],
                profile["name"],
                profile["permission_id"] or None,
                credentials_json,
                now,
                now,
                now,
                user_id,
                int(account_id),
            ),
        )
    else:
        db.execute(
            """
            INSERT INTO drive_accounts(
                user_id, google_email, google_name, google_permission_id, status, is_active,
                credentials_json, created_at, updated_at, last_connected_at, last_used_at
            )
            VALUES(?, ?, ?, ?, 'connected', 0, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                profile["email"],
                profile["name"],
                profile["permission_id"] or None,
                credentials_json,
                now,
                now,
                now,
                now,
            ),
        )
        account_id = str(
            db.query(
                "SELECT id FROM drive_accounts WHERE user_id = ? AND google_email = ? ORDER BY id DESC LIMIT 1",
                (user_id, profile["email"]),
            )[0]["id"]
        )

    if intent.startswith("reconnect:"):
        reconnect_target = intent.split(":", 1)[1].strip()
        if reconnect_target and reconnect_target != account_id:
            db.execute(
                """
                UPDATE drive_accounts
                SET google_email = ?, google_name = ?, google_permission_id = ?, status = 'connected',
                    credentials_json = ?, updated_at = ?, last_connected_at = ?, last_used_at = ?
                WHERE user_id = ? AND id = ?
                """,
                (
                    profile["email"],
                    profile["name"],
                    profile["permission_id"] or None,
                    credentials_json,
                    now,
                    now,
                    now,
                    user_id,
                    int(reconnect_target),
                ),
            )
            account_id = reconnect_target

    _set_only_active(account_id)
    session.pop("google_oauth_state", None)
    session.pop("google_oauth_code_verifier", None)
    session.pop("google_oauth_intent", None)
    _clear_drive_session_caches(account_id)
    return get_account(account_id, require_connected=True)


def connect_new_authorization_url() -> str:
    return authorization_url("connect_new")


def reconnect_authorization_url(account_id: str) -> str:
    get_account(account_id)
    return authorization_url(f"reconnect:{account_id}")


def _cache_owner_key(account_id: str) -> str:
    username = _current_username()
    account = get_account(account_id)
    credentials_json = account.get("credentials_json") or ""
    token_hash = hashlib.sha1(credentials_json.encode("utf-8")).hexdigest()[:12] if credentials_json else "no-token"
    return f"{username}:{account_id}:{token_hash}"


def _cache_owner_prefix(account_id: str | None = None) -> str:
    username = _current_username()
    if account_id:
        return f"{username}:{account_id}:"
    return f"{username}:"


def _server_cache_key(namespace: str, account_id: str, key: str) -> str:
    return f"{namespace}:{_cache_owner_key(account_id)}:{key}"


def _server_cache_get(namespace: str, account_id: str, key: str, ttl_seconds: int) -> Any | None:
    full_key = _server_cache_key(namespace, account_id, key)
    with _SERVER_CACHE_LOCK:
        entry = _SERVER_CACHE.get(full_key)
        if not entry:
            return None
        if int(time.time()) - int(entry.get("ts", 0)) > ttl_seconds:
            _SERVER_CACHE.pop(full_key, None)
            return None
        _SERVER_CACHE.move_to_end(full_key)
        return entry.get("value")


def _server_cache_set(namespace: str, account_id: str, key: str, value: Any) -> None:
    full_key = _server_cache_key(namespace, account_id, key)
    with _SERVER_CACHE_LOCK:
        _SERVER_CACHE[full_key] = {"ts": int(time.time()), "value": value}
        _SERVER_CACHE.move_to_end(full_key)
        while len(_SERVER_CACHE) > SERVER_CACHE_MAX_ENTRIES:
            _SERVER_CACHE.popitem(last=False)


def _server_cache_clear_namespace(namespace: str, account_id: str | None = None) -> None:
    prefix = f"{namespace}:{_cache_owner_prefix(account_id)}"
    with _SERVER_CACHE_LOCK:
        for key in [item_key for item_key in _SERVER_CACHE.keys() if item_key.startswith(prefix)]:
            _SERVER_CACHE.pop(key, None)


def _estimate_session_size_bytes() -> int:
    try:
        serialized = json.dumps(dict(session), default=str, ensure_ascii=False)
    except Exception:
        serialized = str(dict(session))
    return len(serialized.encode("utf-8"))


def _clear_drive_session_caches(account_id: str | None = None) -> None:
    for namespace in (
        "google_drive_list_cache",
        "google_drive_folder_meta_cache",
        "google_drive_breadcrumb_cache",
        "google_drive_page_tokens",
    ):
        _server_cache_clear_namespace(namespace, account_id)


def _invalidate_drive_list_cache(account_id: str) -> None:
    _server_cache_clear_namespace("google_drive_list_cache", account_id)
    _server_cache_clear_namespace("google_drive_page_tokens", account_id)


def _perf_enabled() -> bool:
    return str(os.environ.get("APP_DEBUG_PERF", "")).strip().lower() in {"1", "true", "yes", "on"}


def _extract_validity_from_filename(filename: str) -> Optional[date]:
    match = VALIDITY_IN_FILENAME_RE.search(filename or "")
    if not match:
        return None
    raw_value = match.group(1).strip().replace(".", "-").replace("/", "-")
    parts = raw_value.split("-")
    if len(parts) != 3:
        return None
    day_str, month_str, year_str = parts
    if len(year_str) != 4:
        return None
    try:
        return date(int(year_str), int(month_str), int(day_str))
    except ValueError:
        return None


def _format_modified(value: str | None) -> str:
    if not value:
        return "--"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return value


def _format_size(value: str | int | None) -> str:
    if value in (None, ""):
        return "--"
    size = int(value)
    units = ["B", "KB", "MB", "GB", "TB"]
    amount = float(size)
    index = 0
    while amount >= 1024 and index < len(units) - 1:
        amount /= 1024
        index += 1
    if index == 0:
        return f"{int(amount)} {units[index]}"
    return f"{amount:.1f} {units[index]}"


def _default_warning_days(meta: dict[str, Any] | None = None) -> int:
    if meta and meta.get("warning_days"):
        return int(meta["warning_days"])
    return int(current_app.config["APP_CONFIG"].warning_days)


def _validity_display(validity_type: str, validity_date: date | str | None) -> str:
    if validity_type == "indeterminate":
        return "Indeterminada"
    if validity_type == "not_defined":
        return "Nao definido"
    if isinstance(validity_date, str):
        validity_date = parse_validity_date(validity_date)
    return format_display_date(validity_date)


def _status_dict(meta: dict[str, Any] | None) -> dict[str, Any]:
    validity_type = (meta or {}).get("validity_type") or "not_defined"
    validity_date = (meta or {}).get("validity_date")
    if isinstance(validity_date, str):
        validity_date = parse_validity_date(validity_date)
    status = compute_status(validity_type, validity_date, _default_warning_days(meta))
    return {
        "code": status.code,
        "key": status.code,
        "label": status.label,
        "icon": status.icon,
        "color": status.color,
    }


def _item_to_dict(item: dict[str, Any], account: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    is_folder = item.get("mimeType") == DRIVE_FOLDER_MIME_TYPE
    file_id = str(item.get("id") or "root")
    path = id_to_path(file_id, account["account_id"])
    meta = meta or {}
    validity_type = meta.get("validity_type") or "not_defined"
    validity_date = meta.get("validity_date")
    return {
        "name": item.get("name", "Sem nome"),
        "path": path,
        "type": "directory" if is_folder else "file",
        "size": "--" if is_folder else _format_size(item.get("size")),
        "modified": _format_modified(item.get("modifiedTime")),
        "validity": _validity_display(validity_type, validity_date),
        "validity_type": validity_type,
        "validity_source": meta.get("validity_source", "not_defined"),
        "auto_detected_date": format_display_date(meta.get("auto_detected_date")) if meta.get("auto_detected_date") else None,
        "manual_locked": bool(meta.get("manual_locked")),
        "status": _status_dict(meta),
        "icon": "bi bi-folder-fill" if is_folder else "bi bi-file-earmark-text",
        "drive_id": file_id,
        "mime_type": item.get("mimeType"),
        "web_url": item.get("webViewLink"),
        "notes": meta.get("notes", ""),
        "is_favorite": bool(meta.get("is_favorite")),
        "auctions": meta.get("auctions", ""),
        "pregoes": meta.get("auctions", ""),
        "source": "google_drive",
        "account_id": account["account_id"],
        "account_label": account["label"],
        "account_status": "connected" if account.get("connected") else "disconnected",
    }


def _folder_metadata(account: dict[str, Any], file_id: str) -> dict[str, Any]:
    if file_id == "root":
        return {"id": "root", "name": account["label"], "parents": []}
    cached = _server_cache_get("google_drive_folder_meta_cache", account["account_id"], file_id, FOLDER_META_CACHE_TTL_SECONDS)
    if cached:
        return cached
    drive = build_service_for_account(account["account_id"])
    item = drive.files().get(fileId=file_id, fields="id,name,parents", supportsAllDrives=True).execute()
    _server_cache_set("google_drive_folder_meta_cache", account["account_id"], file_id, item)
    return item


def _parent_path_for(account: dict[str, Any], file_id: str) -> str:
    if file_id == "root":
        return root_path_for(account["account_id"])
    try:
        current = _folder_metadata(account, file_id)
        parents = current.get("parents") or []
        if parents:
            return id_to_path(parents[0], account["account_id"])
    except HttpError:
        pass
    return root_path_for(account["account_id"])


def _breadcrumbs_for(account: dict[str, Any], file_id: str) -> list[dict[str, str]]:
    cached = _server_cache_get("google_drive_breadcrumb_cache", account["account_id"], file_id, FOLDER_META_CACHE_TTL_SECONDS)
    if cached:
        return cached
    breadcrumbs = [{"label": account["label"], "path": root_path_for(account["account_id"])}]
    if file_id == "root":
        _server_cache_set("google_drive_breadcrumb_cache", account["account_id"], file_id, breadcrumbs)
        return breadcrumbs
    try:
        current = _folder_metadata(account, file_id)
        chain = []
        guard = 0
        while current and current.get("id") != "root" and guard < 40:
            chain.append({"label": current.get("name", "Sem nome"), "path": id_to_path(current["id"], account["account_id"])})
            parents = current.get("parents") or []
            if not parents or parents[0] == "root":
                break
            current = _folder_metadata(account, parents[0])
            guard += 1
        breadcrumbs.extend(reversed(chain))
    except HttpError:
        breadcrumbs.append({"label": file_id, "path": id_to_path(file_id, account["account_id"])})
    _server_cache_set("google_drive_breadcrumb_cache", account["account_id"], file_id, breadcrumbs)
    return breadcrumbs


def _display_path_from_breadcrumbs(breadcrumbs: Sequence[dict[str, str]] | None) -> str:
    if not breadcrumbs:
        return "Google Drive"
    cleaned = [str(crumb.get("label") or "").strip() for crumb in breadcrumbs if str(crumb.get("label") or "").strip()]
    return " / ".join(cleaned) if cleaned else "Google Drive"


def _metadata_for_list_items(account: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ids = [_metadata_file_id(account["account_id"], item["id"]) for item in items]
    try:
        metadata_map = metadata_svc.get_metadata_map(ids) if ids else {}
    except metadata_svc.DriveMetadataError as exc:
        _log_metadata_warning("list_items", exc)
        metadata_map = {}
    for item in items:
        item_id = _metadata_file_id(account["account_id"], item["id"])
        if item.get("mimeType") == DRIVE_FOLDER_MIME_TYPE or item_id in metadata_map:
            continue
        inferred = _extract_validity_from_filename(item.get("name", ""))
        if inferred is not None:
            metadata_map[item_id] = {
                "validity_type": "defined",
                "validity_date": inferred,
                "validity_source": "filename",
                "auto_detected_date": inferred,
                "manual_locked": False,
                "warning_days": None,
                "notes": "",
                "is_favorite": False,
                "auctions": "",
            }
    return metadata_map


def _apply_metadata_to_items(account: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ids = [_metadata_file_id(account["account_id"], item["id"]) for item in items]
    try:
        metadata_map = metadata_svc.get_metadata_map(ids) if ids else {}
    except metadata_svc.DriveMetadataError as exc:
        _log_metadata_warning("details", exc)
        metadata_map = {}
        for item in items:
            meta_id = _metadata_file_id(account["account_id"], item["id"])
            is_folder = item.get("mimeType") == DRIVE_FOLDER_MIME_TYPE
            inferred = None if is_folder else _extract_validity_from_filename(item.get("name", ""))
            metadata_map[meta_id] = {
                "validity_type": "defined" if inferred is not None else "not_defined",
                "validity_date": inferred,
                "validity_source": "filename" if inferred is not None else "not_defined",
                "auto_detected_date": inferred,
                "manual_locked": False,
                "warning_days": None,
                "notes": "",
                "is_favorite": False,
                "auctions": "",
            }
        return metadata_map
    for item in items:
        meta_id = _metadata_file_id(account["account_id"], item["id"])
        is_folder = item.get("mimeType") == DRIVE_FOLDER_MIME_TYPE
        inferred = None if is_folder else _extract_validity_from_filename(item.get("name", ""))
        if inferred is not None or meta_id not in metadata_map:
            metadata_map[meta_id] = metadata_svc.apply_auto_validity_from_filename(
                meta_id,
                item.get("name", "Sem nome"),
                inferred,
                source_uri=id_to_path(item["id"], account["account_id"]),
                mime_type=item.get("mimeType", ""),
                web_url=item.get("webViewLink", ""),
            )
        else:
            metadata_svc.touch_file(
                meta_id,
                file_name=item.get("name", "Sem nome"),
                source_uri=id_to_path(item["id"], account["account_id"]),
                mime_type=item.get("mimeType", ""),
                web_url=item.get("webViewLink", ""),
            )
    return metadata_map


def _resolve_status_filter(status_filter: Sequence[str] | None) -> set[str]:
    if not status_filter:
        return set()
    return {str(code).strip().lower() for code in status_filter if str(code).strip()}


def _resolve_account_for_path(path: str | None, *, require_connected: bool = True) -> tuple[dict[str, Any], str]:
    account_id, file_id = parse_drive_path(path)
    account = get_account(account_id, require_connected=require_connected)
    if not account.get("is_active"):
        _set_only_active(account_id)
        account = get_account(account_id, require_connected=require_connected)
    return account, file_id


def list_items(
    path: str | None,
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    status_filter: Sequence[str] | None = None,
) -> dict[str, Any]:
    account, file_id = _resolve_account_for_path(path or ROOT_PATH)
    started_at = time.perf_counter()
    request_id = uuid4().hex[:10]
    safe_page_size = min(max(int(page_size or 50), 1), 200)
    safe_page = max(int(page or 1), 1)
    search_text = (search or "").strip()
    status_allowed = _resolve_status_filter(status_filter)
    status_key = ",".join(sorted(status_allowed))
    cache_key = f"{file_id}:{search_text}:{status_key}:{safe_page}:{safe_page_size}"
    timings: dict[str, float] = {}
    cached = _server_cache_get("google_drive_list_cache", account["account_id"], cache_key, LIST_CACHE_TTL_SECONDS)
    if cached:
        payload = dict(cached)
        if _perf_enabled():
            perf_payload = dict(payload.get("perf") or {})
            perf_payload.update(
                {
                    "request_id": request_id,
                    "cache_hit": True,
                    "total_ms": round((time.perf_counter() - started_at) * 1000, 2),
                    "session_size_bytes_est": _estimate_session_size_bytes(),
                }
            )
            payload["perf"] = perf_payload
        else:
            payload.pop("perf", None)
        return payload

    drive = build_service_for_account(account["account_id"])
    query_parts = [f"'{file_id}' in parents", "trashed = false"]
    if search_text:
        safe_search = search_text.replace("'", "\\'")
        query_parts.append(f"name contains '{safe_search}'")
    query = " and ".join(query_parts)

    try:
        list_started = time.perf_counter()
        if status_allowed:
            raw_items = []
            next_token = None
            while True:
                response = drive.files().list(
                    q=query,
                    fields="nextPageToken, files(id,name,mimeType,size,modifiedTime,webViewLink,parents)",
                    orderBy="folder,name_natural",
                    pageSize=200,
                    pageToken=next_token,
                    includeItemsFromAllDrives=True,
                    supportsAllDrives=True,
                ).execute()
                raw_items.extend(response.get("files", []))
                next_token = response.get("nextPageToken")
                if not next_token:
                    break
        else:
            token_key = f"{file_id}:{search_text}:{safe_page_size}"
            tokens = _server_cache_get("google_drive_page_tokens", account["account_id"], token_key, PAGE_TOKENS_CACHE_TTL_SECONDS) or {}
            page_token = tokens.get(str(safe_page)) if safe_page > 1 else None
            response = drive.files().list(
                q=query,
                fields="nextPageToken, files(id,name,mimeType,size,modifiedTime,webViewLink,parents)",
                orderBy="folder,name_natural",
                pageSize=safe_page_size,
                pageToken=page_token,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
            ).execute()
            next_token = response.get("nextPageToken")
            if next_token:
                tokens[str(safe_page + 1)] = next_token
                _server_cache_set("google_drive_page_tokens", account["account_id"], token_key, tokens)
            raw_items = response.get("files", [])
        timings["drive_list_ms"] = round((time.perf_counter() - list_started) * 1000, 2)
    except HttpError as exc:
        raise GoogleDriveError(f"Erro ao listar Google Drive: {exc}") from exc

    metadata_started = time.perf_counter()
    metadata_map = _metadata_for_list_items(account, raw_items)
    items = [_item_to_dict(item, account, metadata_map.get(_metadata_file_id(account["account_id"], item["id"]))) for item in raw_items]
    timings["metadata_ms"] = round((time.perf_counter() - metadata_started) * 1000, 2)

    if status_allowed:
        items = [item for item in items if item.get("status", {}).get("code") in status_allowed]
        total_items = len(items)
        start = (safe_page - 1) * safe_page_size
        end = start + safe_page_size
        page_items = items[start:end]
        has_more = end < total_items
    else:
        total_items = len(items)
        page_items = items
        has_more = bool(next_token)

    breadcrumbs = _breadcrumbs_for(account, file_id)
    result = {
        "items": page_items,
        "current_path": id_to_path(file_id, account["account_id"]),
        "current_path_display": _display_path_from_breadcrumbs(breadcrumbs),
        "parent_path": _parent_path_for(account, file_id),
        "total": total_items,
        "page": safe_page,
        "page_size": safe_page_size,
        "has_more": has_more,
        "breadcrumbs": breadcrumbs,
        "source": "google_drive",
        "account_id": account["account_id"],
        "account_label": account["label"],
    }
    if _perf_enabled():
        timings["total_ms"] = round((time.perf_counter() - started_at) * 1000, 2)
        timings["session_size_bytes_est"] = _estimate_session_size_bytes()
        timings["request_id"] = request_id
        result["perf"] = timings
    _server_cache_set("google_drive_list_cache", account["account_id"], cache_key, dict(result))
    return result


def get_details(path: str) -> dict[str, Any]:
    account, file_id = _resolve_account_for_path(path)
    drive = build_service_for_account(account["account_id"])
    try:
        item = drive.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,modifiedTime,webViewLink,webContentLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Erro ao abrir detalhes do Google Drive: {exc}") from exc

    metadata_map = _apply_metadata_to_items(account, [item])
    meta = metadata_map.get(_metadata_file_id(account["account_id"], file_id), {})
    data = _item_to_dict(item, account, meta)
    parents = item.get("parents") or []
    if file_id == "root":
        detail_breadcrumbs = [{"label": account["label"], "path": root_path_for(account["account_id"])}]
    else:
        detail_breadcrumbs = (
            _breadcrumbs_for(account, parents[0]) if parents else [{"label": account["label"], "path": root_path_for(account["account_id"])}]
        )
        detail_breadcrumbs = [*detail_breadcrumbs, {"label": item.get("name", "Sem nome"), "path": id_to_path(file_id, account["account_id"])}]
    data.update(
        {
            "warning_days": _default_warning_days(meta),
            "notes": meta.get("notes", ""),
            "web_url": item.get("webViewLink"),
            "download_url": item.get("webContentLink"),
            "path_display": _display_path_from_breadcrumbs(detail_breadcrumbs),
        }
    )
    validity_date = meta.get("validity_date")
    if isinstance(validity_date, str):
        validity_date = parse_validity_date(validity_date)
    data["validity_days_remaining"] = (validity_date - date.today()).days if meta.get("validity_type") == "defined" and validity_date else None
    return data


def set_validity(path: str, validity_type: str, validity_value: str | None, warning_days: int | None) -> dict[str, Any]:
    account_id = path_to_account_id(path)
    file_id = metadata_file_id_for_path(path)
    validity_date = normalise_validity_input(validity_value) if validity_type == "defined" else None
    meta = metadata_svc.set_validity(file_id, validity_type, validity_date, warning_days)
    _invalidate_drive_list_cache(account_id)
    return {
        "status": _status_dict(meta),
        "validity": _validity_display(meta.get("validity_type", "not_defined"), meta.get("validity_date")),
        "validity_type": meta.get("validity_type", "not_defined"),
        "validity_source": meta.get("validity_source"),
        "manual_locked": bool(meta.get("manual_locked")),
    }


def set_notes(path: str, notes: str) -> dict[str, Any]:
    account_id = path_to_account_id(path)
    meta = metadata_svc.set_notes(metadata_file_id_for_path(path), notes)
    _invalidate_drive_list_cache(account_id)
    return {"notes": meta.get("notes", "")}


def set_auctions(path: str, auctions: str) -> dict[str, Any]:
    account_id = path_to_account_id(path)
    meta = metadata_svc.set_auctions(metadata_file_id_for_path(path), auctions)
    _invalidate_drive_list_cache(account_id)
    return {"auctions": meta.get("auctions", ""), "pregoes": meta.get("auctions", "")}


def export_directory_snapshot(path: str, sort_by: str = "name", sort_direction: str = "asc") -> tuple[str, bytes]:
    collected: list[dict[str, Any]] = []
    page = 1
    while True:
        result = list_items(path, page=page, page_size=200)
        collected.extend(result["items"])
        if not result.get("has_more"):
            break
        page += 1
    output = io.StringIO()
    writer = csv.writer(output, delimiter=";")
    writer.writerow(["Nome", "Caminho", "Tipo", "Validade", "Status", "Observacoes", "Favorito", "Pregoes", "Origem validade"])
    for item in collected:
        writer.writerow(
            [
                item.get("name", ""),
                item.get("path", ""),
                item.get("type", ""),
                item.get("validity", ""),
                item.get("status", {}).get("label", ""),
                item.get("notes", ""),
                "Sim" if item.get("is_favorite") else "Nao",
                item.get("auctions", ""),
                item.get("validity_source", ""),
            ]
        )
    csv_bytes = output.getvalue().encode("utf-8-sig")
    output.close()
    return f"relatorio_drive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv", csv_bytes


def create_folder(parent_path: str, name: str) -> dict[str, Any]:
    account, parent_id = _resolve_account_for_path(parent_path)
    drive = build_service_for_account(account["account_id"])
    try:
        item = drive.files().create(
            body={"name": name, "mimeType": DRIVE_FOLDER_MIME_TYPE, "parents": [parent_id]},
            fields="id,name,mimeType,modifiedTime,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Sem permissao ou falha ao criar pasta no Drive: {exc}") from exc
    metadata_svc.touch_file(
        _metadata_file_id(account["account_id"], item["id"]),
        file_name=item.get("name", name),
        source_uri=id_to_path(item["id"], account["account_id"]),
        mime_type=item.get("mimeType", ""),
        web_url=item.get("webViewLink", ""),
    )
    _clear_drive_session_caches(account["account_id"])
    return _item_to_dict(item, account, metadata_svc.get_metadata(_metadata_file_id(account["account_id"], item["id"])))


def create_file(parent_path: str, name: str) -> dict[str, Any]:
    account, parent_id = _resolve_account_for_path(parent_path)
    drive = build_service_for_account(account["account_id"])
    media = MediaIoBaseUpload(io.BytesIO(b""), mimetype="text/plain", resumable=False)
    try:
        item = drive.files().create(
            body={"name": name, "parents": [parent_id]},
            media_body=media,
            fields="id,name,mimeType,size,modifiedTime,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Sem permissao ou falha ao criar arquivo no Drive: {exc}") from exc
    metadata_svc.touch_file(
        _metadata_file_id(account["account_id"], item["id"]),
        file_name=item.get("name", name),
        source_uri=id_to_path(item["id"], account["account_id"]),
        mime_type=item.get("mimeType", ""),
        web_url=item.get("webViewLink", ""),
    )
    _clear_drive_session_caches(account["account_id"])
    return _item_to_dict(item, account, metadata_svc.get_metadata(_metadata_file_id(account["account_id"], item["id"])))


def upload_files(parent_path: str, files: list[Any]) -> list[dict[str, Any]]:
    account, parent_id = _resolve_account_for_path(parent_path)
    drive = build_service_for_account(account["account_id"])
    uploaded: list[dict[str, Any]] = []
    for uploaded_file in files:
        filename = getattr(uploaded_file, "filename", "") or "arquivo"
        stream = getattr(uploaded_file, "stream", uploaded_file)
        try:
            if hasattr(stream, "seek"):
                stream.seek(0)
            media = MediaIoBaseUpload(
                stream,
                mimetype=getattr(uploaded_file, "mimetype", None) or "application/octet-stream",
                resumable=False,
            )
            item = drive.files().create(
                body={"name": filename, "parents": [parent_id]},
                media_body=media,
                fields="id,name,mimeType,size,modifiedTime,webViewLink,parents",
                supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            raise GoogleDriveError(f"Sem permissao ou falha no upload para o Drive: {exc}") from exc
        inferred = _extract_validity_from_filename(filename)
        meta = metadata_svc.apply_auto_validity_from_filename(
            _metadata_file_id(account["account_id"], item["id"]),
            item.get("name", filename),
            inferred,
            source_uri=id_to_path(item["id"], account["account_id"]),
            mime_type=item.get("mimeType", ""),
            web_url=item.get("webViewLink", ""),
        )
        data = _item_to_dict(item, account, meta)
        data["auto_validity"] = inferred is not None
        uploaded.append(data)
    _clear_drive_session_caches(account["account_id"])
    return uploaded


def rename_item(path: str, new_name: str) -> dict[str, Any]:
    account, file_id = _resolve_account_for_path(path)
    drive = build_service_for_account(account["account_id"])
    try:
        item = drive.files().update(
            fileId=file_id,
            body={"name": new_name},
            fields="id,name,mimeType,size,modifiedTime,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Sem permissao ou falha ao renomear item do Drive: {exc}") from exc
    meta_id = _metadata_file_id(account["account_id"], file_id)
    inferred = None if item.get("mimeType") == DRIVE_FOLDER_MIME_TYPE else _extract_validity_from_filename(item.get("name", new_name))
    if inferred is not None:
        metadata_svc.apply_auto_validity_from_filename(
            meta_id,
            item.get("name", new_name),
            inferred,
            source_uri=id_to_path(file_id, account["account_id"]),
            mime_type=item.get("mimeType", ""),
            web_url=item.get("webViewLink", ""),
        )
    else:
        metadata_svc.touch_file(
            meta_id,
            file_name=item.get("name", new_name),
            source_uri=id_to_path(file_id, account["account_id"]),
            mime_type=item.get("mimeType", ""),
            web_url=item.get("webViewLink", ""),
        )
    _clear_drive_session_caches(account["account_id"])
    return {"path": id_to_path(file_id, account["account_id"]), "name": item.get("name", new_name)}


def delete_items(paths: list[str]) -> int:
    if not paths:
        return 0
    account_id = path_to_account_id(paths[0])
    drive = build_service_for_account(account_id)
    count = 0
    for path in paths:
        target_account_id, file_id = parse_drive_path(path)
        if target_account_id != account_id:
            raise GoogleDriveError("Nao misture contas Google Drive diferentes na mesma exclusao.")
        try:
            drive.files().update(fileId=file_id, body={"trashed": True}, supportsAllDrives=True).execute()
        except HttpError as exc:
            raise GoogleDriveError(f"Sem permissao ou falha ao excluir item do Drive: {exc}") from exc
        count += 1
    _clear_drive_session_caches(account_id)
    return count


def move_items(paths: list[str], destination: str) -> dict[str, Any]:
    account_id = path_to_account_id(destination)
    drive = build_service_for_account(account_id)
    dest_id = path_to_id(destination)
    moved: list[str] = []
    for path in paths:
        source_account_id, file_id = parse_drive_path(path)
        if source_account_id != account_id:
            raise GoogleDriveError("Mover entre contas Google Drive diferentes nao e suportado.")
        try:
            current = drive.files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
            previous_parents = ",".join(current.get("parents", []))
            drive.files().update(
                fileId=file_id,
                addParents=dest_id,
                removeParents=previous_parents,
                fields="id,parents",
                supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            raise GoogleDriveError(f"Sem permissao ou falha ao mover item do Drive: {exc}") from exc
        moved.append(id_to_path(file_id, account_id))
    _clear_drive_session_caches(account_id)
    return {"items": moved}


def _list_folder_children_for_copy(drive: Any, folder_id: str) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    page_token: str | None = None
    query = f"'{folder_id}' in parents and trashed = false"
    while True:
        response = drive.files().list(
            q=query,
            fields="nextPageToken, files(id,name,mimeType)",
            pageSize=1000,
            pageToken=page_token,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
        children.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return children


def _is_folder_inside(drive: Any, folder_id: str, possible_parent_id: str) -> bool:
    if not folder_id or folder_id == "root":
        return False
    if folder_id == possible_parent_id:
        return True
    current_id = folder_id
    visited: set[str] = set()
    while current_id and current_id != "root" and current_id not in visited:
        visited.add(current_id)
        current = drive.files().get(fileId=current_id, fields="id,parents", supportsAllDrives=True).execute()
        parents = current.get("parents", [])
        if possible_parent_id in parents:
            return True
        current_id = parents[0] if parents else ""
    return False


def _copy_drive_item_recursive(drive: Any, account_id: str, file_id: str, destination_id: str) -> str:
    original = drive.files().get(fileId=file_id, fields="id,name,mimeType", supportsAllDrives=True).execute()
    name = original.get("name") or "Sem nome"
    mime_type = original.get("mimeType") or ""
    if mime_type == DRIVE_FOLDER_MIME_TYPE:
        if _is_folder_inside(drive, destination_id, file_id):
            raise GoogleDriveError("Nao e possivel copiar uma pasta para dentro dela mesma ou de uma subpasta dela.")
        new_folder = drive.files().create(
            body={"name": name, "mimeType": DRIVE_FOLDER_MIME_TYPE, "parents": [destination_id]},
            fields="id,name,mimeType,modifiedTime,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
        metadata_svc.touch_file(
            _metadata_file_id(account_id, new_folder["id"]),
            file_name=new_folder.get("name", name),
            source_uri=id_to_path(new_folder["id"], account_id),
            mime_type=new_folder.get("mimeType", DRIVE_FOLDER_MIME_TYPE),
            web_url=new_folder.get("webViewLink", ""),
        )
        for child in _list_folder_children_for_copy(drive, file_id):
            _copy_drive_item_recursive(drive, account_id, child["id"], new_folder["id"])
        return id_to_path(new_folder["id"], account_id)
    new_item = drive.files().copy(
        fileId=file_id,
        body={"name": name, "parents": [destination_id]},
        fields="id,name,mimeType,size,modifiedTime,webViewLink,parents",
        supportsAllDrives=True,
    ).execute()
    metadata_svc.touch_file(
        _metadata_file_id(account_id, new_item["id"]),
        file_name=new_item.get("name", name),
        source_uri=id_to_path(new_item["id"], account_id),
        mime_type=new_item.get("mimeType", ""),
        web_url=new_item.get("webViewLink", ""),
    )
    return id_to_path(new_item["id"], account_id)


def copy_items(paths: list[str], destination: str) -> dict[str, Any]:
    account_id = path_to_account_id(destination)
    drive = build_service_for_account(account_id)
    dest_id = path_to_id(destination)
    copied: list[str] = []
    for path in paths:
        source_account_id, file_id = parse_drive_path(path)
        if source_account_id != account_id:
            raise GoogleDriveError("Copiar entre contas Google Drive diferentes nao e suportado.")
        try:
            copied.append(_copy_drive_item_recursive(drive, account_id, file_id, dest_id))
        except GoogleDriveError:
            raise
        except HttpError as exc:
            raise GoogleDriveError(f"Sem permissao ou falha ao copiar item do Drive: {exc}") from exc
    _clear_drive_session_caches(account_id)
    return {"items": copied}


def annotate_saved_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    accounts = {account["account_id"]: account for account in list_accounts()}
    active = get_active_account()
    enriched: list[dict[str, Any]] = []
    for item in items:
        current = dict(item)
        path = str(current.get("path") or "")
        account_id = str(current.get("account_id") or "").strip()
        if not account_id and is_drive_path(path):
            try:
                account_id = path_to_account_id(path)
            except GoogleDriveError:
                account_id = active["account_id"] if active else ""
        account = accounts.get(account_id) if account_id else None
        current["account_id"] = account_id or None
        current["account_label"] = account["label"] if account else "Conta nao vinculada"
        current["account_status"] = "connected" if account and account.get("connected") else "disconnected"
        current["source"] = "google_drive"
        enriched.append(current)
    return enriched
