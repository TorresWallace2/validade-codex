"""Google Drive integration helpers with PostgreSQL metadata persistence.

Performance notes:
- list_items() is intentionally read-only for metadata. It no longer writes/touches
  one row per file while browsing a Drive folder.
- Drive listing responses, folder parents and breadcrumbs are cached in-memory
  on each worker for a short period to avoid repeated Google Drive API calls
  while navigating.
"""
from __future__ import annotations

from collections import OrderedDict
import csv
import hashlib
import io
import json
import os
import re
import time
from threading import RLock
from datetime import date, datetime
from typing import Any, Sequence, Optional
from urllib.parse import parse_qs, quote, unquote, urlsplit
from uuid import uuid4

from flask import current_app, session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError

from ..status import (
    compute_status,
    format_display_date,
    normalise_validity_input,
    parse_validity_date,
)
from . import drive_metadata_service as metadata_svc

SCOPES = ["https://www.googleapis.com/auth/drive"]
DRIVE_PREFIX = "gdrive://"
ROOT_PATH = "gdrive://root"
DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

# Keep this short. It is enough to make navigation feel instant when users go
# back/forward, without keeping Drive data stale for too long.
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


def _redirect_uri() -> str:
    return os.environ.get("GOOGLE_REDIRECT_URI", "").strip()


def _client_config() -> dict[str, Any]:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    redirect_uri = _redirect_uri()
    if not client_id or not client_secret or not redirect_uri:
        raise GoogleDriveError(
            "Google Drive nao configurado. Configure GOOGLE_CLIENT_ID, "
            "GOOGLE_CLIENT_SECRET e GOOGLE_REDIRECT_URI no Render."
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


def create_flow() -> Flow:
    if current_app.debug:
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    return Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=_redirect_uri())


def authorization_url() -> str:
    flow = create_flow()
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["google_oauth_state"] = state
    code_verifier = getattr(flow, "code_verifier", None)
    if code_verifier:
        session["google_oauth_code_verifier"] = code_verifier
    return auth_url


def finish_authorization(authorization_response: str, state: str | None) -> None:
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

    # Google may return extra scopes already granted by the user. oauthlib raises
    # a Warning in this case and interrupts token parsing unless relaxed.
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
    session["google_drive_credentials"] = credentials_to_dict(credentials)
    session.pop("google_oauth_state", None)
    session.pop("google_oauth_code_verifier", None)
    _clear_drive_session_caches()


def credentials_to_dict(credentials: Credentials) -> dict[str, Any]:
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }


def credentials_from_session() -> Credentials | None:
    data = session.get("google_drive_credentials")
    if not data:
        return None

    granted_scopes = set(data.get("scopes") or [])
    required_scopes = set(SCOPES)
    if not required_scopes.issubset(granted_scopes):
        session.pop("google_drive_credentials", None)
        _clear_drive_session_caches()
        raise GoogleDriveError(
            "Permissoes do Google Drive desatualizadas. Desconecte e conecte novamente sua conta Google."
        )

    return Credentials(**data)


def is_connected() -> bool:
    return credentials_from_session() is not None


def disconnect() -> None:
    session.pop("google_drive_credentials", None)
    session.pop("google_oauth_state", None)
    _clear_drive_session_caches()


def service():
    credentials = credentials_from_session()
    if not credentials:
        raise GoogleDriveError("Google Drive nao conectado.")
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def is_drive_path(path: str | None) -> bool:
    return bool(path and str(path).startswith(DRIVE_PREFIX))


def id_to_path(file_id: str) -> str:
    return f"{DRIVE_PREFIX}{quote(file_id, safe='')}"


def path_to_id(path: str | None) -> str:
    if not path or path == ROOT_PATH:
        return "root"
    if not path.startswith(DRIVE_PREFIX):
        raise GoogleDriveError("Caminho do Google Drive invalido.")
    file_id = unquote(path[len(DRIVE_PREFIX) :])
    return file_id or "root"


def _now() -> int:
    return int(time.time())


def _perf_enabled() -> bool:
    return str(os.environ.get("APP_DEBUG_PERF", "")).strip().lower() in {"1", "true", "yes", "on"}


def _cache_owner_key() -> str:
    user = session.get("user") or {}
    username = str(user.get("username") or "anonymous").upper()
    credentials = session.get("google_drive_credentials") or {}
    token = str(credentials.get("token") or "")
    token_hash = hashlib.sha1(token.encode("utf-8")).hexdigest()[:12] if token else "no-token"
    return f"{username}:{token_hash}"


def _cache_owner_prefix() -> str:
    user = session.get("user") or {}
    username = str(user.get("username") or "anonymous").upper()
    return f"{username}:"


def _server_cache_key(namespace: str, key: str) -> str:
    return f"{namespace}:{_cache_owner_key()}:{key}"


def _server_cache_get(namespace: str, key: str, ttl_seconds: int) -> Any | None:
    full_key = _server_cache_key(namespace, key)
    with _SERVER_CACHE_LOCK:
        entry = _SERVER_CACHE.get(full_key)
        if not entry:
            return None
        if _now() - int(entry.get("ts", 0)) > ttl_seconds:
            _SERVER_CACHE.pop(full_key, None)
            return None
        _SERVER_CACHE.move_to_end(full_key)
        return entry.get("value")


def _server_cache_set(namespace: str, key: str, value: Any) -> None:
    full_key = _server_cache_key(namespace, key)
    with _SERVER_CACHE_LOCK:
        _SERVER_CACHE[full_key] = {"ts": _now(), "value": value}
        _SERVER_CACHE.move_to_end(full_key)
        while len(_SERVER_CACHE) > SERVER_CACHE_MAX_ENTRIES:
            _SERVER_CACHE.popitem(last=False)


def _server_cache_clear_namespace(namespace: str) -> None:
    prefix = f"{namespace}:{_cache_owner_prefix()}"
    with _SERVER_CACHE_LOCK:
        for key in [item_key for item_key in _SERVER_CACHE.keys() if item_key.startswith(prefix)]:
            _SERVER_CACHE.pop(key, None)


def _estimate_session_size_bytes() -> int:
    try:
        serialized = json.dumps(dict(session), default=str, ensure_ascii=False)
    except Exception:
        serialized = str(dict(session))
    return len(serialized.encode("utf-8"))


def _session_cache_get(namespace: str, key: str, ttl_seconds: int) -> Any | None:
    return _server_cache_get(namespace, key, ttl_seconds)


def _session_cache_set(namespace: str, key: str, value: Any) -> None:
    _server_cache_set(namespace, key, value)


def _clear_drive_session_caches() -> None:
    for namespace in (
        "google_drive_list_cache",
        "google_drive_folder_meta_cache",
        "google_drive_breadcrumb_cache",
        "google_drive_page_tokens",
    ):
        _server_cache_clear_namespace(namespace)


def _invalidate_drive_list_cache() -> None:
    _server_cache_clear_namespace("google_drive_list_cache")
    _server_cache_clear_namespace("google_drive_page_tokens")


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


def _item_to_dict(item: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    is_folder = item.get("mimeType") == DRIVE_FOLDER_MIME_TYPE
    path = id_to_path(item["id"])
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
        "auto_detected_date": format_display_date(meta.get("auto_detected_date"))
        if meta.get("auto_detected_date")
        else None,
        "manual_locked": bool(meta.get("manual_locked")),
        "status": _status_dict(meta),
        "icon": "bi bi-folder-fill" if is_folder else "bi bi-file-earmark-text",
        "drive_id": item.get("id"),
        "mime_type": item.get("mimeType"),
        "web_url": item.get("webViewLink"),
        "notes": meta.get("notes", ""),
        "is_favorite": bool(meta.get("is_favorite")),
        "auctions": meta.get("auctions", ""),
        "pregoes": meta.get("auctions", ""),
    }


def _folder_metadata(file_id: str) -> dict[str, Any]:
    """Return folder id/name/parents with session cache."""
    if file_id == "root":
        return {"id": "root", "name": "Google Drive", "parents": []}

    cached = _session_cache_get(
        "google_drive_folder_meta_cache",
        file_id,
        FOLDER_META_CACHE_TTL_SECONDS,
    )
    if cached:
        return cached

    drive = service()
    item = drive.files().get(
        fileId=file_id,
        fields="id,name,parents",
        supportsAllDrives=True,
    ).execute()
    _session_cache_set("google_drive_folder_meta_cache", file_id, item)
    return item


def _parent_path_for(file_id: str, raw_items: list[dict[str, Any]] | None = None) -> str:
    if file_id == "root":
        return ROOT_PATH

    # When opening a child folder, the item might already be available in a cached
    # previous listing. But if not, this does at most one cached files.get call.
    try:
        current = _folder_metadata(file_id)
        parents = current.get("parents") or []
        if parents:
            return id_to_path(parents[0])
    except HttpError:
        pass
    return ROOT_PATH


def _breadcrumbs_for(file_id: str) -> list[dict[str, str]]:
    cached = _session_cache_get(
        "google_drive_breadcrumb_cache",
        file_id,
        FOLDER_META_CACHE_TTL_SECONDS,
    )
    if cached:
        return cached

    breadcrumbs = [{"label": "Google Drive", "path": ROOT_PATH}]
    if file_id == "root":
        _session_cache_set("google_drive_breadcrumb_cache", file_id, breadcrumbs)
        return breadcrumbs

    try:
        current = _folder_metadata(file_id)
        chain = []
        guard = 0
        while current and current.get("id") != "root" and guard < 40:
            chain.append({"label": current.get("name", "Sem nome"), "path": id_to_path(current["id"])})
            parents = current.get("parents") or []
            if not parents or parents[0] == "root":
                break
            current = _folder_metadata(parents[0])
            guard += 1
        breadcrumbs.extend(reversed(chain))
    except HttpError:
        breadcrumbs.append({"label": file_id, "path": id_to_path(file_id)})

    _session_cache_set("google_drive_breadcrumb_cache", file_id, breadcrumbs)
    return breadcrumbs


def _display_path_from_breadcrumbs(breadcrumbs: Sequence[dict[str, str]] | None) -> str:
    if not breadcrumbs:
        return "Google Drive"
    labels = [str(crumb.get("label") or "").strip() for crumb in breadcrumbs]
    cleaned = [label for label in labels if label]
    return " / ".join(cleaned) if cleaned else "Google Drive"


def _metadata_for_list_items(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Read metadata only; never write during folder browsing.

    The previous implementation called apply_auto_validity_from_filename() or
    touch_file() for every listed item. That made browsing slow because opening a
    Drive folder could trigger dozens/hundreds of database writes. Here we infer
    the date in memory only when no saved metadata exists.
    """
    ids = [item["id"] for item in items]
    metadata_map = metadata_svc.get_metadata_map(ids) if ids else {}

    for item in items:
        item_id = item["id"]
        is_folder = item.get("mimeType") == DRIVE_FOLDER_MIME_TYPE
        if is_folder or item_id in metadata_map:
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


def _apply_metadata_to_items(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Write/touch metadata for explicit item operations, not for browsing."""
    ids = [item["id"] for item in items]
    metadata_map = metadata_svc.get_metadata_map(ids) if ids else {}

    for item in items:
        is_folder = item.get("mimeType") == DRIVE_FOLDER_MIME_TYPE
        inferred = None if is_folder else _extract_validity_from_filename(item.get("name", ""))
        if inferred is not None or item["id"] not in metadata_map:
            metadata_map[item["id"]] = metadata_svc.apply_auto_validity_from_filename(
                item["id"],
                item.get("name", "Sem nome"),
                inferred,
                source_uri=id_to_path(item["id"]),
                mime_type=item.get("mimeType", ""),
                web_url=item.get("webViewLink", ""),
            )
        else:
            metadata_svc.touch_file(
                item["id"],
                file_name=item.get("name", "Sem nome"),
                source_uri=id_to_path(item["id"]),
                mime_type=item.get("mimeType", ""),
                web_url=item.get("webViewLink", ""),
            )
    return metadata_map


def _resolve_status_filter(status_filter: Sequence[str] | None) -> set[str]:
    if not status_filter:
        return set()
    return {str(code).strip().lower() for code in status_filter if str(code).strip()}


def list_items(
    path: str | None,
    *,
    page: int = 1,
    page_size: int = 50,
    search: str | None = None,
    status_filter: Sequence[str] | None = None,
) -> dict[str, Any]:
    started_at = time.perf_counter()
    request_id = uuid4().hex[:10]
    file_id = path_to_id(path or ROOT_PATH)
    safe_page_size = min(max(int(page_size or 50), 1), 200)
    safe_page = max(int(page or 1), 1)
    search_text = (search or "").strip()
    status_allowed = _resolve_status_filter(status_filter)
    status_key = ",".join(sorted(status_allowed))
    cache_key = f"{file_id}:{search_text}:{status_key}:{safe_page}:{safe_page_size}"
    timings: dict[str, float] = {}
    cache_hit = False

    cached = _session_cache_get("google_drive_list_cache", cache_key, LIST_CACHE_TTL_SECONDS)
    if cached:
        cache_hit = True
        if _perf_enabled():
            perf_payload = dict(cached.get("perf") or {})
            perf_payload.update(
                {
                    "request_id": request_id,
                    "cache_hit": True,
                    "total_ms": round((time.perf_counter() - started_at) * 1000, 2),
                    "session_size_bytes_est": _estimate_session_size_bytes(),
                }
            )
            current_app.logger.info(
                "gdrive.list_items request_id=%s path=%s page=%s page_size=%s cache_hit=true total_ms=%.2f session_size_bytes_est=%s",
                request_id,
                file_id,
                safe_page,
                safe_page_size,
                perf_payload.get("total_ms", 0.0),
                perf_payload.get("session_size_bytes_est", 0),
            )
            payload = dict(cached)
            payload["perf"] = perf_payload
            return payload
        if "perf" in cached:
            payload = dict(cached)
            payload.pop("perf", None)
            return payload
        return cached

    drive = service()
    query_parts = [f"'{file_id}' in parents", "trashed = false"]
    if search_text:
        safe_search = search_text.replace("'", "\'")
        query_parts.append(f"name contains '{safe_search}'")
    query = " and ".join(query_parts)

    try:
        list_started = time.perf_counter()
        if status_allowed:
            # Status is stored in the app metadata, not in Google Drive itself.
            # Therefore we must fetch all files from the folder, apply the status
            # filter locally, and only then paginate the filtered result.
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
            tokens = _session_cache_get(
                "google_drive_page_tokens",
                token_key,
                PAGE_TOKENS_CACHE_TTL_SECONDS,
            ) or {}
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
                _session_cache_set("google_drive_page_tokens", token_key, tokens)
            raw_items = response.get("files", [])
        timings["drive_list_ms"] = round((time.perf_counter() - list_started) * 1000, 2)
    except HttpError as exc:
        raise GoogleDriveError(f"Erro ao listar Google Drive: {exc}") from exc

    metadata_started = time.perf_counter()
    metadata_map = _metadata_for_list_items(raw_items)
    items = [_item_to_dict(item, metadata_map.get(item["id"])) for item in raw_items]
    timings["metadata_ms"] = round((time.perf_counter() - metadata_started) * 1000, 2)

    filter_started = time.perf_counter()
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
    timings["filter_paginate_ms"] = round((time.perf_counter() - filter_started) * 1000, 2)

    breadcrumbs_started = time.perf_counter()
    parent_path = _parent_path_for(file_id, raw_items)
    breadcrumbs = _breadcrumbs_for(file_id)
    current_path_display = _display_path_from_breadcrumbs(breadcrumbs)
    timings["breadcrumbs_ms"] = round((time.perf_counter() - breadcrumbs_started) * 1000, 2)

    result = {
        "items": page_items,
        "current_path": id_to_path(file_id),
        "current_path_display": current_path_display,
        "parent_path": parent_path,
        "total": total_items,
        "page": safe_page,
        "page_size": safe_page_size,
        "has_more": has_more,
        "breadcrumbs": breadcrumbs,
        "source": "google_drive",
    }
    total_ms = round((time.perf_counter() - started_at) * 1000, 2)
    timings["total_ms"] = total_ms
    timings["session_size_bytes_est"] = _estimate_session_size_bytes()
    timings["request_id"] = request_id
    timings["cache_hit"] = cache_hit
    current_app.logger.info(
        "gdrive.list_items request_id=%s path=%s page=%s page_size=%s cache_hit=%s total_ms=%.2f drive_list_ms=%.2f metadata_ms=%.2f breadcrumbs_ms=%.2f session_size_bytes_est=%s",
        request_id,
        file_id,
        safe_page,
        safe_page_size,
        cache_hit,
        timings.get("total_ms", 0.0),
        timings.get("drive_list_ms", 0.0),
        timings.get("metadata_ms", 0.0),
        timings.get("breadcrumbs_ms", 0.0),
        timings.get("session_size_bytes_est", 0),
    )
    cache_result = dict(result)
    cache_result.pop("perf", None)
    _session_cache_set("google_drive_list_cache", cache_key, cache_result)
    if _perf_enabled():
        result["perf"] = timings
    return result

def get_details(path: str) -> dict[str, Any]:
    file_id = path_to_id(path)
    drive = service()
    try:
        item = drive.files().get(
            fileId=file_id,
            fields="id,name,mimeType,size,modifiedTime,webViewLink,webContentLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Erro ao abrir detalhes do Google Drive: {exc}") from exc

    metadata_map = _apply_metadata_to_items([item])
    meta = metadata_map.get(file_id, {})
    data = _item_to_dict(item, meta)
    parents = item.get("parents") or []
    if file_id == "root":
        detail_breadcrumbs = [{"label": "Google Drive", "path": ROOT_PATH}]
    else:
        detail_breadcrumbs = _breadcrumbs_for(parents[0]) if parents else [{"label": "Google Drive", "path": ROOT_PATH}]
        detail_breadcrumbs = [*detail_breadcrumbs, {"label": item.get("name", "Sem nome"), "path": id_to_path(file_id)}]
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
    if meta.get("validity_type") == "defined" and validity_date:
        data["validity_days_remaining"] = (validity_date - date.today()).days
    else:
        data["validity_days_remaining"] = None
    return data


def set_validity(
    path: str,
    validity_type: str,
    validity_value: str | None,
    warning_days: int | None,
) -> dict[str, Any]:
    file_id = path_to_id(path)
    validity_date = None
    if validity_type == "defined":
        validity_date = normalise_validity_input(validity_value)
    meta = metadata_svc.set_validity(file_id, validity_type, validity_date, warning_days)
    _invalidate_drive_list_cache()
    status = _status_dict(meta)
    return {
        "status": status,
        "validity": _validity_display(meta.get("validity_type", "not_defined"), meta.get("validity_date")),
        "validity_type": meta.get("validity_type", "not_defined"),
        "validity_source": meta.get("validity_source"),
        "manual_locked": bool(meta.get("manual_locked")),
    }


def set_notes(path: str, notes: str) -> dict[str, Any]:
    file_id = path_to_id(path)
    meta = metadata_svc.set_notes(file_id, notes)
    _invalidate_drive_list_cache()
    return {"notes": meta.get("notes", "")}


def set_auctions(path: str, auctions: str) -> dict[str, Any]:
    file_id = path_to_id(path)
    meta = metadata_svc.set_auctions(file_id, auctions)
    _invalidate_drive_list_cache()
    return {"auctions": meta.get("auctions", ""), "pregoes": meta.get("auctions", "")}


def export_directory_snapshot(
    path: str,
    sort_by: str = "name",
    sort_direction: str = "asc",
) -> tuple[str, bytes]:
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
    writer.writerow(
        ["Nome", "Caminho", "Tipo", "Validade", "Status", "Observacoes", "Favorito", "Pregoes", "Origem validade"]
    )
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
    filename = f"relatorio_drive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return filename, csv_bytes


def create_folder(parent_path: str, name: str) -> dict[str, Any]:
    parent_id = path_to_id(parent_path)
    drive = service()
    try:
        item = drive.files().create(
            body={"name": name, "mimeType": DRIVE_FOLDER_MIME_TYPE, "parents": [parent_id]},
            fields="id,name,mimeType,modifiedTime,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Sem permissao ou falha ao criar pasta no Drive: {exc}") from exc

    metadata_svc.touch_file(
        item["id"],
        file_name=item.get("name", name),
        source_uri=id_to_path(item["id"]),
        mime_type=item.get("mimeType", ""),
        web_url=item.get("webViewLink", ""),
    )
    _clear_drive_session_caches()
    return _item_to_dict(item, metadata_svc.get_metadata(item["id"]))


def create_file(parent_path: str, name: str) -> dict[str, Any]:
    parent_id = path_to_id(parent_path)
    drive = service()
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
        item["id"],
        file_name=item.get("name", name),
        source_uri=id_to_path(item["id"]),
        mime_type=item.get("mimeType", ""),
        web_url=item.get("webViewLink", ""),
    )
    _clear_drive_session_caches()
    return _item_to_dict(item, metadata_svc.get_metadata(item["id"]))


def upload_files(parent_path: str, files: list[Any]) -> list[dict[str, Any]]:
    parent_id = path_to_id(parent_path)
    drive = service()
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
            item["id"],
            item.get("name", filename),
            inferred,
            source_uri=id_to_path(item["id"]),
            mime_type=item.get("mimeType", ""),
            web_url=item.get("webViewLink", ""),
        )
        data = _item_to_dict(item, meta)
        data["auto_validity"] = inferred is not None
        uploaded.append(data)

    _clear_drive_session_caches()
    return uploaded


def rename_item(path: str, new_name: str) -> dict[str, Any]:
    file_id = path_to_id(path)
    drive = service()
    try:
        item = drive.files().update(
            fileId=file_id,
            body={"name": new_name},
            fields="id,name,mimeType,size,modifiedTime,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Sem permissao ou falha ao renomear item do Drive: {exc}") from exc

    metadata_svc.touch_file(
        file_id,
        file_name=item.get("name", new_name),
        source_uri=id_to_path(file_id),
        mime_type=item.get("mimeType", ""),
        web_url=item.get("webViewLink", ""),
    )
    _clear_drive_session_caches()
    return {"path": id_to_path(file_id), "name": item.get("name", new_name)}


def delete_items(paths: list[str]) -> int:
    drive = service()
    count = 0
    for path in paths:
        file_id = path_to_id(path)
        try:
            drive.files().update(fileId=file_id, body={"trashed": True}, supportsAllDrives=True).execute()
        except HttpError as exc:
            raise GoogleDriveError(f"Sem permissao ou falha ao excluir item do Drive: {exc}") from exc
        count += 1
    _clear_drive_session_caches()
    return count


def move_items(paths: list[str], destination: str) -> dict[str, Any]:
    drive = service()
    dest_id = path_to_id(destination)
    moved: list[str] = []
    for path in paths:
        file_id = path_to_id(path)
        try:
            current = drive.files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
            previous_parents = ",".join(current.get("parents", []))
            drive.files().update(
                fileId=file_id,
                addParents=dest_id,
                removeParents=previous_parents,
                fields="id, parents",
                supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            raise GoogleDriveError(f"Sem permissao ou falha ao mover item do Drive: {exc}") from exc
        moved.append(id_to_path(file_id))
    _clear_drive_session_caches()
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
        current = drive.files().get(
            fileId=current_id,
            fields="id,parents",
            supportsAllDrives=True,
        ).execute()
        parents = current.get("parents", [])
        if possible_parent_id in parents:
            return True
        current_id = parents[0] if parents else ""
    return False


def _copy_drive_item_recursive(drive: Any, file_id: str, destination_id: str) -> str:
    original = drive.files().get(
        fileId=file_id,
        fields="id,name,mimeType",
        supportsAllDrives=True,
    ).execute()

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
            new_folder["id"],
            file_name=new_folder.get("name", name),
            source_uri=id_to_path(new_folder["id"]),
            mime_type=new_folder.get("mimeType", DRIVE_FOLDER_MIME_TYPE),
            web_url=new_folder.get("webViewLink", ""),
        )

        for child in _list_folder_children_for_copy(drive, file_id):
            _copy_drive_item_recursive(drive, child["id"], new_folder["id"])

        return id_to_path(new_folder["id"])

    new_item = drive.files().copy(
        fileId=file_id,
        body={"name": name, "parents": [destination_id]},
        fields="id,name,mimeType,size,modifiedTime,webViewLink,parents",
        supportsAllDrives=True,
    ).execute()
    metadata_svc.touch_file(
        new_item["id"],
        file_name=new_item.get("name", name),
        source_uri=id_to_path(new_item["id"]),
        mime_type=new_item.get("mimeType", ""),
        web_url=new_item.get("webViewLink", ""),
    )
    return id_to_path(new_item["id"])


def copy_items(paths: list[str], destination: str) -> dict[str, Any]:
    drive = service()
    dest_id = path_to_id(destination)
    copied: list[str] = []
    for path in paths:
        file_id = path_to_id(path)
        try:
            copied.append(_copy_drive_item_recursive(drive, file_id, dest_id))
        except GoogleDriveError:
            raise
        except HttpError as exc:
            raise GoogleDriveError(f"Sem permissao ou falha ao copiar item do Drive: {exc}") from exc
    _clear_drive_session_caches()
    return {"items": copied}
