"""Google Drive integration helpers with PostgreSQL metadata persistence."""
from __future__ import annotations

import csv
import io
import os
import re
from datetime import date, datetime
from typing import Any, Optional
from urllib.parse import quote, unquote

from flask import current_app, session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..status import compute_status, format_display_date, normalise_validity_input, parse_validity_date
from . import drive_metadata_service as metadata_svc

SCOPES = ["https://www.googleapis.com/auth/drive"]
DRIVE_PREFIX = "gdrive://"
ROOT_PATH = "gdrive://root"

VALIDITY_IN_FILENAME_RE = re.compile(
    r"\bVAL(?:IDADE)?\.?\s*([0-3]?\d[\/\-.][0-1]?\d[\/\-.]\d{4})\b",
    re.IGNORECASE,
)


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
    flow = create_flow()
    code_verifier = session.get("google_oauth_code_verifier")
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    session["google_drive_credentials"] = credentials_to_dict(credentials)
    session.pop("google_oauth_state", None)
    session.pop("google_oauth_code_verifier", None)


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
    return Credentials(**data)


def is_connected() -> bool:
    return credentials_from_session() is not None


def disconnect() -> None:
    session.pop("google_drive_credentials", None)
    session.pop("google_oauth_state", None)


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
    return {"code": status.code, "key": status.code, "label": status.label, "icon": status.icon, "color": status.color}


def _item_to_dict(item: dict[str, Any], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    is_folder = item.get("mimeType") == "application/vnd.google-apps.folder"
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
        "auto_detected_date": format_display_date(meta.get("auto_detected_date")) if meta.get("auto_detected_date") else None,
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


def _breadcrumbs_for(file_id: str) -> list[dict[str, str]]:
    breadcrumbs = [{"label": "Google Drive", "path": ROOT_PATH}]
    if file_id == "root":
        return breadcrumbs
    try:
        drive = service()
        current = drive.files().get(fileId=file_id, fields="id,name,parents", supportsAllDrives=True).execute()
        chain = []
        guard = 0
        while current and current.get("id") != "root" and guard < 40:
            chain.append({"label": current.get("name", "Sem nome"), "path": id_to_path(current["id"])})
            parents = current.get("parents") or []
            if not parents:
                break
            parent_id = parents[0]
            if parent_id == "root":
                break
            current = drive.files().get(fileId=parent_id, fields="id,name,parents", supportsAllDrives=True).execute()
            guard += 1
        breadcrumbs.extend(reversed(chain))
    except HttpError:
        breadcrumbs.append({"label": file_id, "path": id_to_path(file_id)})
    return breadcrumbs


def _apply_metadata_to_items(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ids = [item["id"] for item in items]
    metadata_map = metadata_svc.get_metadata_map(ids)
    for item in items:
        is_folder = item.get("mimeType") == "application/vnd.google-apps.folder"
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


def list_items(path: str | None, *, page: int = 1, page_size: int = 50, search: str | None = None) -> dict[str, Any]:
    file_id = path_to_id(path or ROOT_PATH)
    drive = service()
    query_parts = [f"'{file_id}' in parents", "trashed = false"]
    if search:
        safe_search = search.replace("'", "\\'")
        query_parts.append(f"name contains '{safe_search}'")
    query = " and ".join(query_parts)
    token_key = f"google_drive_page_tokens:{file_id}:{search or ''}:{page_size}"
    tokens = session.get(token_key, {})
    page_token = tokens.get(str(page)) if page > 1 else None
    try:
        response = drive.files().list(
            q=query,
            fields="nextPageToken, files(id,name,mimeType,size,modifiedTime,webViewLink,parents)",
            orderBy="folder,name_natural",
            pageSize=min(max(page_size, 1), 200),
            pageToken=page_token,
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Erro ao listar Google Drive: {exc}") from exc

    next_token = response.get("nextPageToken")
    if next_token:
        tokens[str(page + 1)] = next_token
        session[token_key] = tokens

    raw_items = response.get("files", [])
    metadata_map = _apply_metadata_to_items(raw_items)
    items = [_item_to_dict(item, metadata_map.get(item["id"])) for item in raw_items]

    current_path = id_to_path(file_id)
    parent_path = ROOT_PATH
    if file_id != "root":
        try:
            current = drive.files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
            parents = current.get("parents") or []
            if parents:
                parent_path = id_to_path(parents[0])
        except HttpError:
            parent_path = ROOT_PATH
    return {
        "items": items,
        "current_path": current_path,
        "parent_path": parent_path,
        "total": len(items),
        "page": page,
        "page_size": page_size,
        "has_more": bool(next_token),
        "breadcrumbs": _breadcrumbs_for(file_id),
        "source": "google_drive",
    }


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
    data.update(
        {
            "warning_days": _default_warning_days(meta),
            "notes": meta.get("notes", ""),
            "web_url": item.get("webViewLink"),
            "download_url": item.get("webContentLink"),
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


def set_validity(path: str, validity_type: str, validity_value: str | None, warning_days: int | None) -> dict[str, Any]:
    file_id = path_to_id(path)
    validity_date = None
    if validity_type == "defined":
        validity_date = normalise_validity_input(validity_value)
    meta = metadata_svc.set_validity(file_id, validity_type, validity_date, warning_days)
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
    return {"notes": meta.get("notes", "")}


def set_auctions(path: str, auctions: str) -> dict[str, Any]:
    file_id = path_to_id(path)
    meta = metadata_svc.set_auctions(file_id, auctions)
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
    filename = f"relatorio_drive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return filename, csv_bytes


def create_folder(parent_path: str, name: str) -> dict[str, Any]:
    parent_id = path_to_id(parent_path)
    drive = service()
    try:
        item = drive.files().create(
            body={"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]},
            fields="id,name,mimeType,modifiedTime,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Sem permissao ou falha ao criar pasta no Drive: {exc}") from exc
    metadata_svc.touch_file(item["id"], file_name=item.get("name", name), source_uri=id_to_path(item["id"]), mime_type=item.get("mimeType", ""), web_url=item.get("webViewLink", ""))
    return _item_to_dict(item, metadata_svc.get_metadata(item["id"]))


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
    metadata_svc.touch_file(file_id, file_name=item.get("name", new_name), source_uri=id_to_path(file_id), mime_type=item.get("mimeType", ""), web_url=item.get("webViewLink", ""))
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
    return {"items": moved}


def copy_items(paths: list[str], destination: str) -> dict[str, Any]:
    drive = service()
    dest_id = path_to_id(destination)
    copied: list[str] = []
    for path in paths:
        file_id = path_to_id(path)
        try:
            original = drive.files().get(fileId=file_id, fields="name,mimeType", supportsAllDrives=True).execute()
            if original.get("mimeType") == "application/vnd.google-apps.folder":
                raise GoogleDriveError("Copiar pastas do Google Drive ainda nao e suportado pela API do app.")
            new_item = drive.files().copy(
                fileId=file_id,
                body={"name": original.get("name"), "parents": [dest_id]},
                fields="id,name,mimeType,webViewLink",
                supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            raise GoogleDriveError(f"Sem permissao ou falha ao copiar item do Drive: {exc}") from exc
        copied.append(id_to_path(new_item["id"]))
    return {"items": copied}
