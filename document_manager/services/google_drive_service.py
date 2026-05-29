"""Google Drive integration helpers."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from urllib.parse import quote, unquote

from flask import current_app, session
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
DRIVE_PREFIX = "gdrive://"
ROOT_PATH = "gdrive://root"


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
    # Permite testar localmente com http://localhost. Em producao no Render sera HTTPS.
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
    # Algumas versões do Google OAuth usam PKCE automaticamente.
    # Como o callback cria um novo Flow, precisamos guardar o code_verifier
    # gerado no primeiro passo para trocar o code pelo token depois.
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
    file_id = unquote(path[len(DRIVE_PREFIX):])
    return file_id or "root"


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


def _item_to_dict(item: dict[str, Any]) -> dict[str, Any]:
    is_folder = item.get("mimeType") == "application/vnd.google-apps.folder"
    path = id_to_path(item["id"])
    return {
        "name": item.get("name", "Sem nome"),
        "path": path,
        "type": "directory" if is_folder else "file",
        "size": "--" if is_folder else _format_size(item.get("size")),
        "modified": _format_modified(item.get("modifiedTime")),
        "validity": "Nao definido",
        "validity_type": "not_defined",
        "status": {"key": "not_defined", "label": "Nao definido", "icon": "?", "color": "secondary"},
        "icon": "bi bi-folder-fill" if is_folder else "bi bi-file-earmark-text",
        "drive_id": item.get("id"),
        "mime_type": item.get("mimeType"),
        "web_url": item.get("webViewLink"),
    }


def _breadcrumbs_for(file_id: str) -> list[dict[str, str]]:
    breadcrumbs = [{"label": "Google Drive", "path": ROOT_PATH}]
    if file_id == "root":
        return breadcrumbs
    try:
        drive = service()
        current = drive.files().get(
            fileId=file_id,
            fields="id,name,parents",
            supportsAllDrives=True,
        ).execute()
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
            current = drive.files().get(
                fileId=parent_id,
                fields="id,name,parents",
                supportsAllDrives=True,
            ).execute()
            guard += 1
        breadcrumbs.extend(reversed(chain))
    except HttpError:
        breadcrumbs.append({"label": file_id, "path": id_to_path(file_id)})
    return breadcrumbs


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
            fields=(
                "nextPageToken, files(id,name,mimeType,size,modifiedTime,webViewLink,parents)"
            ),
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

    items = [_item_to_dict(item) for item in response.get("files", [])]
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
            fields="id,name,mimeType,size,modifiedTime,webViewLink,parents",
            supportsAllDrives=True,
        ).execute()
    except HttpError as exc:
        raise GoogleDriveError(f"Erro ao abrir detalhes do Google Drive: {exc}") from exc
    data = _item_to_dict(item)
    data.update({
        "validity_type": "not_defined",
        "warning_days": 15,
        "notes": "",
        "web_url": item.get("webViewLink"),
    })
    return data
